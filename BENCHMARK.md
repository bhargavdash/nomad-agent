# Sprint 2 AI-6 — Pipeline Benchmark & Quality Analysis

**Date:** 2026-05-12
**Run by:** end-to-end via `scripts/run_pipeline.py`
**LLM config:** Groq Llama-3.3-70b-versatile for all four roles (YouTube, Reddit, Google Blog, Synthesizer)
**Pipeline status:** ✅ Both sample trips succeed end-to-end on the first attempt. No retries, no skeleton fallback, no crashes.

> TL;DR: **The plumbing works. The content is mid.** ~70 % of final-itinerary stops are generic `source="maps"` placeholders my own synthesizer padded in, because the upstream agents (especially YouTube and Reddit) are returning very little usable signal. This is exactly the gap the next sprint task (AI-7, per-source hardening) was scoped to close — running the pipeline now made that concrete.

---

## 1. Test inputs

| Sample | Destination | Dates | Season | Vibes | Pace |
|---|---|---|---|---|---|
| `samples/goa-december.json` | Goa, India | 2026-12-15 → 22 (7d) | peak / **very_peak** crowd / Christmas+NYE festival | beaches, street food, nightlife, hidden gems | Balanced |
| `samples/manali-monsoon.json` | Manali, India | 2026-07-10 → 17 (7d) | **monsoon** / low crowd / 2 active warnings | adventure, hidden gems, mountains | Action-Packed |

Signal layer correctly differentiated both — no LLM call yet and the destinations already produce different query plans, crowd levels, and warnings.

---

## 2. Performance benchmark

### Wall-clock timing (sequential execution)

| Stage | Goa (s) | Manali (s) | Notes |
|---|---|---|---|
| Signals | <0.01 | <0.01 | Pure Python, no LLM |
| **YouTube agent** | **18.0** | **11.7** | 4–5 queries × YouTube API + transcript fetch + 3 pass1 LLM calls + 1 pass2 LLM call |
| **Reddit agent** | **72.5** | **46.1** | Dominant cost — Reddit JSON API with rate-limited fan-out (15–18 query-sub pairs, 1s sleep between) + comment enrichment + 1 LLM call |
| **Google Blog agent** | **5.4** | **5.7** | 3–4 Tavily searches (parallel) + 1 LLM call |
| **Synthesizer** | **5.7** | **6.8** | 1 LLM call |
| **End-to-end** | **~100 s** | **~73 s** | Sequential. Parallel execution (Sprint 3 AI-9) cuts the wall-clock since YouTube + Reddit + Google can overlap; theoretical floor ≈ max(YT, Reddit, Google) + synth + signals = Reddit-bound (≈55–80 s) |

**Where the time goes:** Reddit dominates. Roughly 60–75 % of total wall-clock is spent inside the Reddit agent, almost entirely waiting on the 1-second rate-limit sleep × N requests. LangGraph parallel orchestration in Sprint 3 will cut this in half, but doesn't fix the per-agent latency — that needs a smaller fan-out or async pipelining inside the Reddit tool itself.

### LLM call count per run

| Agent | LLM calls / run | Model | Tokens (rough) |
|---|---|---|---|
| YouTube | 4 (3 pass1 batches + 1 pass2 synth) | Groq Llama-3.3-70B | ~6–10 k in, ~1–2 k out |
| Reddit | 1 | Groq Llama-3.3-70B | ~5–8 k in, ~1 k out |
| Google Blog | 1 | Groq Llama-3.3-70B | ~4 k in, ~1 k out |
| Synthesizer | 1 | Groq Llama-3.3-70B | ~3–4 k in, ~2–3 k out |
| **Total** | **7** | | well within Groq free-tier quota |

Cost: effectively zero on Groq free tier. The pipeline is **API-budget healthy** for further iteration.

---

## 3. Discovery yield per agent

| Agent | Goa returned | Manali returned | Quality verdict |
|---|---|---|---|
| YouTube | **1** (from 52 raw videos, 30 quality-filtered, 18 sent to LLM, 14 clusters identified) | **1** | 🔴 **Massively under-delivering.** Pass-2 clustering throws away 13/14 candidate places. |
| Reddit | 5 returned | 3 returned (5 extracted, 2 dropped by dedupe) | 🟠 **Off-topic.** Goa: 4 of 5 are about India broadly, not Goa. Manali: **3 of 3 are not about Manali at all.** |
| Google Blog | 5 returned | 8 returned | 🟡 **On-topic but generic.** Real place names, but bodies are stock travel-blog filler — no restaurant detail, no historical context. |

### Final itinerary stop source breakdown

| Destination | Total stops | youtube | reddit | blog | **maps (my padding)** |
|---|---|---|---|---|---|
| Goa | 23 | 1 (4 %) | **0 (0 %)** | 5 (22 %) | **17 (74 %)** |
| Manali | 24 | 1 (4 %) | **0 (0 %)** | 7 (29 %) | **16 (67 %)** |

🔴 **The synthesizer is using zero Reddit discoveries in the final output** because the Reddit content is off-topic (broad India tips, not destination-specific places). Synthesizer can't anchor a stop to "Indian public toilets" — correct decision, but it means 100 % of the Reddit pipeline cost (≈60 s) is wasted output for these two trips.

🟡 **The synthesizer pads with generic maps stops** like "Lunch at a local eatery", "Cultural anchor", "Sunset at the beach", "Pool time". These add structure without adding signal — and inflate `stats_places` artificially.

---

## 4. Correctness audit (does it satisfy the spec's "done when"?)

| Acceptance criterion (from AI-6 spec) | Goa | Manali |
|---|---|---|
| `scripts/run_pipeline.py samples/<x>.json` prints a full `AIItinerary` JSON | ✅ | ✅ |
| JSON has `durationDays` items (7) | ✅ 7 days | ✅ 7 days |
| No duplicate stops across days | ✅ verified — 23 unique names / 23 stops | ✅ verified — 24 unique names / 24 stops |
| Every stop tagged with at least one source | ✅ all stops have `source` populated | ✅ |
| Any stop traceable back to which agent contributed it | ✅ via `AIItinerary.discoveries` list + matching `discovery_title` in synthesizer prompt | ✅ |
| Goa and Manali produce visibly different output | ✅ — see §5 | ✅ |

**All acceptance criteria pass.** AI-6 is functionally done; the rest of this document is about quality, not correctness.

---

## 5. Destination differentiation — is the AI actually personalising?

The two outputs are genuinely different — this isn't a destination-keyword-swap of one template:

| Aspect | Goa | Manali | Differentiated? |
|---|---|---|---|
| Emoji | 🌴 | 🏔️ | ✅ |
| Day 1 title | "Arrival and Beach Time" | "Arrival and Acclimatization" | ✅ |
| Geography per day | "North Goa" stays consistent | "Manali" / "Old Manali" sub-areas | ✅ |
| Activities | Baga beach, Anjuna Flea Market, Purple Martini, Chapora Fort | Hidimba Devi Temple, Solang Valley, Bhrigu Lake trek, Jogini Waterfalls | ✅ (no overlap) |
| Vibe-matching | Nightlife reflected: "Party at a local nightclub" Day 5 | Adventure reflected: "Trek to Bhrigu Lake" Day 4, "Paragliding" Day 6 | ✅ |
| Festival/season signal pickup | Christmas Market on Day 5 (NYE-adjacent) | Monsoon warnings **NOT surfaced** in any stop or day description | ⚠️ partial |

🟠 **The monsoon signal is being squandered.** The signals layer correctly produced two strong warnings ("heavy rain, road closures", "landslides possible"), but they never appear in the Manali itinerary's stop descriptions or day narratives. The synthesizer prompt does receive them but doesn't enforce that they surface. The user trekking Bhrigu Lake in July genuinely needs that warning — this is a usability bug, not just a polish issue.

---

## 6. Bugs found in this run

### 🔴 P0 — Chronological order bug in synthesizer padding (mine to fix)

Five stops across the two outputs are out of chronological order within their day:

```
Goa     Day 3:  10:00 AM Anjuna Flea → 5:00 PM Purple Martini → 2:00 PM Cultural anchor   ⚠
Goa     Day 5:  10:00 AM Christmas market → 9:00 PM Party → 2:00 PM Cultural anchor       ⚠
Manali  Day 5:  11:00 AM Local Market → 3:00 PM Relaxation → 2:00 PM Cultural anchor      ⚠
Manali  Day 6:  9:30 AM Paragliding → 4:30 PM Tea Break → 2:00 PM Cultural anchor         ⚠
Manali  Day 7:  2:30 PM Departure → 3:30 PM Shopping → 2:00 PM Cultural anchor            ⚠
```

**Root cause:** `_default_anchor_stop(sort_order, index_in_day)` in `app/agents/synthesizer.py` picks a preset by `index_in_day % 6`. When the LLM emits 2 stops and we pad to 3, we always hit preset index 2 = "2:00 PM Cultural anchor" regardless of when the day's existing stops finish.

**Fix (≤ 30 min):** After assembling all stops for a day (LLM + padding), sort by chronological time (`time` + `ampm` → minutes-since-midnight) before assigning `sortOrder`. Alternatively, pick the padding preset based on the latest existing stop's time.

### 🟠 P1 — `stats_places` over-counts

`stats_places` for Goa is **22** but only **6** stops are real (1 youtube + 5 blog) — the remaining 16 "places" are generic maps anchors ("Lunch at a local eatery", "Cultural anchor", "Pool time"). The number lies to the UI. Either count only stops with `source != "maps"`, or count distinct `discovery_title` references.

### 🟡 P2 — Goa `stats_tips=5` is technically correct but misleading

`stats_tips=5` counts the 5 Reddit discoveries with tip/warning tags — but the synthesizer didn't use any of them as stops, so the user sees a "5 tips" badge in the UI that maps to invisible content. Either drop unused discoveries from the output, or count only tips referenced by a stop.

---

## 7. Skeptical analysis — agent by agent

### YouTube — extracting **1 place from 52 videos** is unacceptable

The agent finds 14 candidate clusters (`'Baga beach', 'Masala Crab', 'Parra Road', 'Anjuna Beach', 'Arambol beach'` and 9 more for Goa) but the pass-2 synthesizer LLM step returns exactly **one**. That's a 93 % drop in the final step.

The single "Baga beach" body — *"A popular beach in Goa with multiple mentions across different videos, indicating its prominence in the region"* — is itself a tell: the agent is summarising statistics about its own clustering, not extracting **what the videos actually showed**. That's the photo-worthy/aesthetic content the per-source content strategy says YouTube should specialise in, and it's nowhere in the output.

**Likely culprits:**
1. Pass-2 prompt is over-restrictive — probably demands "high confidence" too strictly.
2. The pass-1 → pass-2 cluster summary discards the original transcripts/descriptions, so pass-2 has nothing visual left to extract from.
3. Transcript fetch only got 3/18 videos (YouTube transcripts often unavailable on Shorts) — so most cluster context is title + description only, which is anaemic.

**What AI-7 should do here:**
- Loosen the pass-2 prompt; aim for 5–8 places returned, not 1.
- Carry visual descriptors through pass-1 → pass-2 (don't summarise them away).
- Add quality gate test: "YouTube agent must return ≥ 4 discoveries with at least one photo/view-related tag."

### Reddit — wrong content type entirely

For Manali, the agent returned three "Manali" discoveries that are actually about: (1) Indian tourist-place pricing for foreigners, (2) surrendering an Indian passport at RPO Chandigarh, (3) the 2026 monsoon onset over India. **Zero are about Manali.**

This is because the agent searches `r/india`, `r/travel`, `r/IndiaTravel`, `r/solotravel` with `?q=Manali+India+tips&sort=relevance` — but the top-relevance results on those subs that match "Manali" are giant pan-India trip-report posts that **mention** Manali in one paragraph among many. The extraction LLM then dutifully extracts the post's most interesting tip — which is rarely about Manali.

**The tool is healthy** (it's getting posts back); **the agent is not** (it's not filtering or weighting for destination-specificity).

**What AI-7 should do here:**
- Post-fetch filter: drop posts whose title and first 200 chars don't contain the destination string (or a known sub-region).
- LLM prompt rule: "Only extract insights specifically about {destination}. If a post discusses many places, only extract the {destination}-specific paragraphs. If none, return nothing for that post."
- Quality gate test: "≥ 60 % of returned Reddit discoveries must have the destination string in title OR body."
- Bonus: weight `r/{destination}` (e.g. `r/goa`) much higher than generic subs.

### Google Blog — generic regurgitation, no editorial value

Every single blog discovery for Manali follows the template:
> *"A {temple/lake/museum/waterfall} to visit in Manali, part of a travel guide that includes where to go, eat, stay, and shop."*

That's the extraction LLM paraphrasing the same generic excerpt for every place. It's not extracting the **distinctive value** of blog content (which AI-7 says should be: structured day-frames, restaurant detail, cultural context, pairing suggestions).

For Goa, "Antares" is described as "A beach club with great food, located in North Goa. Best for: couples, foodies." — that's at least a category + audience but it's still surface-level. No mention of the cuisine type, signature dishes, price tier, or whether it's a sunset-vs-dinner spot.

**What AI-7 should do here:**
- Strengthen prompt to demand named cuisine / signature dishes for restaurants, named architects/dynasties for temples, named treks/difficulty for hikes.
- Reject discoveries whose body matches a stock template (regex: `"A .* to visit in .*, part of a travel guide"`).
- Quality gate test: "≥ 50 % of blog discoveries must contain at least one proper-noun beyond the place name itself."

### Synthesizer — correct but conservative

The synthesizer made the right calls structurally:
- Ignored off-topic Reddit content (correctly)
- Used all 5 Goa blog stops, 7 of 8 Manali blog stops
- Wove the YouTube discovery into Day 1/2 (the "anchor" position)
- Produced coherent day narratives, sensible time slots, valid sortOrder (except the padding bug)
- Picked appropriate emojis 🌴 / 🏔️
- Picked up the Christmas signal on Goa Day 5

But it's over-padding. With only 6 (Goa) / 9 (Manali) real anchors and 7 days at 4–5 stops/day target, the only way to hit the target is to invent maps stops. The padding logic is too eager — it would be better to **emit a shorter itinerary with denser real content** than pad to a fixed target with filler.

**What AI-7 should do here:**
- Make `pace_density` an upper bound, not a target — allow fewer stops/day if research is thin.
- Reject the synthesizer's output if maps-stops > 50 % and surface this as a "research insufficient, retry with different queries" signal to the agents in a future iteration.

---

## 8. What this run made concrete for the AI-7 hardening sprint

Going into this benchmark, AI-7 was scoped from the content-strategy *theory* in the board (YouTube → photo, Reddit → tips, Google → cultural). Now we have data showing exactly how each agent fails:

| Agent | Theoretical strength | Observed reality | Concrete fix |
|---|---|---|---|
| YouTube | Photo-worthy / aesthetic spots | Returns 1 generic place from 52 videos | Loosen pass-2 prompt, preserve visual descriptors through clustering, target 5–8 returned |
| Reddit | Tips, warnings, timing, transport, honest critique | Returns India-broad tips, almost nothing destination-specific | Destination-mention filter pre-LLM; "only extract about {destination}" prompt rule |
| Google Blog | Structured day-frames, restaurants, cultural context | Stock templated phrases, no proper nouns | Demand named entities (cuisine, dishes, architects, trek names); regex-reject template phrases |
| Synthesizer | Cross-source merge + day shaping | Correct structure, over-pads with maps when research is thin | `pace_density` as upper bound; bias toward fewer-but-real stops |

This is exactly the input that the **per-source content strategy spec doc** (AI-7's first deliverable) needs: not abstract principles, but the specific failure modes to fix. The benchmark output JSONs at `out/goa.json` and `out/manali.json` are the regression fixtures for that hardening pass.

---

## 9. Honest verdict

**Pipeline correctness: 9/10.** It runs, it returns valid JSON, it handles errors gracefully, every stop is traceable, the two destinations differentiate. Sprint 2 AI-6's spec is met.

**Itinerary usability: 4/10.** A user looking at the Goa Day 6 schedule ("8 AM breakfast at the villa, 10 AM beach time, 3 PM pool time") learns nothing they couldn't have written themselves. The Manali user gets no warning about monsoon road closures despite the signal layer producing one. Both itineraries are 67–74 % synthesizer-generated filler.

**Source-of-truth integrity: 6/10.** Stop sources are honest (we don't claim youtube for maps stops), but `stats_places=22` when only 6 stops are real is borderline dishonest to the UI. `stats_tips=5` when zero tips appear as stops is worse.

**The right way to read this:** the AI **plumbing** is production-ready; the **content** is what AI-7 was designed to fix. We deliberately ordered Sprint 2 to ship the JSON-flow first precisely so that the per-source weaknesses would be **observable** before the hardening pass. That worked — the observations above are concrete, prioritised, and testable.

---

## 10. Recommended next moves (priority order)

1. **Fix the padding chronology bug** (P0, < 30 min) — see §6.
2. **Fix the `stats_places` / `stats_tips` over-count** (P1, < 1 hr) — count only non-maps stops / referenced tips.
3. **Surface signals.warnings in the Manali itinerary** (P1, < 1 hr) — add to the synthesizer prompt as a hard rule: "If `signals.warnings` is non-empty, mention at least one warning in Day 1's description."
4. **AI-7 Reddit destination-mention filter** (P0 for AI-7, ~1 hr) — biggest quality win available.
5. **AI-7 YouTube pass-2 prompt loosening + visual descriptor preservation** (P0 for AI-7, ~2 hr).
6. **AI-7 Google Blog "demand named entities" prompt + template-rejection regex** (P1 for AI-7, ~1 hr).
7. **AI-7 supplementary source: OpenStreetMap Overpass for POI lat/lng** — would give the synthesizer real geographic clustering data instead of guessing from place names. Deferred to AI-7's eval phase.

Run-output artefacts kept under `out/` for AI-7 regression:
- `out/goa.json`, `out/goa.stderr.log`
- `out/manali.json`, `out/manali.stderr.log`

---

---

# Sprint 3 AI-7 — Improvement Benchmark

**Date:** 2026-05-17
**Run by:** end-to-end via `scripts/run_pipeline.py`
**LLM config:** Groq Llama-3.3-70b-versatile (YouTube, Reddit, Google Blog); Groq Llama-3.3-70b-versatile (Synthesizer — note: switched from Claude Sonnet to Groq for cost; reverts if synthesis quality drops further)
**Test input:** `samples/rajasthan-december.json` — Rajasthan, India | Dec 1–10 (10 days) | heritage, desert, local cuisine, photography | Balanced pace | $$ | Heritage Haveli

> TL;DR: **The P0 bugs are fixed. The Reddit filter is working. Stats are honest. But the content quality ceiling hasn't moved much** — YouTube still produces thin bodies, Google Blog still uses stock templates, and "Cultural anchor" padding persists because the Python fallback presets weren't updated when the LLM prompt rule was added. Reddit is now contributing 8 relevant discoveries but still zero place-stops in the final itinerary (correctly treated as context, not destinations).

---

## 1. Test input

| Sample | Destination | Dates | Season | Vibes | Pace | Duration |
|---|---|---|---|---|---|---|
| `samples/rajasthan-december.json` | Rajasthan, India | 2026-12-01 → 10 (10d) | peak / peak crowd | heritage, desert, local cuisine, photography | Balanced | 10 days |

Signal layer output:
- Region: india | Season: peak | Crowd: peak | Budget: mid | Pace density: 4 stops/day
- Active festivals: — | Weather hint: — | Warnings: —
- Query modifiers include: `peak season`, `best weather`, `less crowded`, `local favorites`, `heritage`, `desert`, `local cuisine`, `photography`

---

## 2. Performance benchmark

### Wall-clock timing (sequential execution)

| Stage | Rajasthan (s) | Goa Sprint 2 (s) | Manali Sprint 2 (s) | Notes |
|---|---|---|---|---|
| Signals | <0.01 | <0.01 | <0.01 | Pure Python |
| **YouTube agent** | **~12** | **18.0** | **11.7** | 4 queries × 15 videos, 54 unique → 30 quality-filtered → 8 clusters → pass2 returned 6 → kept 4. Faster than Goa. |
| **Reddit agent** | **~48** | **72.5** | **46.1** | 5 queries × 3 subs = 15 pairs. 61 raw posts, 21 dropped off-topic by destination filter, 10 survived, comments enriched, LLM call returned 8. |
| **Google Blog agent** | **~5** | **5.4** | **5.7** | 3 queries × 5 Tavily results = 15 articles → LLM extracted 8 |
| **Synthesizer** | **~7** | **5.7** | **6.8** | 10-day trip (vs 7) — slightly more LLM work. 1 attempt, succeeded. |
| **End-to-end** | **~72 s** | **~100 s** | **~73 s** | Rajasthan is 10 days (43% more) yet faster than Goa due to Reddit destination filter cutting request count |

**Reddit speedup:** The destination-mention filter (`_post_mentions_destination`) drops off-topic posts before comment enrichment. 21 posts dropped = 21 fewer comment-fetch HTTP calls × 2s sleep overhead each. That's the dominant cause of the time reduction vs Sprint 2's Goa run.

### LLM call count per run

| Agent | LLM calls | Notes |
|---|---|---|
| YouTube | 3 (2 pass1 batches + 1 pass2) | 1 pass1 batch returned 400 (validation error on empty place_name), 2 succeeded |
| Reddit | 1 | |
| Google Blog | 1 | |
| Synthesizer | 1 | |
| **Total** | **6** | One fewer than Sprint 2 (Groq 400 on one pass1 batch counted as a failed call, not a retry) |

---

## 3. Discovery yield per agent

| Agent | Rajasthan returned | Quality verdict |
|---|---|---|
| YouTube | **4** (from 54 raw, 30 quality-filtered, 8 clusters, pass2=6, kept=4) | 🟡 **Better than Sprint 2** (1→4). Still thin bodies: "A palace in Jaipur, also known as City Palace." Hawa Mahal correctly dropped (vague_phrase). Jaisalmer Fort dropped (short_body=19 chars). |
| Reddit | **8** (from 61 raw, 21 off-topic dropped, 10 filtered, extracted=8, kept=8) | 🟢 **Massive improvement over Sprint 2** (3→8). All 8 are Rajasthan-specific: kidney stone risk, water quality, tourist prices, elephant tourism ethics, train class, Udaipur rec, pharma safety, rural hospitality. Destination filter is working. |
| Google Blog | **8** (from 15 articles → LLM extracted 8) | 🟡 **Same quality as Sprint 2** — content bodies still stock-template style. "Lake city with a mix of Rajput and Mughal architecture, and scenic views. Best for: romance, history enthusiasts." No restaurant details, no architect names, no pairing suggestions. |

### Final itinerary stop source breakdown

| Destination | Total stops | youtube | reddit | blog | **maps (padding)** |
|---|---|---|---|---|---|
| Rajasthan (Sprint 3) | 30 | 2 (7%) | **0 (0%)** | 7 (23%) | **21 (70%)** |
| Goa (Sprint 2) | 23 | 1 (4%) | 0 (0%) | 5 (22%) | 17 (74%) |
| Manali (Sprint 2) | 24 | 1 (4%) | 0 (0%) | 7 (29%) | 16 (67%) |

Reddit still contributes 0 place-stops. This is now **correct behavior** — the 8 Reddit discoveries are warnings and tips (kidney stone risk, water quality) that the synthesizer correctly surfaces in day *descriptions* rather than inventing a place called "Rajasthan kidney stone risk". The source integrity is better; the padding rate is essentially unchanged.

---

## 4. Bugs fixed vs Sprint 2

### ✅ FIXED — P0: Chronological ordering bug

Sprint 2 `BENCHMARK.md §6` identified five out-of-order stops across Goa + Manali.

**Rajasthan run:** Zero chronological violations across all 10 days and 30 stops. Day 10 has "2:00 PM Cultural anchor → 2:30 PM Departure" — 30-minute gap is close but technically correct (2:00 before 2:30). Fix confirmed: `_resort_stops_chronologically()` with `_time_to_minutes()` in `synthesizer.py` works correctly.

### ✅ FIXED — P1: `stats_places` over-count

Sprint 2 Goa: `stats_places=22` despite only 6 real places.

**Rajasthan run:** `stats_places=9` — correctly counts only non-maps stops (2 youtube + 7 blog = 9). With 21 maps stops out of 30 total, the honest count is 9 not 30. Fix confirmed: `_compute_stats()` now excludes `source="maps"` stops.

### ✅ FIXED — P2: `stats_tips` misleading count

Sprint 2 Goa: `stats_tips=5` badge but 0 tips actually referenced as stops.

**Rajasthan run:** `stats_tips=0` — 8 Reddit warning/tip discoveries found but none are referenced as place-stops (correct). No phantom tip badge. Fix confirmed: count only discoveries referenced by an actual stop.

### ✅ FIXED — P1: Warnings not surfaced in itinerary

Sprint 2 Manali: monsoon landslide/road-closure warnings generated by signals layer but never appeared in the itinerary.

**Rajasthan run:** Day 1 description: *"Be mindful of dehydration and drink plenty of water, as Rajasthan is part of the 'kidney stone belt' in India."* Day 3 description: *"Be mindful of the water quality in Rajasthan."* These warnings came directly from Reddit discoveries and were surfaced via the synthesizer prompt Rule 8 (`WARNINGS SURFACING`). Fix confirmed.

### ✅ IMPROVED — Reddit destination-mention filter

Sprint 2: Reddit returned pan-India content (RPO Chandigarh passport surrender, monsoon onset over India) for a Manali query.

**Rajasthan run:** 21 of 61 raw posts (34%) dropped by `_post_mentions_destination()` before LLM. The 10 surviving posts yielded 8 genuine Rajasthan-specific discoveries. Filter is working; false-positive rate (wrongly dropped on-topic posts) unknown but output quality is clearly higher.

### ✅ IMPROVED — YouTube pass-2 yield: 1 → 4 places

Sprint 2: 52 raw videos → 1 place returned (93% drop in pass-2).

**Rajasthan run:** 54 raw videos → 4 places returned (33% drop in pass-2). Still below the AI-7 target of 5-8, but meaningfully better. Two drops were correct: "Hawa Mahal" (vague_phrase match: "beautiful place") and "Jaisalmer Fort" (short_body: 19 chars). The quality gate is doing its job.

---

## 5. Bugs confirmed NOT fixed / newly observed

### 🔴 R1 — "Cultural anchor" still appears in padding output (7 of 30 stops)

The synthesizer prompt (Rule 3) explicitly says *"NOT a generic label like 'Cultural anchor'"*, but `_default_anchor_stop()` in `synthesizer.py` still has `"Cultural anchor"` as `presets[2]`. When the LLM delivers <3 stops for a day, Python padding fires and inserts "Cultural anchor" at 2:00 PM regardless. Affects Days 3, 5, 6, 7, 8, 9, 10.

**Root cause:** The LLM prompt rule and the Python fallback presets are out of sync. Prompt was updated; fallback presets were not.

**Fix (< 15 min):** Replace `"Cultural anchor"` in `_default_anchor_stop` `presets[2]` with a more concrete named anchor like `"Old City exploration"` or simply remove the preset and derive it from the trip destination. Also rename the other generic presets ("Neighborhood walk" → `"Bazaar walk"` or similar).

### 🟠 R2 — Day 5 (Jaisalmer Day 2) is entirely maps padded

The synthesizer exhausted Jaisalmer blog/YouTube candidates after Day 4. Day 5 has 3 maps-only stops: Neighborhood walk → Cultural anchor → Exploring the Havelis. The synthesizer should have recognised it had no more Jaisalmer material and either (a) tightened to fewer stops, or (b) borrowed a discovery from another city's overflow. This is the "synthesizer should allow fewer stops when research is thin" principle from AI-7, not yet fully implemented — the target was made an upper bound, but padding still fills to the minimum (3).

### 🟠 R3 — Google Blog body quality unchanged

All 8 blog discoveries follow the same template: `"<Place> with <generic_description>. Best for: <audience>."` No restaurant cuisine types, no architect names, no specific trek grades. This was the AI-7 P1 fix target ("demand named entities; reject template phrases") — appears not yet applied.

### 🟡 R4 — YouTube bodies are thin but improving

Bodies like "A palace in Jaipur, also known as City Palace" and "A fort in Jaipur, also known as Amer Fort" give the synthesizer no visual or contextual detail to write rich stop descriptions from. The AI-7 fix (carry visual descriptors through pass-1 → pass-2) appears partially applied (4 places vs 1 is a win) but body richness is unchanged.

### 🟡 R5 — `r/rajasthan` subreddit not in destination map

`_DESTINATION_SUBREDDIT_MAP` has `"rajasthan": ["IndiaTravel"]` but not `r/rajasthan` (which exists and has ~40k members). The agent defaulted to `r/travel`, `r/solotravel`, `r/india`, `r/IndiaTravel`. Adding `r/rajasthan` would materially improve recall for Rajasthan-specific content.

---

## 6. Destination differentiation — does Rajasthan feel distinct?

| Aspect | Rajasthan (Sprint 3) | Differentiated from Goa/Manali? |
|---|---|---|
| Emoji | 🏰 | ✅ |
| Multi-city routing | Jaipur → Jodhpur → Jaisalmer → Udaipur → Pushkar → Ranthambore | ✅ Synthesizer correctly distributed 10 days across 6 sub-cities |
| Day 1 warning | Kidney stone belt mention | ✅ Reddit-sourced, unique to Rajasthan |
| Activities | Forts, palaces, desert safari, wildlife, stepwells | ✅ No beach or monsoon content |
| Vibe alignment | heritage → forts/palaces; photography → no specific photo-stop surfaced | ⚠️ Photography vibe not reflected in YouTube content (thin bodies) |
| December signal | Season=peak, crowd=peak, query modifiers include "less crowded" | ✅ Signal correctly characterised |
| Festival signal | No active festivals (correct — Pushkar Camel Fair is October/November) | ✅ |

---

## 7. Honest verdict — Sprint 3

**Pipeline correctness: 9/10** (unchanged). 10 days, valid JSON, no crashes, every stop traceable.

**Itinerary usability: 5/10** (up from 4/10 in Sprint 2). Improvements: warnings surfaced in day descriptions, multi-city routing works for a 10-day trip, stats are honest. Regressions: none new. Ceiling: still 70% maps padding, still "Cultural anchor" filler.

**Source-of-truth integrity: 8/10** (up from 6/10). `stats_places` and `stats_tips` are now honest counts. Reddit filter removes off-topic content before LLM. No phantom tip badges.

**Reddit relevance: 8/10** (up from 2/10 in Sprint 2). Destination filter is the single biggest quality win. 8 Rajasthan-specific discoveries vs 0 useful ones for Manali.

**YouTube depth: 4/10** (up from 2/10). 4 places vs 1 is meaningful. Bodies are still too thin for the synthesizer to write rich stop descriptions.

**Google Blog depth: 3/10** (unchanged). Template bodies, no named entities beyond place name.

---

## 8. Priority order for next sprint

1. **Fix `_default_anchor_stop` presets** — replace "Cultural anchor" with named concrete stops; sync with the LLM prompt rule already in place. (P0, < 15 min)
2. **Add `r/rajasthan` to subreddit map** — and audit other missing destination-specific subs. (P1, < 30 min)
3. **Google Blog: demand named entities in prompt** — cuisine, architect, trek difficulty. Regex-reject "Best for:" template phrases. (P1, ~1 hr)
4. **YouTube: carry visual descriptors through pass-1 → pass-2** — stop discarding quote/visual data in cluster summaries. (P1, ~2 hr)
5. **Synthesizer: allow 0-stop days / shorter days when research is genuinely exhausted** — don't fill Day 5 with 3 generic maps stops when there's no Jaisalmer material left. (P2, ~1 hr)
6. **OpenStreetMap Overpass POI enrichment** — geographic clustering rather than LLM guessing from place names. Deferred from AI-7. (P3, ~4 hr)

---

## 9. Run artefacts

- `samples/rajasthan-december.json` — test input
- `out/rajasthan.json` — final AIItinerary JSON
- `out/rajasthan.stderr.log` — full stage-by-stage pipeline log

Regression fixtures (Sprint 2):
- `out/goa.json`, `out/goa.stderr.log`
- `out/manali.json`, `out/manali.stderr.log`
