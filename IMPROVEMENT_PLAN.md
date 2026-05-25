# Nomad-Agent — Pipeline Quality & Architecture Improvement Plan

> **Status:** Proposed (planning). Implementation happens workstream-by-workstream after approval.
> **Branch:** dedicated feature branch for this work (the blocking `_coerce_time` regression fix already landed on `main`).
> **Audience:** anyone who needs to understand *why* we're changing the pipeline and *what each change buys us*.
> **Companion doc:** [`SYSTEM_DESIGN.md`](./SYSTEM_DESIGN.md) — the architecture-level, "how it's built" view. This doc is the *why/what*; that one is the *how*.

---

## 1. Why this document exists

Nomad's agent pipeline already *works* structurally — signals → 4 parallel research agents → merge → synthesizer → Supabase. The plumbing is sound. **The gap is output quality.** Our last full benchmark (Sprint 7, Rajasthan, Dec 20–31) scored the generated itinerary at **~4/10** against a handmade reference, with these headline failures:

- **73% of stops were `source="maps"` filler** ("Old Jaipur Market Walk", "Lunch in Jaipur — swap for a specific spot you've saved").
- **Geographically incoherent route** — Jaipur → Jodhpur → Jaisalmer → **Udaipur** backtracks ~490 km east. No distance or drive-time reasoning exists anywhere.
- **Irrelevant, season-wrong negativity surfaced** — Day 1 literally read *"the summer heat wave can trigger kidney stones"* on a **December** (winter) trip.
- **Anchors present but hollow** — famous sites appear as bare stubs ("Pre-validated anchor stop. Check local advisories").

The goal is **not** to match a flagship model like GPT‑5.5 — we explicitly cannot afford premium LLM APIs (see §3). The goal is to reach **~90% of that quality on free-tier infrastructure** through better context, better prompts, caching, and a thin geographic-reasoning layer.

> **Important baseline note:** the 4/10 number is our *real* baseline. A regression committed shortly before this plan (`_coerce_time` lost its parentheses in `synthesizer.py`) made the synthesizer crash on *every* trip with ≥3 research candidates, dropping us *below* that baseline. That one-character fix has already shipped to `main` and is verified by the existing test suite (30/30 synthesizer tests green). This plan is about climbing *above* 4/10.

---

## 2. The core problems (symptom → root cause → evidence)

| # | Symptom (what the user sees) | Root cause (what's actually wrong) | Evidence |
|---|---|---|---|
| P1 | Itinerary feels generic; lots of filler stops | Anchors are seeded as `source="maps"` *stubs* with a templated body; nothing gives them real content | `merge_node` anchor seeding (`pipeline.py`), benchmark §3.4 (73% maps) |
| P2 | Places are picked with no sense of geography, distance, or time | **There is no geographic reasoning layer at all** — no distances, no routing, no per-stop durations, no per-city day allocation | benchmark §3.1/§6.1; no geo code anywhere in `app/` |
| P3 | Irrelevant / negative Reddit content surfaces (health scares, corruption, season-wrong warnings) | Reddit filters only catch *vague* phrasing + *off-topic* posts — not health-scare/politics; the `warning` category is **force-promoted into Day 1** regardless of relevance; insights aren't season-gated | `reddit.py` filters, synth RULE 8, benchmark "kidney stones" on a Dec trip |
| P4 | Personalization is weak — vibes and the user's own words barely show | **The personalization layer is half-wired.** `signals.query_modifiers`, `signals.vibe_source_weights`, and `trip_params.preferences` (the user's free-text!) are computed/collected but **never read by any agent or the synthesizer** | grep of `app/`: these only appear where they're *written* |
| P5 | Every request is slow and re-does the same work | **No caching.** Every request hits YouTube + Reddit + Tavily and fires ~13–15 LLM calls, even for a destination we just researched | no Redis/cache anywhere; `pyproject.toml` |
| P6 | (Latent) quality cap from the model | Synthesizer runs on Groq `llama-3.3-70b`, but a **free Cerebras key for a 235B model is already configured and unused** | `.env` vs `.env.example`; `factory.py` supports `cerebras` |
| P7 | (Latent) prompts are hard to iterate | Every prompt is a giant inline Python string; tweaking one is a code change + redeploy, with no versioning, reuse, or A/B path | `_SYNTH_SYSTEM` (~130 lines), `_PASS1/2_SYSTEM`, `_REDDIT_SYSTEM`, `_BLOG_SYSTEM` |
| P8 | (Latent) correctness bugs | `write_itinerary` isn't idempotent (re-runs duplicate rows); festivals leak into the wrong city/season (Pushkar Camel Fair placed in Jaisalmer in December) | `supabase_writer.py`, benchmark §4 |

---

## 3. Guiding principles & constraints

These shape every decision below:

1. **No premium LLM spend.** Free tiers only — **Groq** (~100k tokens/day) and **Cerebras** (1M tokens/day free, serves `qwen-3-235b`). No Anthropic/OpenAI/Gemini billing. Quality must come from context, prompts, caching, and routing — not from paying for a bigger model.
2. **Graceful degradation stays sacred.** Every research agent already returns `[]` on failure and the synthesizer always emits *something*. No new component may break that contract.
3. **The Node ⇄ Python contract is fixed.** This service owns research + synthesis only. Schema field names (`AIStop`, `AIDay`, `discoveries`) and the Supabase column contract are not negotiable without a coordinated Node change.
4. **Deterministic before LLM.** Anything that can be computed without a model (season, distances, sunrise times) should be — it's free, fast, and reliable.
5. **Cache the reusable, personalize the specific.** Destination *knowledge* is shared and slow to gather; trip *personalization* is per-request and fast. Separating these is the central architectural idea (see `SYSTEM_DESIGN.md`).

---

## 4. Workstreams

Each workstream states the problem it solves, why it matters, what changes, and how we'll know it worked. They're ordered roughly by value-per-effort, not strict dependency.

### WS0 — Stop the bleeding *(mostly done)*
- **Problem:** the `_coerce_time` regression crashes every real synthesis (P0); no CI means it wasn't caught.
- **What changes:** ✅ regression fixed on `main`. ▶ Add a minimal CI gate: `uv run pytest` + `uv run ruff check app` on every PR.
- **Success:** red tests block merge; the class of "obvious break shipped without running tests" cannot recur.
- **Effort:** XS. **Risk:** none.

### WS1 — Wire the dormant personalization layer *(solves P4)*
- **Problem:** `preferences`, `query_modifiers`, and `vibe_source_weights` are computed but ignored. The user's own typed intent is dropped on the floor.
- **Why:** this is the cheapest possible quality lever — the data already exists, it's just not plumbed through.
- **What changes:**
  - Pass `trip_params.preferences` into the synthesizer prompt **and** into research query construction.
  - Feed `signals.query_modifiers` into each research agent's query builder (today they re-derive from `vibes[0]` only).
  - Actually *apply* `vibe_source_weights` in the synthesizer (bias which source dominates stop selection) — or delete it if we decide vector retrieval (WS6) supersedes it.
- **Success:** a trip whose `preferences` says "we love markets and street food, hate touristy buffets" visibly shifts the itinerary; weights measurably change source mix.
- **Effort:** S. **Risk:** low.

### WS2 — Externalize prompts as agent "skill" files *(solves P7; enables P1/P2/P3 prompt work)* — ✅ CORE SHIPPED (Milestone B)
**Shipped:** `app/skills/loader.py` (frontmatter + `@include` + caching + `render_skill`); all 6 agent prompts moved **byte-identically** to `app/skills/*.md` (verified by a one-time round-trip migration) and loaded via `load_skill(...)`; 11 loader unit tests incl. `@include` resolution. The prompt-contract tests now guard the markdown-backed constants.
**Next (content iteration, now unblocked):** extract shared blocks into `app/skills/_shared/*.md` (one banned-words list instead of per-agent copies) and add `app/skills/regions/*.md` overlays — done as a deliberate pass with a benchmark check, since it changes prompt *content* (the verbatim move deliberately did not).
- **Problem:** prompts are buried in code as megastrings; iterating means editing Python, and the banned-words list is copy-pasted across four agents.
- **Why:** this is the technique from your work — markdown "skills" that an agent loads as context. It decouples prompt engineering from code, makes prompts diffable/versionable/A/B-testable, and lets shared blocks (voice rules, banned words, output schema) live in one place.
- **What changes:**
  - New `app/skills/` directory of markdown files: `synthesizer.md`, `reddit_research.md`, `youtube_extraction.md`, `blog_research.md`, plus `_shared/voice.md`, `_shared/banned_words.md`, `_shared/output_schema.md`.
  - A small `SkillLoader` (load + cache + `{placeholder}` substitution + frontmatter metadata) with **progressive disclosure** — an agent loads only its skill + referenced shared blocks.
  - Optional overlays: `skills/regions/india.md` ("Rajasthan = multi-city; group cities; mind drive times") layered in when the region matches.
- **Success:** changing the synthesizer's voice or banned-words list is a markdown edit, not a code change; no duplication across agents.
- **Effort:** M. **Risk:** low (behavior-preserving refactor first, then iterate content).
- **Note:** these are *runtime* skills for the Nomad agents — distinct from the `.claude/skills/*.md` dev-time skills already in the repo.

### WS3 — Reddit relevance & season gating *(solves P3)*
- **Problem:** irrelevant negativity and season-wrong warnings reach the itinerary, amplified by RULE 8 force-surfacing a "warning" into Day 1.
- **What changes:**
  - Extraction prompt (now a skill file) explicitly **excludes** health-scare/illness-anxiety, corruption/politics, and generic "is it safe" hand-wringing — keep only *actionable, place-specific* tips.
  - Add a denylist + a relevance check; **season-gate** warnings (don't surface a summer-heat warning on a winter trip).
  - Change synth RULE 8 to surface only the **deterministic `signals.warnings`** (which are season-correct) on Day 1 — not arbitrary Reddit "warnings".
- **Success:** the "kidney stones in December" class of output disappears; warnings that *do* appear are season-correct and actionable.
- **Effort:** S–M. **Risk:** low.

### WS4 — Real anchors + go-to/hidden-gem balance *(solves P1)*
- **Problem:** famous must-see places appear as hollow `maps` stubs; meanwhile crowd logic still injects "hidden gems / off-the-beaten-path" modifiers.
- **What changes:**
  - The anchor-enrichment LLM call returns a **one-line real description** per anchor (not just a name), so seeded anchors carry content.
  - Prefer research-sourced content for an anchor when it exists; the stub is only a fallback.
  - Re-balance query modifiers so anchors and hidden gems coexist (dial back automatic "off-the-beaten-path" injection).
- **Success:** Hawa Mahal/City Palace/etc. show specific descriptions, not "check local advisories"; maps-stub share drops well below 40%.
- **Effort:** S–M. **Risk:** low. *(Largely overlaps with WS6's richer cached pool.)*

### WS5 — Model routing to free Cerebras-235B *(solves P6)*
- **Problem:** synthesis quality is capped by a 70B model when a far stronger free model is available.
- **What changes:**
  - Move the synthesizer to Cerebras `qwen-3-235b-a22b-instruct` via env.
  - Add a **fallback** in the factory: if the primary provider errors/queues, fall back to Groq 70B so we never lose graceful degradation.
  - Consider Cerebras for the heavy extraction passes too, watching the 1M/day budget.
- **Success:** measurably richer synthesis narratives at $0 additional cost; no increase in hard failures thanks to fallback.
- **Effort:** S. **Risk:** low–medium (free-tier queueing — mitigated by fallback).

### WS6 — Redis context & caching layer *(solves P5; the "Redis Iris / agent memory" ask)* — ✅ L1 SHIPPED; L2/L3 deferred
**Shipped:** **L1** destination research cache + geocode cache (`app/cache.py`, `research_gate` node) with graceful degradation when `REDIS_URL` is unset. A repeat-destination HIT skips all 4 research agents (~13 LLM + ~50 API calls) and relieves the Groq cap.
**Deferred (with full rationale in [`SYSTEM_DESIGN.md`](./SYSTEM_DESIGN.md) §5.6/§5.7):** **L2** semantic retrieval (only pays off once pools are large / research goes vibe-neutral) and **L3** long-term user memory (needs real returning users + a cross-service ownership decision). Triggers to build each are documented there.
- **Problem:** no caching; every request re-pays the full research cost; no cross-trip memory of a user's tastes.
- **Why:** destination research is the expensive, *reusable* artifact. Caching it per-destination turns a ~13–15-LLM-call, ~60s cold request into a ~1-call, few-second warm request — and unlocks per-user memory.
- **What changes (layered — see `SYSTEM_DESIGN.md` §5 for the full design and the options analysis):**
  - **L1 — Destination Research Cache (exact-key).** Cache the merged, *vibe-neutral* discovery pool per normalized destination. On a hit, skip all four research agents; the synthesizer personalizes from the cached pool.
  - **L2 — Semantic Discovery Retrieval (RedisVL vector, local embeddings).** Embed discoveries; retrieve the top-K most relevant to this trip's vibes/preferences, always unioned with anchor-tagged ones. Personalizes selection from a shared pool and balances go-to vs niche.
  - **L3 — Long-term user memory.** A compact per-user preference profile updated from each trip and folded into future personalization — the "agentic memory" concept, built on OSS Redis rather than the paid Iris product.
  - **Geocode cache** (supports WS7) lives here too.
- **Success:** warm-path latency and LLM-call count drop ~10×; repeat destinations don't re-hit external APIs; a returning user's known tastes shape new trips.
- **Effort:** L. **Risk:** medium (new dependency, cache-invalidation discipline).

### WS7 — Geographic reasoning layer *(solves P2 — the single biggest quality lever)*
- **Problem:** no notion of where places are, how far apart, what order to visit, or how long things take.
- **What changes:**
  - A pre-synthesis **routing step**: geocode cities/areas via **free OpenStreetMap Nominatim** (cached in Redis), compute a sensible visit order (nearest-neighbor) + a pairwise distance/drive-time matrix.
  - Feed the synthesizer a **hard "visit cities in this order, don't backtrack" rule** + distance hints + per-city day allocation.
  - Inject **deterministic sunrise/sunset times** (city × month) for real time-of-day hooks.
  - *Later:* OSM **Overpass** for POI lat/lng → intra-day clustering ("X is 2 km from Y").
- **Success:** routes stop backtracking; days cluster geographically; time hooks ("Nahargarh sunrise ~7:14 AM") appear; benchmark route-logic score climbs from 2/10.
- **Effort:** L. **Risk:** medium (Nominatim rate limits — mitigated by aggressive caching).

### WS8 — Correctness hardening *(solves P8)*
- **What changes:** make `write_itinerary` idempotent (delete existing days/stops for the trip before insert); add a festival guard (only surface a festival if it's in the right city *and* active in the trip window); add multi-city day-allocation (ties into WS7).
- **Success:** re-running a trip doesn't duplicate rows; no out-of-city/out-of-season festival stops.
- **Effort:** S–M. **Risk:** low.

---

## 5. Sequencing & milestones

```
Milestone A — "Honest baseline restored + cheap quality"  (WS0 ✅, WS1, WS3, WS5)
   → re-benchmark Rajasthan; expect maps-share down, negativity gone, richer synthesis.

Milestone B — "Prompts as skills"  (WS2, then move WS1/WS3/WS4 prompt content into skills)
   → prompt iteration no longer requires code changes.

Milestone C — "Context engine"  (WS6 L1 → L2 → L3)
   → warm requests ~10× cheaper/faster; personalization from a shared pool; user memory.

Milestone D — "Geographic reasoning"  (WS7, WS8)
   → coherent routes, time hooks, idempotent writes, festival correctness.
```

WS2 (skills) is deliberately early because every later prompt change (WS3, WS4, WS7) is cleaner once prompts live in markdown.

---

## 6. How we measure success

Re-run the Rajasthan benchmark (and add Goa + a Europe city) after each milestone and track:

| Metric | Baseline (Sprint 7) | Target |
|---|---|---|
| `source="maps"` share of stops | 73% | < 35% |
| Non-maps stops with a *named entity* in the description | low | > 70% |
| Route geographic coherence (no backtracking) | 2/10 | ≥ 7/10 |
| Irrelevant/season-wrong warnings | present | 0 |
| Warm-path LLM calls per request (cached destination) | ~13–15 | 1–2 |
| Warm-path latency (cached destination) | ~60s | < 8s |
| Personalization visibly reflects `preferences` text | no | yes |

---

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Cerebras free tier queues at peak | Factory fallback to Groq 70B; never a hard failure |
| Stale cached research (closed venues) | TTL (≈45d) + schema version prefix + manual bust; freshness already filtered at agent level |
| Nominatim rate limits | Cache every geocode in Redis (near-permanent); batch + backoff |
| Cache returns research biased to the first requester's vibes | L1 research is gathered with **vibe-neutral** queries; personalization happens at synthesis/retrieval, not research |
| Prompt-as-skills refactor changes behavior | Refactor is behavior-preserving first (byte-identical prompts), then iterate content under tests |
| Redis adds an ops dependency | Local Redis for dev; free managed tier (e.g. Redis Cloud free) for prod; degrade gracefully to "no cache" if Redis is down |

---

## 8. Decisions (resolved)

1. **Skill files** = ✅ **runtime agent prompts** — externalize the Nomad agents' prompt megastrings into `app/skills/*.md` (WS2).
2. **Redis hosting** = ✅ **local for dev + free managed tier for prod; degrade to no-cache if Redis is down** (cache is an optimization, never a hard dependency).
3. **Embeddings for L2** = ⏳ **decide at Milestone C** (doesn't block earlier milestones).
4. **User memory (L3) ownership** = ⏳ open; revisit at Milestone C (likely Redis keyed by `user_id`, coordinate with `nomad-api`).

### Reconciliation note (WS1 ⇄ WS6)
To keep research **cacheable and reusable** (WS6/Milestone C wants a *vibe-neutral, destination-only* pool), personalization is applied at the **synthesis** step, not by biasing research queries. So WS1 wires `preferences`, `vibe_source_weights`, and `query_modifiers` into the **synthesizer** (the decision point) now; research-query shaping stays broad and is revisited under WS6.
