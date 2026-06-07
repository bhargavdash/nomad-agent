# Cache Architecture — Research Once, Personalise Many

## What changed and why

### Before (FI-6 design)

The cache key included `vibe_cluster` as a dimension:

```
nomad:research:{ver}:{destination}:{season}:{vibe_cluster}
```

This meant Goa-monsoon-adventure and Goa-monsoon-foodie were **separate cache entries**. Each new user with different vibes would trigger a full research run, even if another user had already researched the same destination and season. The feature was designed to give each vibe cluster its own targeted pool, but this created a cache that was never actually shared across users.

Worse: the planned **reiteration loop** (user asks "more cafes, fewer ATVs") would change their `vibe_cluster` → new cache key → full research run from scratch. That makes the cache irrelevant to the most expensive iteration path.

### After (L0 design)

The cache key is now destination × season only:

```
nomad:research:{ver}:{destination}:{season}
```

One pool per destination+season, shared by **all users regardless of their vibes**. Research agents run in **broad mode** — neutral queries covering all four vibe clusters. A new `pool_filter` step narrows the broad pool to the user's relevant subset before the synthesizer sees it.

---

## Pipeline topology after this change

```
signal_node
    │
    ├──► geo_node ─────────────────────────────────────────────────────────┐
    │                                                                       │
    └──► research_gate_node (L0 cache: destination+season)                 │
              │                                                             │
              ├── HIT: research_cache = broad pool (skip agents)           │
              │                                                             │
              └── MISS: run broad-mode research agents                     │
                    ├── youtube_node                                        │
                    ├── youtube_longform_node                               │
                    ├── reddit_node                                         │
                    └── google_blog_node                                    │
                          │                                                 │
                    merge_node ──► [writes full pool to cache + Supabase]  │
                          │                                                 │
                    pool_filter_node ◄── scores by vibe_cluster            │
                    (top ~15 from broad pool)                               │
                          │                                                 │
                    synthesizer_node ◄──────────────────────────────────────┘
                          │
                         END
```

Key invariant: `set_cached_research` is called inside `merge_node`, **before** `pool_filter_node` runs. The cache and Supabase always store the full unfiltered pool. The filter is read-side only.

---

## What broad mode means for each agent

Each agent's `_build_queries()` previously included a "first user vibe" slot (Q3 or Q4) that baked the requesting user's preference into the search queries. That slot is now replaced with fixed canonical queries:

| Agent | Old user-specific slot | New canonical replacement |
|---|---|---|
| YouTube Shorts | `"{dest} {first_vibe}"` (Q4) | `"{dest} hidden gems offbeat"` (Q4) + `"{dest} culture history"` (Q5) |
| YouTube Longform | `"{dest} {first_vibe}"` (Q3) | `"{dest} itinerary"` (Q3) + `"{dest} culture history"` (Q4) |
| Reddit | `"{dest} {first_vibe}"` (Q4) | `"{dest} things to do activities"` (Q4) |
| Google Blog | `"{dest} {first_vibe} travel tips"` (Q3) | `"{dest} travel guide itinerary"` (Q3) |
| Google Blog | `budget_tier` branching (Q4) | `"{dest} local food restaurants experiences"` (Q4, always) |

Query caps were raised to accommodate the extra canonical queries:
- YouTube Shorts: 5 → 6
- YouTube Longform: 4 → 5

---

## The pool_filter step

**File:** `app/pool_filter.py`

**When it runs:** After `merge_node`, before `synthesizer_node`. Reads `state["all_discoveries"]`, writes `state["synthesizer_pool"]`.

**What it does:** Scores each discovery in the broad pool on two dimensions and returns the top `max_items` (default 15):

1. **Cross-source agreement** (primary): how many distinct sources (youtube, reddit, blog, maps) mention a place with the same normalized title. A place independently surfaced by two sources is more trustworthy.

2. **Vibe token overlap** (secondary): how many tokens from the discovery's tags overlap with the user's `signals.query_modifiers`. This is a lightweight proxy for relevance to the user's actual interests.

The filter is pure Python, deterministic, O(n). No LLM, no I/O.

**Why not use `vibe_tags` on `ResearchDiscovery`:** Adding a new field to `ResearchDiscovery` would require a Zod schema change in `nomad-api` and potentially a Prisma migration, since `model_dump()` is written to `research_jobs.discoveries` JSONB. More importantly, the tags would be sparse — the always-on vlog/food/anchor queries produce discoveries with no cluster tag, making the field unreliable. `query_modifiers` is the correct proxy.

---

## `vibe_cluster` — what it still does

`TravelSignals.vibe_cluster` is preserved. The `_vibe_cluster()` function and `_VIBE_CLUSTER` mapping in `signals.py` are unchanged. The change is **where in the pipeline it is consumed**:

| | Before | After |
|---|---|---|
| Cache write key | `destination:season:vibe_cluster` | `destination:season` |
| Usage | Determines cache bucket | Passed to `pool_filter_node` for scoring |

The user's vibe cluster still shapes which 15 discoveries the synthesizer sees — it just happens at read time against a shared pool, not at write time as a cache partition.

---

## Cost analysis

### Why cold miss cost goes up

A cold miss now runs **broader queries** than before. Specifically:

- **YouTube Shorts**: 5 queries → 6 per cold miss. At 100 YouTube API units per `search.list` call: ~500 → ~600 units.
- **YouTube Longform**: 4 queries → 5. ~400 → ~500 units.
- **Total YouTube quota per cold miss**: ~900 → ~1,100 units (~1.2× increase).

Before, each unique (destination, season, vibe_cluster) combination was a separate cold miss — meaning the second user with different vibes paid the full research cost again. After, there is **at most one cold miss per destination+season**, regardless of how many vibe combinations arrive.

Net effect at scale:

| Scenario | Before (FI-6) | After (L0) |
|---|---|---|
| 1st user: Goa monsoon adventure | Full research (900 YT units) | Full research (1,100 YT units) |
| 2nd user: Goa monsoon foodie | **Full research again** (900 units) | Cache HIT (0 units) |
| 3rd user: Goa monsoon relaxation | **Full research again** (900 units) | Cache HIT (0 units) |
| User reiterates (vibes change) | **Full research again** | Cache HIT + filter + synth only |

The cold miss is ~20% more expensive per event, but the number of cold miss events per destination+season drops from N (one per vibe variant) to 1.

### YouTube quota capacity

On the default 10,000 units/day quota:
- Before: ~11 cold misses/day capacity (across all vibe variants)
- After: ~9 cold miss **destinations** per day, but each serves unlimited vibe variants

---

## Pros of this design

1. **True "research once" sharing.** Any user researching Goa in monsoon hits the same pool, regardless of whether they want cafes or waterfalls.

2. **Reiteration is cheap.** When a user updates their vibes after seeing a first draft, the pipeline re-runs only `pool_filter_node` (pure Python) + `synthesizer_node` (one LLM call). Research agents do not re-run.

3. **No `vibe_cluster` in wire contract.** `ResearchDiscovery` schema is unchanged. No Zod or Prisma migration required.

4. **Broader pool improves cross-source agreement.** With queries covering all four clusters, more places appear from multiple sources, giving the filter stronger signal to identify well-validated stops.

5. **Synthesizer quality preserved.** The filter caps input to 15, matching what the synthesizer worked with before. No prompt dilution from the larger pool.

---

## Cons and known limitations

1. **Cold miss costs ~1.2× more** in YouTube API units. This is the deliberate trade described above — pay once, amortize over all users.

2. **Festival / crowd contamination.** Two query slots remain date-dependent:
   - Reddit Q3: branches on `crowd_level` ("hidden gems avoid tourists" vs "hidden gems")
   - Reddit Q6: queries `active_festivals[0]` if any festival is active
   
   These are destination+time signals (not user preference), so the first cold-filling user's travel dates influence the pool for all subsequent users in that destination+season bucket. This is an acceptable trade-off: festivals are rare, and the synthesizer's prompt already receives `active_festivals` to handle festival-awareness correctly at synthesis time.

3. **No stampede protection.** Two simultaneous cold misses for the same destination+season will both run full research and last-writer-wins in the cache. This is inherited behaviour — not introduced here — and requires a Redis SETNX/lock pattern to fix properly in a future PR.

4. **`season="unknown"` collision.** Trips with no parseable dates all fall under the `unknown` season bucket. The first unknown-season cold-filler's pool is served to all subsequent unknown-season users. Mitigated by: most production trips have dates; no degradation from before.

5. **Vibe overlap scoring is approximate.** The filter's secondary scoring criterion (tag/modifier token overlap) is a best-effort signal. Tags reflect a place's natural attributes, not which query surfaced it. In practice, cross-source agreement (the primary criterion) is the dominant signal — well-known places appear from multiple agent sources regardless of vibe.

---

## Future work this enables

The pool_filter architecture is the foundation for user-reiterated trip refinement:

```
User: "More cafes, fewer adventure stops"
    → Update signals.vibe_cluster + signals.query_modifiers
    → Re-run pool_filter_node against cached L0 pool (no research)
    → Re-run synthesizer_node with updated filtered pool
    → Produce revised itinerary in seconds instead of minutes
```

A future L1 cache (keyed by `trip_id:vibe_fingerprint`) can cache synthesized output so even the synthesizer call is skipped on an identical re-request.
