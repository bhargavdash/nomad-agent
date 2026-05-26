# Nomad-Agent — System Design

> A system-design-interview-style walkthrough: requirements → current architecture → bottleneck analysis → target architecture → deep-dives (Redis context engine, geo-routing, prompt/skill management, model routing) → data flows → trade-offs → scaling.
> **Companion:** [`IMPROVEMENT_PLAN.md`](./IMPROVEMENT_PLAN.md) (the *why/what* and the workstream roadmap). This doc is the *how*.

---

## 1. Problem framing & requirements

**The product:** given a trip request (destination, dates, party size, vibes, budget, pace, free-text preferences), produce a coherent **day-by-day itinerary** grounded in real, current traveler content — not generic guidebook boilerplate.

**This service's slice:** Nomad is polyglot. A Node service (`nomad-api`) owns auth, CRUD, and the polling the frontend reads. **This Python service owns only the agentic research + synthesis.** Both share one Supabase Postgres DB; this service never calls Node back — all cross-service communication is via the DB.

### Functional requirements
- Accept a trip → return `202` immediately → run the pipeline as a background task → write results to Supabase for Node to poll.
- Gather destination content from multiple sources (YouTube short + long form, Reddit, travel blogs).
- Synthesize a personalized, chronologically-ordered, geographically-sane itinerary.
- Always produce *something* valid, even under partial failure (graceful degradation).

### Non-functional requirements & constraints
| Dimension | Requirement |
|---|---|
| **Cost** | **Free-tier LLMs only** (Groq ~100k tok/day; Cerebras 1M tok/day). No paid model APIs. This is the dominant constraint. |
| **Latency** | Cold (new destination) acceptable up to ~60–90s (it's async + a progress UI). Warm (known destination) should be a few seconds. |
| **Reliability** | One agent failing must not fail the trip; the synthesizer failing must still persist a skeleton. |
| **Quality** | Reach ~90% of a flagship model's itinerary quality via context/prompts/caching, not model spend. |
| **Scale (assumed)** | Indie scale: tens–hundreds of trips/day, a **long tail of destinations with a hot head** (Goa, Rajasthan, Bali, Paris repeat constantly). This shape is *why caching pays off*. |
| **Contract** | Pydantic schemas mirror Node's Zod; Supabase column names are snake_case and fixed. |

The scale shape is the key insight: **few distinct popular destinations, requested repeatedly with different personalization.** That's a textbook cache-friendly workload — *if* we separate destination knowledge from per-trip personalization.

---

## 2. Current architecture (as-is)

```
            POST /agent/research  (Bearer INTERNAL_AGENT_SECRET)
                     │  202 Accepted immediately
                     ▼
            BackgroundTask: _run_and_persist
                     │            │
        progress pacer            └── run_pipeline (LangGraph)
        (fake phase writes)                │
                                           ▼
                                   ┌─────────────┐
                                   │   signals   │  pure Python (season, crowd,
                                   │ (+1 LLM if  │  festivals, warnings, weights,
                                   │  region unk)│  anchors via 1 cached LLM call)
                                   └──────┬──────┘
              ┌───────────────┬──────────┼───────────────┬───────────────┐
              ▼               ▼          ▼               ▼               
      ┌──────────────┐ ┌────────────┐ ┌────────┐ ┌──────────────┐
      │ youtube       │ │ youtube     │ │ reddit │ │ google_blog  │   4 agents,
      │ shorts        │ │ longform    │ │        │ │ (Tavily)     │   PARALLEL
      │ (2-pass LLM)  │ │ (2-pass LLM)│ │(1-pass)│ │ (2-pass LLM) │
      └──────┬───────┘ └─────┬──────┘ └───┬────┘ └──────┬───────┘
             └───────────────┴───────┬────┴─────────────┘
                                     ▼
                              ┌────────────┐
                              │   merge     │  concat + seed anchor stubs +
                              │             │  mid-flight discoveries write
                              └─────┬──────┘
                                    ▼
                              ┌────────────┐
                              │ synthesizer │  1–2 LLM calls → AIItinerary
                              └─────┬──────┘
                                    ▼
                          write itinerary + stats → Supabase
```

**Where the work (and cost) lives — per cold request:**

| Stage | External API calls | LLM calls | Notes |
|---|---|---|---|
| signals | 0 | 0–1 (region classify, cached) + 1 (anchors, cached/dest) | deterministic + tiny LLM |
| youtube shorts | ~5 search + ~5 details | ~3 (pass-1 batches) + 1 (pass-2) | |
| youtube longform | ~4 search + ~4 details | ~4 (pass-1) + 1 (pass-2) | transcript-gated |
| reddit | up to ~18 search + ~10 comments, **sequential with 1s sleeps** | 1 | **latency hog (~28s of sleeps), frequent 403s** |
| google_blog | ~4 Tavily | 2 (anchor + main) | |
| synthesizer | 0 | 1–2 | |
| **Total** | **~50 HTTP + Tavily/Reddit** | **~13–15 LLM calls** | on Groq 70B w/ ~100k tok/day cap |

**Implication:** a handful of cold trips can exhaust the Groq daily token cap, and Reddit's sequential sleeps dominate wall-clock. Every one of these is **re-paid on every request**, even for a destination we researched an hour ago.

---

## 3. Bottleneck & failure analysis

### 3.1 Cost / latency
- **No caching** → 100% of requests are "cold". The reusable artifact (what's worth seeing in Goa) is recomputed every time. *This is the #1 efficiency defect.*
- **Reddit is serial** (rate-limit-driven 1s sleeps for search *and* comment enrichment) → tens of seconds, and it 403s often, so we frequently pay the latency for little yield.

### 3.2 Quality leaks
- **Personalization not wired (defect, not a missing feature):** `signals.query_modifiers`, `signals.vibe_source_weights`, and `trip_params.preferences` are produced/collected but **never consumed** downstream. The personalization layer computes signals into a void.
- **No geographic reasoning:** the synthesizer freeforms `duration_days` with zero distance/order/duration awareness → backtracking routes, no time hooks, no per-city allocation.
- **Anchors are hollow:** seeded as `source="maps"` stubs → famous places read as filler; inflates the "maps %" metric.
- **Reddit negativity amplified:** weak relevance filtering + a hard rule that promotes a "warning" into Day 1 → season-wrong, irrelevant content surfaces prominently.

### 3.3 Correctness / ops
- `write_itinerary` is **not idempotent** (insert-only) → re-runs duplicate rows.
- Festival placement isn't guarded by city/season → events leak to the wrong place/time.
- **No CI** → a one-character regression (`_coerce_time`) shipped and broke all synthesis; existing tests would have caught it.

---

## 4. Target architecture (to-be)

**Central idea: split "destination knowledge" from "trip personalization".**

```
                         POST /agent/research
                                 │ 202
                                 ▼
                         _run_and_persist (bg)
                                 │
                                 ▼
                          ┌──────────────┐
                          │   signals     │  (season/festival/crowd + warnings)
                          └──────┬───────┘
                                 ▼
                   ┌──────────────────────────────┐
                   │  RESEARCH (cacheable, shared)  │
                   │                                │
                   │   L1 cache hit? ──yes──────────┼──► skip agents, load pool
                   │        │ no                     │
                   │        ▼                        │
                   │  4 agents (VIBE-NEUTRAL queries)│  ← gather a broad pool
                   │        ▼                        │
                   │  write L1 cache (TTL) + L2 index│
                   └───────────────┬────────────────┘
                                   ▼
                   ┌──────────────────────────────┐
                   │  PERSONALIZATION (per-request) │
                   │  • L3 user-memory fold-in      │
                   │  • L2 retrieve top-K by         │
                   │    vibes+preferences (+anchors) │
                   │  • GEO routing: order cities,   │
                   │    distances, sunrise/sunset    │
                   │  • SYNTHESIZER (skill-driven    │
                   │    prompt, Cerebras-235B)       │
                   └───────────────┬────────────────┘
                                   ▼
                       idempotent write → Supabase
```

New/changed components:
- **Context engine (Redis):** L1 destination research cache, L2 vector retrieval over discoveries, L3 long-term user memory, plus a geocode cache. (§5)
- **Geo-routing service:** geocode → order → distances → sunrise/sunset, fed to the synthesizer. (§6)
- **Skill/prompt subsystem:** prompts as versioned markdown loaded at runtime. (§7)
- **Model router:** Cerebras-235B primary, Groq-70B fallback. (§8)
- **Research decoupling:** research agents run **vibe-neutral, broad** queries so the cached pool is reusable across all personalizations; selection/weighting moves to personalization.

---

## 5. Deep-dive: the Redis context engine (the "Iris / agent memory" question)

### 5.1 What "Redis Iris" is, and what we actually adopt
Redis **Iris** (announced May 2026) is an enterprise **Context Engine** = Context Retriever + Agent Memory (short/long term) + Data Integration (CDC). It's a commercial, enterprise-positioned product. We **adopt the pattern, not the product** — the same capabilities are available on **OSS Redis** via **RedisVL** (vector + semantic memory abstractions), the open-source **Redis Agent Memory Server**, and **LangCache** (semantic response cache). This honors the no-premium constraint.

### 5.2 Options considered

| Option | What it gives | Fit for Nomad | Verdict |
|---|---|---|---|
| **Exact-key KV/JSON cache** (Redis strings / RedisJSON) | Store the merged discovery pool per destination; O(1) lookup | Perfect for the "same destination, different personalization" workload; trivial to reason about | ✅ **L1 — adopt now** |
| **Vector retrieval** (RedisVL / Redis vector sets) | Embed discoveries; semantic top-K by vibe/preference | Personalizes *selection* from a shared pool; balances go-to vs niche; shrinks synth prompt | ✅ **L2 — adopt next** (needs an embedder) |
| **Long-term agent memory** (Agent Memory Server pattern) | Per-user durable preferences, semantically recalled | Cross-trip personalization continuity — the real differentiator | ✅ **L3 — adopt later** |
| **Semantic response cache** (LangCache) | Cache whole LLM outputs by input similarity | Risky for itineraries (date/personalization-specific) → stale results | ⚠️ Defer / optional |
| **LangGraph Redis checkpointer** | Resume in-flight graph runs | Pipeline is short + fire-and-forget; low marginal value | ⚠️ Optional |
| **Dedicated vector DB** (Pinecone/Weaviate/Chroma) | Standalone vector store | We already need Redis for L1/L3; pools are tiny (≤~50/dest); a second system is overhead | ❌ Reject (consolidate on Redis) |
| **Redis Iris (product)** | Turnkey enterprise context engine | Enterprise/commercial; overkill; violates cost constraint | ❌ Reject (adopt the *pattern* on OSS) |

**Embeddings for L2:** use a **local model** (e.g. `fastembed`/MiniLM, ONNX, ~tens of MB, runs in-process) → **zero API cost**, fast, honors the constraint. A free embedding API is the fallback. Because per-destination pools are small (≤~50 discoveries), even brute-force cosine in-process is viable; RedisVL buys persistence + reuse of embeddings across requests and a clean upgrade path as pools grow.

### 5.3 Data model

```
# L1 — destination research cache (RedisJSON or string of JSON)
KEY   nomad:research:v1:{slug(destination)}            # slug = lowercased, trimmed, punctuation-normalized
VALUE { "discoveries": [ResearchDiscovery, ...],       # the vibe-neutral pool
        "meta": { "fetched_at": iso, "agent_versions": {...},
                  "source_counts": {youtube, reddit, blog, anchor} } }
TTL   ~45 days        # travel content ages; bound staleness
BUST  bump v1→v2 on schema/agent change; manual purge endpoint

# L2 — discovery vector index (RedisVL)
INDEX nomad:disc:v1                                     # HNSW, cosine
DOC   nomad:disc:v1:{discovery_id}
      fields: destination_slug, source, tags[], is_anchor(bool),
              text(title+body), vector(embedding)
QUERY embed(vibes + preferences + query_modifiers) → top-K filtered by destination_slug,
      UNION all is_anchor=true for that destination   # guarantee go-to coverage

# L3 — long-term user memory
KEY   nomad:user:{user_id}:profile  (RedisJSON)         # compact, structured
VALUE { budget_lean, pace_lean, dietary, dislikes[],
        loved_places[], recurring_vibes[], updated_at }
(optional) nomad:user:{user_id}:mem  → RedisVL vector memories for semantic recall
WRITE async after each trip: extract durable prefs from preferences + chosen stops

# Geocode cache (supports §6)
KEY   nomad:geo:v1:{slug(place)}  →  { lat, lng, display_name }
TTL   ~permanent (geography doesn't move)
```

### 5.4 Invalidation & safety
- **Versioned key prefixes** (`v1`) so a schema or agent-logic change can invalidate cleanly by bump.
- **TTL** bounds staleness without manual work.
- **Vibe-neutral L1:** research queries for the cache should not heavily bake in the first requester's vibes, or the pool is biased. Personalization is strictly a synthesis (and, later, L2) concern.
- **Degrade gracefully:** if Redis is unreachable, the pipeline runs cold (today's behavior) — caching is an optimization, never a hard dependency.

### 5.5 Implementation status

- **L1 (destination research cache) — ✅ SHIPPED.** `app/cache.py` + a `research_gate` node. On a hit, the 4 research agents no-op and the synthesizer personalizes from the cached pool. Geocode cache (§6) also lives here. Graceful no-op when `REDIS_URL` is unset.
- **L2 (vector retrieval) — ⏸ deferred.** Analysis below.
- **L3 (long-term user memory) — ⏸ deferred.** Analysis below.

> Known L1 limitation to revisit: one of ~5 research queries per agent is vibe-shaped, so the cached pool is *mostly* destination-neutral but slightly tinted toward whoever populated it. The synthesizer re-personalizes selection from the pool, so it's acceptable for now. The clean fix (make research fully vibe-neutral so the cache is purely "destination knowledge") pairs naturally with L2.

### 5.6 L2 — semantic discovery retrieval (deferred): full analysis

**What it is.** Embed every cached discovery (title + body) into a vector and store it in a **RedisVL** index. At synthesis time, embed the trip's intent (`vibes + preferences + query_modifiers`), retrieve the **top-K most similar** discoveries for that destination, and always union in the `anchor`-tagged ones (so famous must-sees can't be filtered out). Feed that focused subset to the synthesizer instead of the whole pool.

**Value it would bring.**
- **Personalize selection from a shared pool.** This is the missing half of the L1 "research once, personalize many" story: L1 makes the pool reusable; L2 makes each user's *slice* of it relevant. A foodie and a history buff hitting the same cached Goa pool would get different top-K subsets.
- **Scales the pool.** It lets the cached pool grow large (hundreds of discoveries per hot destination, accumulated across requests) without bloating the synth prompt — you retrieve the best ~15, not all 300. This also unlocks making research fully vibe-neutral and *broad* (gather everything once), since retrieval handles relevance.
- **Cheaper, tighter synth prompts.** Fewer, more-relevant candidates → shorter prompt → less token cost and less dilution.
- **Cross-trip reuse of embeddings.** Embed once on cache-write; reuse on every read.

**Why it's deferred (the case against, for now).**
- **The synthesizer already handles the whole pool.** Today a destination's pool is ≤ ~30–50 discoveries — well within what the synth reads comfortably. Retrieval's marginal benefit is ~zero until pools are large, which only happens *after* we switch to broad vibe-neutral research (not done).
- **It adds a dependency + moving parts.** A local embedding model (`fastembed`/MiniLM ONNX, ~tens of MB) or a free embedding API, plus a RedisVL index to create/maintain/version. More to operate and test.
- **Correctness risk on a thin pool.** With small pools, top-K retrieval can *drop* a relevant item that a full-pool LLM would have used; you'd be adding a lossy filter in front of a model that didn't need one.

**Cost / complexity.** Medium. New embedding dependency, an index lifecycle (build on cache-write, query on read, re-index on `CACHE_VERSION` bump), and tuning K + the anchor-union rule. ~zero ongoing $ with local embeddings.

**The trigger to build it.** When **either** (a) we move to broad vibe-neutral research and pools routinely exceed ~60–80 discoveries, **or** (b) synth prompt size / token cost on hot destinations becomes a measured problem, **or** (c) personalization quality from a shared pool is visibly weak (different users getting too-similar itineraries). Until one of those is true, L2 is speculative.

**Integration sketch.** On cache-write, embed each discovery → `HSET` into `nomad:disc:{ver}` (RedisVL schema in §5.3). In the synthesizer path, replace "pass the whole pool" with "embed intent → RedisVL KNN query filtered by `destination_slug`, K≈15, UNION `is_anchor=true`." Everything else (the synth prompt, overlays, geo) is unchanged.

### 5.7 L3 — long-term per-user memory (deferred): full analysis

**What it is.** A compact, durable per-user **preference profile** keyed by `user_id` (e.g. budget lean, pace lean, dietary needs, disliked things, loved places, recurring vibes). After each trip, asynchronously **extract** durable preferences from the user's `preferences` text + the stops they kept/locked, and merge into the profile. On a new trip, **fold the profile into personalization** (the synth prompt) so the system "remembers" them. Optionally a vector-backed memory (`nomad:user:{id}:mem`) for semantic recall of past notes.

**Value it would bring.**
- **Continuity / the "it knows me" feel.** This is the differentiator — the "agentic memory" idea behind Redis Iris. A returning user who always picks boutique stays and hates touristy buffets shouldn't have to re-say it every trip.
- **Better cold-start personalization.** Even a sparse new request gets shaped by what we already know about the user.
- **Compounding quality.** The profile sharpens with every trip, independent of the destination cache.

**Why it's deferred (the case against, for now).**
- **Needs usage to be worth it.** Long-term memory only pays off for **returning users with multiple trips**. Pre-launch / low-traffic, there's little history to remember, so the feature sits idle.
- **Cross-service ownership.** Users live in `nomad-api` (Postgres `profiles`). A preference profile arguably belongs there (or at least must be coordinated), not solely in this service's Redis — a polyglot-boundary decision (`rules/db-contract.md`).
- **Privacy + correctness.** Storing inferred user preferences raises consent/staleness questions (people's tastes change; a wrong "remembered" preference is worse than none). Needs an extraction quality bar + a way for users to see/edit/clear it.
- **Extraction is non-trivial.** Deciding *what* is a durable preference vs. a one-off ("wanted a beach this once") is its own prompt-engineering + eval task.

**Cost / complexity.** Medium–high. A profile store, an async extraction step after each trip, synth-prompt integration, plus the cross-service ownership + privacy decisions.

**The trigger to build it.** When there are **real returning users with ≥2–3 trips each**, and we've decided where the profile of record lives (Redis here vs. Postgres in `nomad-api`). Before that, it's premature.

**Integration sketch.** After `mark_trip_ready`, enqueue an async "memory update": LLM-extract durable prefs from `preferences` + kept stops → merge into `nomad:user:{id}:profile`. In `run_synthesizer`, load the profile and prepend a "What we know about this traveler" block (below the per-trip `preferences`, which always wins on conflict). Gate the whole feature behind a flag and a user-visible "memory" they can clear.

---

## 6. Deep-dive: geographic reasoning layer

A deterministic pre-synthesis step (no premium APIs):

1. **Extract** candidate cities/areas from the discovery pool + anchors.
2. **Geocode** each via **OSM Nominatim** (free; 1 req/s) → lat/lng, **cached in Redis** so we geocode any place once, ever.
3. **Order** cities by nearest-neighbor from the entry city → a non-backtracking route; compute a **pairwise distance matrix** (haversine; optionally OSRM/OpenRouteService free tier for real drive times).
4. **Allocate** days per city proportional to content depth + a sane min/max.
5. **Sunrise/sunset** per city × month from a deterministic formula (no API) for time-of-day hooks.
6. **Feed the synthesizer** (via its skill prompt): the ordered city list, distances/drive-times, day allocation, and sun times — with a **hard rule**: "visit cities in this order; cluster each day geographically; don't backtrack."

*Later (Tier 2):* OSM **Overpass** for POI lat/lng → intra-day clustering ("these three are within 2 km").

This directly attacks the benchmark's weakest dimension (route logic 2/10) and adds the time-specific hooks the handmade reference had ("Nahargarh sunrise ~7:14 AM").

---

## 7. Deep-dive: prompts as runtime "skill" files

**Today:** prompts are inline megastrings (`_SYNTH_SYSTEM` ~130 lines, `_PASS1/2_SYSTEM`, `_REDDIT_SYSTEM`, `_BLOG_SYSTEM`). The banned-words list is duplicated across four agents. Any tweak is a code change.

**Target:** an `app/skills/` tree of markdown loaded at runtime (the "skills as md" technique):

```
app/skills/
  synthesizer.md            # voice, hard rules, output schema (via includes)
  reddit_research.md
  youtube_extraction.md     # shared by shorts + longform
  blog_research.md
  geo_routing.md
  _shared/
    voice.md                # the "don't sound like an LLM" rules — one source of truth
    banned_words.md         # stunning/vibrant/must-visit/... — referenced everywhere
    output_schema.md
  regions/
    india.md                # "Rajasthan = multi-city; group cities; mind drive times"
```

A `SkillLoader`:
- loads + caches files, supports `{placeholder}` substitution and `@include _shared/voice.md`,
- reads **frontmatter** metadata (version, applies-to),
- supports **progressive disclosure** — an agent loads only its skill + referenced shared/region blocks.

**Wins:** prompt iteration without code edits; diffable/versionable/A-B-testable prompts; shared blocks deduplicated; per-region/per-vibe overlays; a clean home for the eventual synthesizer split (`pick.md` → `narrate.md`). These are *runtime* skills for Nomad's agents — orthogonal to the repo's existing `.claude/skills/*.md` dev-time skills.

---

## 8. Deep-dive: model routing

```
get_llm("synthesizer")
   → primary: cerebras / qwen-3-235b-a22b-instruct   (free 1M tok/day, ~235B)
   → on error/queue/timeout: fallback groq / llama-3.3-70b-versatile
```

- Bigger free model for the quality-critical synthesis step; **factory-level fallback** preserves graceful degradation when the free tier queues.
- Research extraction stays on Groq 70B by default (cheap, parallel-friendly); selectively promote to Cerebras while watching the 1M/day budget.
- All still routed through `app/llm/factory.py` (the one place clients are built) — no agent code changes.

---

## 9. Data-flow walkthroughs

### Cold path (new destination)
```
request → signals → L1 MISS
       → 4 agents (vibe-neutral) → merge → write L1 + build L2 index
       → L3 fold-in → L2 retrieve top-K (+anchors) → geo-route → synthesize (Cerebras)
       → idempotent write → Supabase
   cost: ~13–15 LLM calls + external APIs   latency: ~60–90s   (≈ today, plus cache write)
```

### Warm path (cached destination, different user/personalization)
```
request → signals → L1 HIT (load pool)
       → L3 fold-in → L2 retrieve top-K (+anchors) → geo-route (geocodes cached)
       → synthesize (Cerebras)
       → idempotent write → Supabase
   cost: ~1–2 LLM calls, 0 research APIs   latency: a few seconds
```

The warm path is the common case at our scale (hot head of popular destinations) → ~10× cost/latency reduction where it matters most.

---

## 10. Trade-offs & alternatives considered

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Cache granularity | **Per-destination, personalization-free** | Per-(destination+vibes+dates) | Far higher hit rate; personalization is cheap to redo at synthesis |
| Vector store | **Redis (RedisVL)** | Pinecone/Chroma/Weaviate | Already need Redis; tiny pools; one fewer system |
| Embeddings | **Local (fastembed)** | Hosted embedding API | Zero cost; honors constraint; pools are small |
| Memory product | **OSS Redis pattern** | Redis Iris (paid) | Enterprise/commercial; cost constraint |
| Geo | **Nominatim + haversine, cached** | Google Maps / Mapbox | Free; cache makes rate limits a non-issue |
| Prompts | **Markdown skill files** | Keep inline strings | Iteration speed, versioning, reuse, A/B |
| Synth model | **Cerebras-235B + Groq fallback** | Stay on Groq-70B / pay for flagship | Big free quality jump; fallback keeps reliability; no spend |
| Semantic itinerary cache | **Defer** | Adopt now | Correctness risk (date/personalization specificity) outweighs marginal savings |

---

## 11. Scaling & ops

- **Memory budget:** L1 pools are a few KB–tens of KB each; even thousands of destinations fit in a free Redis tier. L2 vectors (MiniLM, 384-dim) are small; cap per-destination doc count. Set `maxmemory` + `allkeys-lru` so the long tail evicts naturally while the hot head stays warm.
- **Free-tier limits:** Groq ~100k tok/day, Cerebras 1M tok/day, Nominatim 1 req/s, Tavily 1k/mo, YouTube 10k units/day. Caching is what keeps us under all of these as volume grows.
- **Dependency posture:** Redis down → degrade to cold path. Cerebras down → Groq fallback. Any single agent down → `[]` and continue. No new single point of failure.
- **Observability:** add cache hit/miss + per-stage latency/LLM-call counters; keep LangSmith tracing optional.

---

## 12. Build order (mirrors `IMPROVEMENT_PLAN.md` §5)

1. **CI gate + wire personalization + Reddit gating + Cerebras routing** (Milestone A) — cheap, high-impact, no new infra.
2. **Skills subsystem** (Milestone B) — refactor prompts to markdown (behavior-preserving), then iterate content.
3. **Redis context engine** L1 → L2 → L3 (Milestone C).
4. **Geo-routing + correctness hardening** (Milestone D).
