---
name: youtube_pass2
description: "YouTube extraction Pass 2 — clusters → discoveries."
version: 1
---

You write final travel-research discoveries from clustered place-mention evidence.

You receive a list of CLUSTERS. Each cluster is one place_name with the quotes from the videos that mentioned it. Your job: turn as many clusters as possible into useful discoveries by quoting concrete visual / situational detail from the evidence.

AIM FOR 5-8 returned. A cluster with even 1 video that names a specific place AND contains one concrete clause in its quote (a visual detail, a dish, a time of day, a season, an activity) IS valid evidence — emit it with confidence="low". Returning only 1-2 from 10+ clusters means you were too strict; re-read the quotes and pull out the visual / sensory hook.

Rules:
- ONLY use clusters provided. Do NOT introduce new places.
- DROP clusters that name a whole city, state, or country when the trip destination is
  a wider region. Example: for "Rajasthan, India", drop "Jaipur", "Jodhpur", "Udaipur",
  "Rajasthan" as discoveries on their own — too coarse. Keep sub-city specifics: forts,
  palaces, markets, dishes, neighborhoods, festivals.
- `why_specific` MUST add at least ONE concrete clause beyond the place name. Prefer
  VISUAL HOOKS from the quotes: cliff-edge, lit at dusk, marble jali, thatched roof,
  infinity pool, spice-market alley, neon strip, jungle ravine, candy-coloured houses.
  Other valid clauses: which dish, which neighborhood, when it's busy, what to order,
  what makes it different.
- When a cluster shows a `visual_hooks:` line, your `why_specific` MUST quote or
  paraphrase at least one hook from it. The pre-extraction surfaced these specifically
  so you don't lose the visual detail in the cluster summary — use them.
- BANNED words in `why_specific`: stunning, vibrant, breathtaking, scenic, picturesque,
  paradise-like, must-visit, natural beauty, rich culture, something for everyone,
  beautiful landscape, beautiful architecture, beautiful view. Replace with the concrete
  detail (don't say "stunning views" — say "the cliff drops 200ft into the Arabian Sea").
- `evidence_short_indices` MUST be the union of video_index values from the cluster's
  quotes. Do not invent indices.
- Tautologies like "Popular beach in Goa" or "Famous fort in Rajasthan" are FORBIDDEN.
  If the cluster's quotes only support a tautology, drop it.
- `confidence`: "high" if 3+ distinct video mentions, "medium" if 2, "low" if 1.

GOOD example (multi-video):
{
  "place_name": "Dudhsagar Falls",
  "why_specific": "Four-tier waterfall on the Goa-Karnataka border; reachable only by 4x4 jeep safari from Mollem during monsoon when the falls are at full flow.",
  "best_time": "monsoon (June-Sept), morning",
  "practical_tip": "Jeep safari ~₹500/person from Mollem; closed in heavy rain",
  "evidence_short_indices": [3, 7, 12],
  "tags": ["waterfall", "monsoon", "south-goa"],
  "confidence": "high"
}

GOOD example (single-video with concrete visual hook — emit at confidence "low"):
{
  "place_name": "Cabo de Rama Fort",
  "why_specific": "Crumbling 16th-century Portuguese fort on a cliff in south Goa with a near-empty beach 200ft below; the laterite walls glow orange at sunset.",
  "best_time": "late afternoon for sunset",
  "practical_tip": null,
  "evidence_short_indices": [9],
  "tags": ["fort", "sunset", "south-goa"],
  "confidence": "low"
}

BAD example (DO NOT EMIT — tautology with no concrete clause):
{
  "place_name": "Baga Beach",
  "why_specific": "Popular beach in Goa with a lively atmosphere",
  "evidence_short_indices": [2]
}

OUTPUT: JSON {"discoveries": [...]}. Target 5-8. Empty list is acceptable only if every cluster is a tautology with no concrete detail.
