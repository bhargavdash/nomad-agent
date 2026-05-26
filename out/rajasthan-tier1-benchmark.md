# Rajasthan Dec 20–31 — Tier 1 Benchmark (prompt overhaul + skill overlays)

**Date:** 2026-05-25 | **Run:** `out/rajasthan-tier1.json` | **Synth:** Cerebras qwen-3-235b
**Changes:** circuit-planning prompt + `route_summary`, content-completeness/highlights rules, signals-driven overlays (`regions/india`, `trip_shapes/region_multi_city`, `vibes/food_and_markets`), deterministic `seasonal_tips`.

## What the overlays + prompt produced

- **Overlays fired:** `regions/india`, `trip_shapes/region_multi_city`, `vibes/food_and_markets` ✓
- **Circuit committed (route_summary):** `Jaipur (3) → Jodhpur (3) → Jaisalmer (3) → Udaipur (2)` — the classic, **geographically coherent** Rajasthan circuit with per-city day allocation. (Sprint 7 backtracked; Milestone A was linear-ish; Tier 1 now *plans the circuit explicitly*.)
- **Transport strategy** (the India overlay): named trains + class — "Shatabdi Express", "overnight train", "AC2 for comfort" (9× AC2 mentions).
- **Food, named** (matches GPT-5.5): dal baati (4), thali (9), kachori (2) at **named eateries** — LMB, Jodhpur Saffron, Ambrai, Choki Dhani.
- **Shopping, named** (the user's explicit ask): Bandhani, leheriya, leather mojris at Tripolia/Bapu/Sardar/Sadar bazaars.
- **`highlights` are now concrete takeaways:** "Saffron-laced kachoris at Sardar Market", "Leather mojris at Sadar Bazaar", "Thali at Jodhpur Saffron" — not stop-name restatements.
- **Honest voice** (matches GPT-5.5): "Choki Dhani… touristy — go for the [experience]", "skip the elephant rides, they're cruel", "save the longer tour for next visit".
- **Desert experience** (Thar, per preferences): evening camel safari, golden dunes at sundown, Kuldhara Village, Bada Bagh.
- **Negativity:** 0 (kidney-stones/corruption class stays gone).

## Honest critique

| Issue | Severity | Owner |
|---|---|---|
| **maps-share rose to ~79%** — BUT these are *real named places* the 235B model produced (Amber Fort, Mehrangarh, Ambrai, Kuldhara…), not filler. True filler this run ≈ 2–3 stops ("Lunch in Jodhpur/Jaisalmer", "Rajasthan rural areas"). Research was thin this run (yt 0 / blog 4 / reddit 3), so the model leaned on parametric knowledge. | Metric artifact | WS6 (caching/better research yield); the `source="maps"` overload itself |
| **`stats_places=7 / tips=1 / photo=1`** undercount, because the honest-stats rule only counts non-maps stops — and the good content is maps-tagged. | Metric artifact | revisit stats once `maps` is disambiguated |
| **`route_summary` is generated but dropped** — no field in the wire schema. The circuit overview / transport strategy / packing tips have nowhere to live. | Real gap | **Tier 2** |
| **Geo slip:** "Nathdwara Temple" placed in Jaisalmer (it's near Udaipur). | Minor | WS7 (geo grounding) |
| **Day 7 travel-day logic:** lunch "in Jaisalmer" scheduled before the evening train *to* Jaisalmer. | Minor | synth travel-day coherence |

## Verdict

Per-day **content quality now closely tracks the GPT-5.5 benchmark** (circuit, per-city dishes + named restaurants + markets + what-to-buy, transport strategy, honest caveats) — at $0 model cost. The dominant *artifact* is `source="maps"` doing double duty (real-named anchor vs filler), which inflates maps-share and deflates stats; that's the thing to address next (WS6 + a source disambiguation), not a content regression. The biggest *structural* gap — nowhere to surface the circuit overview / transport / packing / budget — is exactly **Tier 2**.

Estimated weighted score vs handmade: **~7.5/10** (up from Milestone A ~6.5, Sprint 7 ~4), gated now by route micro-errors and the missing trip-level layer.
