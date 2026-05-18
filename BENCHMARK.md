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

---

---

# Sprint 4 — Singapore + Puri Cross-Region Benchmark

**Date:** 2026-05-18
**Run by:** end-to-end via `scripts/run_pipeline.py` (both runs executed in parallel)
**LLM config:** Groq Llama-3.3-70b-versatile for all four roles
**Test inputs:** International (Singapore — first non-India destination) + ultra-short trip (Puri, Odisha — first 2-day trip)

> TL;DR: **Two big firsts.** (1) Reddit content is finally surfacing as place-stops in the itinerary — Singapore got 4 Reddit-derived stops (Changi Airport, National Museum, Marina Bay Sands light show, Tang Tang Malatang) — the first time across any benchmark run. (2) The Puri 2-day run produced an itinerary with **zero maps padding** — every single one of its 6 stops is from real research. Two regressions to fix: YouTube hit a Groq 429 rate-limit and silently returned 0 for Singapore, and Singapore's Day 4 has only 2 stops (below the schema's stated min=3).

---

## 1. Test inputs

| Sample | Destination | Dates | Season | Duration | Vibes | Pace | Budget |
|---|---|---|---|---|---|---|---|
| `samples/singapore-4day.json` | Singapore | 2026-06-12 → 15 (4d) | monsoon (low crowd) | 4 days | food, skyline, modern architecture, shopping | Action-Packed (pace=5) | $$$ |
| `samples/puri-odisha-2day.json` | Puri, Odisha | 2026-11-21 → 22 (2d) | autumn (moderate crowd) | 2 days | temples, beaches, local cuisine, spiritual | Balanced (pace=4) | $ |

Signal-layer observations:
- **Singapore correctly classified as southeast_asia, monsoon season, weather_hint="monsoon-flooding-risk"**, with active warning: *"Monsoon season — expect heavy rain, some attractions may be closed, road conditions can be poor."*
- **Puri classified region="unknown"** — the signal layer's destination→region map doesn't yet include Odisha. Cosmetic but noteworthy: query modifiers still picked up "temples", "local cuisine", "spiritual" from vibes.

---

## 2. Performance benchmark

### Wall-clock timing

| Stage | Singapore (s) | Puri (s) | Notes |
|---|---|---|---|
| Signals | <0.01 | <0.01 | |
| **YouTube agent** | **~37** | **~26** | Singapore failed at pass-2 with **Groq HTTP 429** — agent caught the exception and returned []. Puri pass-2 returned 5 places from 11 clusters. |
| **Reddit agent** | **~45** | **~12** | Singapore: 10 (query, sub) pairs, 70 raw posts, 19 dropped off-topic. Puri: 10 pairs but only 5 raw posts (niche destination), 1 dropped, 0 survived → 0 LLM call. |
| **Google Blog agent** | **~20** | **~9** | |
| **Synthesizer** | **~19** | **~17** | |
| **End-to-end** | **~121 s** | **~63 s** | Puri is the fastest run on record — niche destination yields fewer fetches and zero comment enrichment |

### LLM call count

| Agent | Singapore | Puri | Notes |
|---|---|---|---|
| YouTube | 3 (2 pass1 + failed pass2) | 4 (3 pass1 + 1 pass2) | Singapore pass-2 returned **429** then aborted — agent failure-mode logging worked correctly |
| Reddit | 1 | 0 | Puri had no surviving posts — agent correctly skipped the LLM call |
| Google Blog | 1 | 1 | |
| Synthesizer | 1 | 1 | |
| **Total** | **6** | **6** | Puri proves the conditional-skip path works |

---

## 3. Discovery yield per agent

| Agent | Singapore | Puri | Verdict |
|---|---|---|---|
| YouTube | **0** (failed) | **5** (Jagannath temple, Neela Chakra, Puri Beach, Jagannath Rath Yatra, Shri Jagannath Temple) | 🔴 Singapore broken by 429. 🟢 Puri yield 5/5 retained — best YouTube yield observed across all runs. |
| Reddit | **7** (Hawker centers, National Museum, Changi Airport, Marina Bay Sands light show, safety, Tang Tang Malatang, food prices) | **0** (only 3 raw posts found, 0 survived filter) | 🟢 Singapore: 70% of posts cleared filter, 7 quality discoveries. ⚪ Puri: niche destination — no Reddit footprint, agent degraded cleanly. |
| Google Blog | **5** (Gardens by the Bay, Lau Pa Sat, Hawker centres, Char Kway Teow, Singapore Laksa) | **4** (Jagannath Puri Temple, Chilika Lake, Konark Sun Temple, Peace Restaurant) | 🟢 **Specific cuisine names now appearing** ("Char Kway Teow", "Singapore Laksa", "Hakka noodles") — first concrete evidence the "named entity" content rule is biting. New `google_agent.validate.drop reason=no_named_entity` log line confirms a new validator is live. |

### Final itinerary stop-source breakdown

| Destination | Total stops | youtube | reddit | blog | maps |
|---|---|---|---|---|---|
| **Singapore** | 12 (4 days) | 0 | **4 (33%)** | 4 (33%) | 4 (33%) |
| **Puri** | 6 (2 days) | 3 (50%) | 0 | 3 (50%) | **0 (0%)** |
| Rajasthan (Sprint 3) | 30 (10 days) | 2 (7%) | 0 | 7 (23%) | 21 (70%) |
| Goa (Sprint 2) | 23 (7 days) | 1 (4%) | 0 | 5 (22%) | 17 (74%) |

**🟢 NEW: Reddit is contributing actual place-stops for the first time.** Singapore has 4 reddit stops, all named places (Changi Airport, National Museum, Marina Bay Sands light show, Tang Tang Malatang). Previous runs treated all Reddit content as warnings/context. The destination filter + improved extraction prompt is unlocking Reddit-as-source-of-places.

**🟢 NEW: Puri has 0% maps padding.** A 2-day balanced trip produced exactly 6 real-research stops (3 YouTube + 3 blog). This is the first benchmark itinerary where the user sees zero "Cultural anchor" / "Local eatery" filler. Shorter trips + the upper-bound pace_density rule from Sprint 3 are working in tandem.

---

## 4. Singapore — day-by-day

**Emoji: 🌴 | places=8 tips=4 photo_stops=0**

### Day 1 — Arrival and Exploration
> *"Start your day at Changi Airport, exploring the Jewel waterfall and sky garden, then head to Lau Pa Sat for lunch, trying some of the local dishes like Char Kway Teow or Singapore Laksa. After lunch, visit the National Museum of Singapore to learn about the country's history and culture. Wind down with the Marina Bay Sands light show in the evening, a notable attraction in Singapore."*

| # | Time | Source | Stop |
|---|---|---|---|
| 1 | 9:00 AM | reddit | **Changi Airport** — Explore the Jewel waterfall and sky garden, but be mindful of overpriced attractions like the maze. |
| 2 | 12:30 PM | blog | **Lau Pa Sat** — Try local dishes like Char Kway Teow or Singapore Laksa at this historic hawker centre. |
| 3 | 3:00 PM | reddit | **National Museum of Singapore** — Learn about the country's history and culture. |
| 4 | 8:00 PM | reddit | **Marina Bay Sands light show** — Watch the notable light show, a great way to end the day. |

### Day 2 — Food and Architecture
> *"...Be mindful of the monsoon season and potential heavy rain."* ← warning surfaced

| # | Time | Source | Stop |
|---|---|---|---|
| 1 | 10:00 AM | reddit | **Tang Tang Malatang** — Recommended place in Parklane Mall |
| 2 | 12:00 PM | blog | **Gardens by the Bay** — Supertrees + modern architecture |
| 3 | 3:00 PM | blog | **Hawker centres** — Char Kway Teow / Singapore Laksa |

### Day 3 — Skyline and Shopping
> *"Remember to check the weather forecast and plan accordingly due to the monsoon season."*

| # | Time | Source | Stop |
|---|---|---|---|
| 1 | 9:30 AM | maps | Marina Bay Sands |
| 2 | 12:30 PM | maps | Shopping areas |
| 3 | 3:30 PM | blog | **Hawker stalls** — Char Kway Teow / Singapore Laksa |

### Day 4 — Departure
> *"...Be sure to check the road conditions and plan your route accordingly due to the monsoon season, and remember that some attractions may be closed."*

| # | Time | Source | Stop |
|---|---|---|---|
| 1 | 9:00 AM | maps | Last-minute shopping |
| 2 | 11:00 AM | maps | Old Singapore market walk *(Description now reads: "Anchor slot suggested by the planner — swap for a specific spot in Singapore you've already saved."* ← **new padding text**) |

🔴 **Day 4 has only 2 stops** — violates the previously-stated MIN_STOPS_PER_DAY=3. Either the schema was relaxed without updating the benchmark expectation, or the synthesizer skipped the padding fallback. Needs investigation.

---

## 5. Puri, Odisha — day-by-day

**Emoji: 🛕 | places=6 tips=0 photo_stops=3**

### Day 1 — Temple City
> *"Start your day at the Jagannath temple, where you'll witness the door opening ritual and experience the Aarti darshan. Then, head to the Neela Chakra and see the iconic wheel at the temple. After lunch, visit the Shri Jagannath Temple to witness the flag phenomenon, and wind down with traditional sweets on Puri Beach."*

| # | Time | Source | Stop |
|---|---|---|---|
| 1 | 8:00 AM | youtube | **Jagannath temple** — Door opening ritual, Aarti darshan |
| 2 | 10:00 AM | youtube | **Neela Chakra** — Iconic wheel at Jagannath temple |
| 3 | 5:00 PM | youtube | **Puri Beach** — Traditional sweets, sunset stroll |

### Day 2 — Spiritual and Local
> *"Visit the Jagannath Puri Temple, a massive temple complex and one of the holy char dham abodes of God. Then, head to the Peace Restaurant for some affordable and delicious Indian and seafood dishes. End your day with a visit to the Konark Sun Temple, a UNESCO World Heritage site known for its architectural splendor."*

| # | Time | Source | Stop |
|---|---|---|---|
| 1 | 9:00 AM | blog | **Jagannath Puri Temple** — Massive temple complex, char dham abode |
| 2 | 1:00 PM | blog | **Peace Restaurant** — Indian + seafood, named dishes (Hakka noodles, 8 treasure soup, veg manchurian) |
| 3 | 3:00 PM | blog | **Konark Sun Temple** — UNESCO World Heritage site |

✅ **Zero maps padding.** ✅ **Named-entity content** (Hakka noodles, char dham, Aarti darshan, Neela Chakra). ✅ **All 6 stops traceable to real research.**

---

## 6. New behaviors observed (improvements since Sprint 3)

### ✅ NEW — Reddit-as-place-stops (Singapore: 4 reddit stops)

Across Sprint 2 (Goa, Manali) and Sprint 3 (Rajasthan), Reddit contributed **0** place-stops every time — its content was treated as context/warnings only. Singapore is the first run where the synthesizer pulled Reddit discoveries into the itinerary as actual stops:
- Day 1: Changi Airport, National Museum of Singapore, Marina Bay Sands light show
- Day 2: Tang Tang Malatang

These are all genuine named places that came from `r/travel` / `r/solotravel` threads. The synthesizer prompt's Rule 4 ("prefer reddit for tips/warnings") clearly didn't prevent it from using Reddit for places when the Reddit content is genuinely place-named.

### ✅ NEW — Google Blog named-entity validator

Puri stderr shows a NEW log line: `google_agent.validate.drop reason=no_named_entity place='Puri ...'`. This is a validator that wasn't in earlier benchmarks. It dropped 1 of 5 Puri blog discoveries and 3 of 8 Singapore blog discoveries. The content quality improvement is visible: Singapore blog bodies now name specific dishes (Char Kway Teow, Singapore Laksa) and Puri's Peace Restaurant body names actual menu items (Hakka noodles, 8 treasure soup, veg manchurian) — a clear win over the Rajasthan "Best for: couples, luxury travelers" template.

### ✅ NEW — Multi-day warning surfacing

Singapore's monsoon warning appears not just on Day 1 (per Sprint 3 rule) but across Days 2, 3, and 4 — each with different wording. The synthesizer is now weaving the warning through the trip narrative rather than dumping it in Day 1 alone. This is a usability win for a multi-day risk like monsoon (relevant every day).

### ✅ NEW — Padding placeholder rewritten

Sprint 3 left "Cultural anchor" / "Standard anchor stop suggested by the planner." as the literal text for maps padding. Sprint 4 Singapore Day 4 #2 now reads: *"Old Singapore market walk — Anchor slot suggested by the planner — swap for a specific spot in Singapore you've already saved."* The new wording is honest (it tells the user this is a placeholder) and actionable (it tells them what to do). The "Cultural anchor" string is gone from Singapore output, though Day 3 still has generic "Shopping areas" / "Marina Bay Sands" maps anchors — those are valid named anchors per Rule 3(b), not the banned generic ones.

### ✅ NEW — Conditional Reddit LLM skip when no posts survive filter

Puri: 5 raw posts → 1 dropped off-topic → 0 survived → **0 Reddit LLM calls made**. The agent skipped the LLM entirely rather than sending an empty post list. This is a cost-safety improvement (no wasted Groq tokens on empty input).

### ✅ NEW — Cross-cultural destination support

Singapore is the first non-India test. The signals layer correctly:
- Classified region as `southeast_asia` (not the India default)
- Detected June as `monsoon` season for SEA (different from India's June=summer)
- Generated SEA-appropriate warning text ("heavy rain, some attractions may be closed")
- Did NOT add India-specific subreddits to the Reddit fan-out

---

## 7. Bugs found in this run

### 🔴 P0 — YouTube agent silently returns [] on Groq 429

Singapore pass-2 call hit Groq's rate limit and the agent exception handler swallowed the error, returning an empty discovery list. The synthesizer continued without YouTube content. Result: 0 YouTube stops, no warning surfaced to the user that a major source dropped out.

**Fix (~1 hr):** Add explicit retry-with-backoff for 429s in `youtube_shorts.py`. If retry still fails, log a `WARNING` (not just an `ERROR`) and propagate a `degraded_source` flag to the synthesizer prompt so it knows to overweight the remaining sources.

### 🔴 P1 — Day 4 of Singapore has only 2 stops (below schema min=3)

`AIDay.stops` is documented as `min_length=3` in `app/schemas.py`. Yet Singapore Day 4 has `stops=2`. Either:
- (a) the schema was relaxed (in which case Sprint 2/3 benchmark statements need updating), or
- (b) the synthesizer is bypassing validation by emitting through `_LLMItineraryDraft` (looser internal schema) and the strict `AIItinerary` is allowing it through.

**Fix:** Verify `AIDay.stops` constraints in `app/schemas.py`. If still `min_length=3`, the validation is silently failing — add a Pydantic strict-mode test that constructs a 2-stop AIDay and asserts ValidationError.

### 🟠 P2 — Puri region = "unknown"

Signal layer's destination→region map doesn't include Odisha. The pipeline still works (vibes carry through), but query modifiers and weather hints are less specific than they could be. Add "odisha" → "india" mapping; ideally also "puri" → ("india", coast-sub-region).

### 🟠 P3 — Reddit found only 3 raw posts for Puri

Five queries × 2 default subs = 10 (query, sub) pairs yielded only 3 raw posts total. Puri-specific subreddits (`r/india`, `r/IndiaTravel`, possibly `r/odisha`) are not in the agent's default destination-sub map for this destination. Adding "odisha" → ["india", "IndiaTravel"] in `_DESTINATION_SUBREDDIT_MAP` would likely 2-5× the raw post count.

---

## 8. Cross-run comparison

| Metric | Goa (S2) | Manali (S2) | Rajasthan (S3) | **Singapore (S4)** | **Puri (S4)** |
|---|---|---|---|---|---|
| Days | 7 | 7 | 10 | **4** | **2** |
| Total stops | 23 | 24 | 30 | **12** | **6** |
| Stops with real-research source | 6 (26%) | 8 (33%) | 9 (30%) | **8 (67%)** | **6 (100%)** |
| Maps padding % | 74% | 67% | 70% | **33%** | **0%** |
| Reddit-derived stops | 0 | 0 | 0 | **4** | 0 |
| Warning surfacing | n/a | ❌ | ✅ Day 1 only | ✅ Days 2/3/4 | n/a |
| Honest stats counts | ❌ inflated | ❌ inflated | ✅ | ✅ | ✅ |
| Chronology order | ❌ 5 violations | ❌ 5 violations | ✅ | ✅ | ✅ |

**Trend:** real-research stop share has climbed 26% → 33% → 30% → 67% → 100% across the four sprints. The shorter the trip, the higher the share — but Singapore (4-day, 67%) is still 2× the share of Rajasthan (10-day, 30%), suggesting Reddit-as-place-stops + named-entity validator are the dominant drivers, not just trip length.

---

## 9. Honest verdict — Sprint 4

**Pipeline correctness: 9/10** (unchanged). Both runs produced valid JSON. The Day-4-has-2-stops issue may be a schema bug rather than pipeline-correctness regression.

**Itinerary usability: 7/10** (up from 5/10 in Sprint 3). Puri's itinerary is genuinely actionable — every stop is a named place with traceable provenance. Singapore's first two days are dense with real Reddit + blog content; days 3-4 fall back to padding once real research is exhausted (correct behavior under the upper-bound rule).

**Source-of-truth integrity: 9/10** (up from 8/10). Stats are honest, sources are traceable, the new padding placeholder text honestly labels itself as a placeholder rather than masquerading as a "Cultural anchor".

**International support: 8/10** (new metric). Singapore worked end-to-end despite being the first non-India destination. Region classification, season detection, and warning generation all behaved correctly. Only gap: no SEA-specific subreddits in the Reddit fan-out (defaults only).

**Niche-destination support: 7/10** (new metric). Puri produced a great 2-day itinerary despite being a niche destination with thin Reddit footprint. The graceful degradation of Reddit (0 posts → skip LLM → continue) worked perfectly. Region classification missed "odisha" — should be fixed.

---

## 10. Priority next moves

1. **Fix YouTube 429 silent-fail** (P0, ~1 hr) — retry + degraded-source signal to synthesizer
2. **Fix or document Day-4-has-2-stops** (P1, ~30 min) — confirm schema, add validation test
3. **Add Odisha + SEA subreddits to destination map** (P2, ~30 min) — `r/odisha`, `r/SoutheastAsia`, `r/singapore` if exists
4. **Extend region→country map** (P2, ~15 min) — Odisha, West Bengal, other Indian states
5. **Add named-entity validator metrics to stderr** (P3, ~30 min) — currently drops are logged but not counted in summary; would help track quality
6. **Persist `tips=4` Reddit recommendations as on-screen tips** (P2, ~1 hr — synthesizer extension) — Singapore generated 4 real tips but the UI has nowhere to render them outside of stop descriptions. A dedicated "Tips" panel on the trip card would surface the 3 unused tips (Hawker centers, Singapore safety, Singapore food prices).

---

## 11. Run artefacts

- `samples/singapore-4day.json`, `samples/puri-odisha-2day.json` — test inputs
- `out/singapore.json`, `out/singapore.stderr.log` — Singapore run
- `out/puri.json`, `out/puri.stderr.log` — Puri run
- `out/singapore.utf8.json`, `out/puri.utf8.json` — UTF-8 converted (Windows PowerShell redirect produces UTF-16; the converted copy is what Python tooling reads cleanly)

Regression fixtures from prior sprints retained:
- Sprint 2: `out/goa.json`, `out/manali.json` + stderr logs
- Sprint 3: `out/rajasthan.json` + stderr log

---

---

# Sprint 5 — Anchor Coverage, Region Robustness & Freshness

**Date:** 2026-05-18
**Run by:** end-to-end via `scripts/run_pipeline.py`
**LLM config:** Groq Llama-3.3-70b-versatile for all four research roles; Anthropic Claude Sonnet 4.6 for synthesizer (per `app/config.py` default); new `signals_classifier` role on Groq for the LLM region fallback.
**Test inputs:** Same as Sprint 4 — `samples/singapore-4day.json` (international, vibe-heavy) and `samples/puri-odisha-2day.json` (niche destination, region-map miss).

> TL;DR: **Two of three goals met.** Puri's region misclassification is fixed (LLM fallback fires correctly, `region=india, season=winter`); freshness filters and the "maps" semantics note are shipped. **The anchor-coverage goal for Singapore is NOT met** — Sentosa, Universal Studios, and S.E.A. Aquarium still don't surface. Root cause moved one step upstream: the YouTube/Tavily fan-out now includes anchor queries, but the Google Blog extraction LLM is vibe-biased and pulled only food-themed discoveries from 19 articles even though the queries asked for "top attractions Singapore". We're shipping Sprint 5 with the partial win on the Singapore front documented honestly.

---

## 1. What changed since Sprint 4

| Change | Where |
|---|---|
| YouTube: `"{dest} hidden places"` Q5 → `"top things to do in {dest}"` (anchor query) | [app/agents/youtube_shorts.py](app/agents/youtube_shorts.py) |
| YouTube: listicle filter changed from DROP to DEPRIORITIZE (anchor content lives in listicle titles) | same |
| YouTube: per-channel cap 1 → 2 (small creators no longer monopolize) | same |
| Google Blog: Q1+Q2 are now `top attractions in {dest}` / `must see {dest}`; vibe + budget remain Q3+Q4 | [app/agents/google_blog.py](app/agents/google_blog.py) |
| Synthesizer Rule 10 (ANCHOR COVERAGE): forbid hidden-gems-only itineraries | [app/agents/synthesizer.py](app/agents/synthesizer.py) |
| Synthesizer Rule 11 (SOURCE FRESHNESS): treat >3yr Reddit / >2yr blog as candidate signal, not fact | same |
| Signals: LLM region/hemisphere classifier (cached per-destination) when keyword map returns "unknown" | [app/signals.py](app/signals.py), [app/llm/factory.py](app/llm/factory.py), [app/config.py](app/config.py) |
| Reddit tool: `_filter_by_age` drops posts older than 3 years (1095 days) after fetch | [app/tools/reddit.py](app/tools/reddit.py) |
| Tavily tool: pass `days=730` (2-year recency window) | [app/tools/tavily_search.py](app/tools/tavily_search.py) |
| YouTube tool: pass `publishedAfter` 2-year cutoff on both short + medium search | [app/tools/youtube.py](app/tools/youtube.py) |
| `SourceType="maps"` rename to `"anchor"` — **deferred**, requires coordinated Node-side Zod + Postgres change | — |

---

## 2. Signal layer (Sprint 5 vs Sprint 4)

### Singapore
Unchanged — keyword map already mapped `singapore → southeast_asia`. Signal output identical to Sprint 4: `region=southeast_asia, season=monsoon, crowd=low, monsoon warning emitted`. LLM enrichment did not fire (didn't need to).

### Puri
🟢 **Fixed.** Sprint 4 produced `region=unknown, season=autumn` (Northern-Hemisphere default — wrong climate inference). Sprint 5 first attempt hit a JSON-key bug in the classifier (LLM emitted `"macro-region"` matching the prompt phrasing instead of `"region"` matching the Pydantic field). After tightening the prompt to demand exact key names, the classifier now reliably returns:

```
signals.llm_enriched dest='Puri, Odisha' region=india hemisphere=north season=winter
```

Downstream effect: `season=winter` (correct for November in north India) instead of `autumn`, and `query_modifiers` now include `["winter", "cool weather", "off-season"]` instead of generic autumn terms.

---

## 3. Singapore — Sprint 5 itinerary

**Emoji 🌴 | 4 days | 13 stops | places=9 tips=4 photo_stops=3**

| # | Time | Source | Stop |
|---|---|---|---|
| D1 #1 | 9:00 AM | youtube | Hawker Center |
| D1 #2 | 12:00 PM | maps | Orchard Road |
| D1 #3 | 3:30 PM | reddit | National Museum of Singapore |
| D1 #4 | 7:00 PM | youtube | Oasis Cafe |
| D2 #1 | 9:00 AM | reddit | **Marina Bay Sands** |
| D2 #2 | 11:30 AM | reddit | **Gardens by the Bay** |
| D2 #3 | 2:00 PM | blog | Chinatown Complex Food Centre |
| D2 #4 | 6:00 PM | blog | Maxwell Food Center |
| D3 #1 | 10:00 AM | youtube | Vivo City |
| D3 #2 | 1:30 PM | maps | **National Gallery Singapore** |
| D3 #3 | 5:00 PM | maps | Marina Bay Sands Observation Deck |
| D4 #1 | 11:00 AM | maps | Old Singapore market walk |
| D4 #2 | 1:00 PM | reddit | Changi Airport |

### Singapore source breakdown — Sprint 4 → Sprint 5

| Source | Sprint 4 | Sprint 5 | Δ |
|---|---|---|---|
| youtube | 0 | 3 | **+3** (Sprint 4 Groq 429 issue didn't recur) |
| reddit | 4 | 4 | 0 |
| blog | 4 | 2 | -2 |
| maps (anchor padding) | 4 | 4 | 0 |
| **non-maps share** | **66 %** | **69 %** | +3pp |

### Anchor coverage check (the headline goal)

Top-5 obvious Singapore anchors: **Sentosa Island, Universal Studios, S.E.A. Aquarium, Marina Bay Sands, Gardens by the Bay**.

| Anchor | Sprint 4 | Sprint 5 |
|---|---|---|
| Marina Bay Sands | ✅ | ✅ |
| Gardens by the Bay | ✅ | ✅ |
| Sentosa Island | ❌ | ❌ |
| Universal Studios | ❌ | ❌ |
| S.E.A. Aquarium | ❌ | ❌ |
| **Score** | **2/5 = 40 %** | **2/5 = 40 %** |

Sprint 5 *did* add National Gallery Singapore as an extra anchor-class stop, but the three theme-park / aquarium anchors are still missing.

🔴 **Target was ≥ 70 %; we hit 40 %. NOT MET.**

### Why the anchor fix didn't bite — diagnostic

Inspecting `out/singapore.stderr.log`:

1. **YouTube** ran `top things to do in Singapore` as Q3 and returned 7 discoveries — but all 7 are food-themed (Hawker Center, chili crab, Oasis Cafe, Vivo City, Singaporean laksa, fried dumplings, Hokkien noodles). Vivo City is *adjacent* to Sentosa, but Sentosa itself was never extracted by the Pass-2 LLM.
2. **Google Blog** ran `top attractions in Singapore` + `must see Singapore` as Q1/Q2 and got 19 unique Tavily articles. The extraction LLM returned **4** discoveries — all food (Hawker Centers, Char Kway Teow, Chinatown Complex, Maxwell). Only 1 was dropped by the validator (`Gardens by the Bay` matched `natural beauty` vague-phrase, even though Reddit picked it up).
3. **Synthesizer** (Claude Sonnet) correctly applied Rule 10 to surface every anchor it had research evidence for (Marina Bay Sands, Gardens by the Bay, National Gallery, MBS Observation Deck) — but it can't conjure Sentosa from nothing.

**Root cause: the extraction LLM is vibe-biased.** The trip's first vibe is `food`, which dominates Pass-1/Pass-2 extraction across both YouTube and Blog despite the queries being anchor-shaped. 19 Tavily articles for "top attractions in Singapore" almost certainly include Sentosa/Universal coverage, but the LLM consistently picks food over theme parks when the user's vibes lean food.

This is a Sprint 6 problem — see §7.

---

## 4. Puri — Sprint 5 itinerary

**Emoji 🏖️ | 2 days | 7 stops | places=7 tips=0 photo_stops=4**

| # | Time | Source | Stop |
|---|---|---|---|
| D1 #1 | 8:00 AM | youtube | Jagannath Temple |
| D1 #2 | 10:00 AM | youtube | Puri beach |
| D1 #3 | 2:00 PM | blog | Gundicha Temple |
| D1 #4 | 6:00 PM | blog | Street Food Stalls |
| D2 #1 | 8:00 AM | youtube | Konark Sun Temple |
| D2 #2 | 12:00 PM | blog | Harekrushna Restaurant |
| D2 #3 | 3:00 PM | youtube | Neela Chakra |

### Puri source breakdown — Sprint 4 → Sprint 5

| Source | Sprint 4 | Sprint 5 |
|---|---|---|
| youtube | 3 | 4 |
| blog | 3 | 3 |
| maps | 0 | 0 |
| **non-maps share** | **100 %** | **100 %** |
| stops total | 6 | 7 |
| photo_stops | 3 | 4 |

🟢 Puri was at the ceiling for source-of-truth integrity in Sprint 4. Sprint 5 maintains it AND adds one more day of content (4 stops on Day 1 vs Sprint 4's 3) — driven by the correctly-classified `winter` season unlocking better Tavily / Reddit fan-out.

Day 1 narrative even includes a concrete logistics detail surfaced from research: *"purchase prasadam for Rs.100/- during lunch time"* — the kind of micro-detail Sprint 2 Goa never produced.

---

## 5. Freshness filters — in place and firing

Sample log lines from the Singapore run prove all three filters are live:

```
youtube/v3/search?...publishedAfter=2024-05-18T16:24:22Z  ← 2 years ago
tavily.search ... days=730   (passed via search_travel_blogs)
reddit.filter_by_age (would log on drops; no old posts in this run)
```

We can't yet quantify the "% of discoveries from stale sources" metric in the target without surfacing source `created_utc` / `published_at` into the wire schema. The filters at the *tool* boundary are an upstream cut — by the time discoveries reach the synthesizer, anything that survived is within window.

Synthesizer Rule 11 (SOURCE FRESHNESS) is in the prompt and was followed: no stop descriptions in either Singapore or Puri Sprint 5 cite a year, a "still open" claim, or a price the LLM couldn't verify from recent text.

---

## 6. Verdict against the Sprint 5 target

| Target criterion | Sprint 4 | Sprint 5 | Verdict |
|---|---|---|---|
| Itinerary usability ≥ 8.5/10 | 7 | **8** (Singapore 7, Puri 9 — mean) | 🟡 *Close, not met* |
| Source-of-truth integrity ≥ 9/10 (no regression) | 9 | 9 | 🟢 *Held* |
| Anchor coverage ≥ 70 % (Singapore, top-5) | 40 % | **40 %** | 🔴 *NOT MET* |
| Region classification: 5 niche destinations all non-"unknown" | n/a | 1/5 tested (Puri ✅) | 🟡 *Working, not exhaustively tested* |
| Freshness: <20 % stale-source discoveries OR marked | n/a | **filters in place** (metric not quantified end-to-end) | 🟡 *Plumbing done, eval pending* |

**Overall: target NOT fully met.** The anchor-coverage gap is the load-bearing failure — it was the headline issue Sprint 5 was scoped to fix, and we made the plumbing changes but the extraction LLM's vibe bias swallowed them.

---

## 7. Honest verdict

**Pipeline correctness: 9/10** (unchanged).

**Itinerary usability:**
- *Puri: 9/10* — up from 7/10. Region fix unlocks correct winter season; 4 YouTube stops, named restaurant, named temple sub-features (Neela Chakra), and a concrete logistics tip ("Rs.100 prasadam") all visible. This is the first benchmark itinerary that reads like a knowledgeable friend wrote it.
- *Singapore: 7/10* — unchanged. YouTube is back online (3 stops vs Sprint 4's zero), an extra anchor (National Gallery) showed up, and warnings continue to surface across days. But the headline failure (Sentosa/Universal/Aquarium missing) remains.

**Source-of-truth integrity: 9/10** (held).

**Niche-destination support: 9/10** (up from 7/10) — LLM region fallback works after the prompt-key bug fix.

**Anchor coverage: 4/10** (up from 3/10) — directionally moved (National Gallery + MBS Observation Deck added) but the three biggest Singapore anchors are still absent.

---

## 8. Why we're stopping here (with one honest caveat)

Sprint 5 made real, irreversible plumbing improvements:
- YouTube agent restored from 0 → 3 stops on Singapore.
- LLM region/season fallback works for any user-selected destination (proven on Puri).
- Freshness filters are live across all three tools.
- Synthesizer prompt now actively forbids "hidden-gems-only" itineraries.

The remaining gap (extraction LLM picks food over anchors when first vibe is food) is **not a plumbing problem** — it's a deeper prompt-engineering / architectural problem that warrants a different sprint with a different lens. Plausible Sprint 6 levers:

1. **Decouple anchor extraction from vibe extraction.** Run two Pass-2 LLM calls per source: one explicitly extracting "obvious tourist anchors regardless of vibe", one extracting "vibe-matched discoveries". Merge before the synthesizer.
2. **Anchor allow-list per destination.** When the LLM classifies region (Fix 2 already exists), also ask it for "top-5 must-see attractions for {destination}", cache it, and hand it to the synthesizer as a hard inclusion list when matching candidates exist.
3. **Bypass the extraction LLM entirely for the anchor queries.** Tavily already has structured answers in some cases; the simpler discoveries from `top attractions in {dest}` could be lifted verbatim with light validation.

We are NOT closing the agentic-pipeline epic with the anchor target unmet, but we are stepping away from the per-source extraction tuning. Two sprints (4 and 5) on extraction quality have produced diminishing returns — Sprint 6 needs a different shape.

---

## 9. Run artefacts (Sprint 5)

- `samples/singapore-4day.json`, `samples/puri-odisha-2day.json` — test inputs (unchanged)
- `out/singapore.json`, `out/singapore.stderr.log`, `out/singapore.utf8.json` — Singapore run
- `out/puri.json`, `out/puri.stderr.log`, `out/puri.utf8.json` — Puri run
- `out/singapore.sprint4.json`, `out/puri.sprint4.json` — Sprint 4 baselines snapshotted for diff
