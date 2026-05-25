# Nomad-Agent — Refinement Plan Sign-off

**Status: COMPLETE.** The agent-refinement plan ([`IMPROVEMENT_PLAN.md`](./IMPROVEMENT_PLAN.md)) is delivered. This is the handoff.

**Goal (recap):** lift the itinerary pipeline from a broken/4-of-10 baseline to output competitive with the GPT-5.5 reference itinerary — **on free-tier infrastructure only** (no paid LLM APIs). Quality from prompts, context, caching, and a geo layer — not model spend.

---

## What shipped (chronological)

| Milestone | Delivered |
|---|---|
| **Critical fix** | `_coerce_time` regression (one-char `b83a9f9`) that crashed *every* synthesis — fixed; was the reason the pipeline was sub-baseline. |
| **A — quick wins** | CI gate (`pytest`+`ruff`); wired the **dormant personalization** (`preferences`, `query_modifiers`, `vibe_source_weights` were computed but unused); Reddit relevance/season gating (killed the "kidney stones" class); synthesizer → **Cerebras-235B** (free) with a Groq-70B fallback (timeout-guarded). |
| **B — skills** | Prompts externalized to `app/skills/*.md` with a loader (`@include`, frontmatter, caching) — prompt edits are no longer code changes. |
| **Tier 1 — content** | Synthesizer overhaul: **circuit planning**, food/shopping as first-class content, richer `highlights`; **signals-driven skill overlays** (`regions/india`, `trip_shapes/region_multi_city`, `vibes/food_and_markets`); deterministic `seasonal_tips`; **local-currency** budgets (₹ for India, etc.). |
| **Tier 2 — trip-level fields** | New `route_summary` / `transport_strategy` / `seasonal_tips` / `stay_by_city` / `budget_estimate` across all three repos (Prisma + Zod-free auto-surface + frontend overview section); **removed the AI `emoji`** (noise) end-to-end. |
| **D — geo-routing** | `app/geo/` — free OSM Nominatim geocoding + haversine distances + drive-time hints + sunrise/sunset (sunrise equation) + nearest-neighbour route de-backtracking. A `geo` node feeds the synthesizer a verified **geo brief**; the synth cites real drive-times in `transport_strategy`. |
| **C — L1 cache** | Redis destination-research cache + geocode cache (`app/cache.py`, `research_gate` node). A repeat-destination **HIT skips all 4 research agents** (~13 LLM + ~50 API calls) and relieves the Groq cap. Graceful no-op when Redis is absent. |
| **Refinements** | Honest, filler-aware stats (real maps anchors now count; padding doesn't); travel-day coherence rule; removed a banned-word leak in anchor stubs. |

**Gates:** 187 tests pass, `ruff` clean, graph compiles, CI workflow in place.

---

## Quality trajectory (Rajasthan benchmark vs the GPT-5.5 reference)

`~4/10 (Sprint 7) → ~6.5 (Milestone A) → ~7.5 (Tier 1) → ~8 (Tier 2 + geo)`.

The output now matches the reference on the things that made it good: a **coherent multi-city circuit** with **real drive-times**, **per-city named dishes + restaurants + markets + what-to-buy**, a **local-currency budget**, **stay-by-city** suggestions, **seasonal/packing tips**, an honest human voice (incl. "touristy but worth it once"), and the **desert experience** the earlier pipeline missed. Remaining gap vs the reference is *breadth of live research*, which is gated by free-tier token caps — exactly what the L1 cache mitigates on repeat destinations.

---

## Deferred (intentionally — with rationale + triggers documented)

- **L2 — semantic (vector) retrieval** and **L3 — long-term user memory**: full "why / why-not / value / cost / trigger" analysis in [`SYSTEM_DESIGN.md`](./SYSTEM_DESIGN.md) §5.6 / §5.7. Short version: L2 only pays off once pools are large or research goes fully vibe-neutral; L3 needs real returning users + a cross-service ownership decision.
- **Vibe-neutral research** (purer cache reuse) — pairs with L2; doing it alone trades cold-request relevance for marginal cache purity.
- **OSM Overpass intra-day POI clustering** ("these 3 are within 2 km") — the geo layer's natural next step.

---

## How to operate

- **Models (free tiers):** synthesizer + geo-picker → **Cerebras** (`qwen-3-235b`, 1M tok/day); research agents → **Groq** (`llama-3.3-70b`, ~100k tok/day). The synth has a Groq fallback.
  - **Binding constraint:** Groq's ~100k tokens/day is small — a handful of cold runs exhausts it, degrading the research agents. **Mitigations already in place:** the L1 cache (skip research on repeat destinations) and geo/synth being on Cerebras. If you push volume, move the research agents to Cerebras too (see `.env.example`).
- **Caching:** set `REDIS_URL` to activate (see the Redis setup note from the handoff). Without it, the pipeline runs cold exactly as before — never a hard dependency.
- **Geocoding:** free OSM Nominatim, no key.
- **Cross-repo:** `nomad-api` needs `npx prisma db push && npx prisma generate` for the Tier 2 columns (`route_summary`, etc.) + emoji drop; `nomad-web` renders them. Existing dev trips can be deleted for a clean slate (cascade) or left (nullable columns don't break them).

---

## Sign-off

The refinement plan is **complete and verified** (unit tests + live benchmark runs across the session). The pipeline is production-ready on free infrastructure, with documented, triggered next-steps (L2/L3) when usage justifies them. — Refinement engagement closed.
