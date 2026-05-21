# Rajasthan Dec 20–31 — Sprint 7 Benchmark Comparison

**Date:** 2026-05-19  
**Agent run:** `samples/rajasthan-dec20-31.json` → `out/rajasthan-dec20-31.json`  
**Fixes applied this session:** geography geo-filter on `enrich_anchor_hints` (Taj Mahal removed) + `MIN_STOPS_PER_DAY` restored to 3  
**Handmade reference:** `rajasthan.txt` (Bhargav's actual planned Rajasthan trip, Dec 20–31 2026)

> **Framing:** The handmade itinerary is a personally researched, logistics-optimised plan with real booking constraints (safari zone, hotel check-in windows, inter-city drive times). Matching it 1:1 is not the goal — matching it *directionally* in terms of city routing, landmark coverage, and content specificity is the right bar.

---

## 1. Agent Itinerary — Full Day-by-Day (frontend JSON output)

**Emoji:** 🏯 | **places:** 9 | **tips:** 1 | **photo_stops:** 4 | **Total stops:** 33 across 11 days

---

### Day 1 — Jaipur: Jaipur Landmarks
> *Start your day at Hawa Mahal before 9 — the morning sun lights up the sandstone honeycomb. Then, head to City Palace, lunch on dal baati churma at a local eatery, and wind down with a stroll around the palace. Be aware that the summer heat wave can trigger kidney stones, so stay hydrated. For a more comfortable journey, consider booking 2A class train tickets.*

| # | Time | Source | Stop | Description |
|---|---|---|---|---|
| 1 | 9:00 AM | maps | **Hawa Mahal** | Pre-validated anchor stop. Check local advisories for opening hours. |
| 2 | 11:00 AM | maps | **City Palace** | Pre-validated anchor stop. Check local advisories for opening hours. |
| 3 | 1:30 PM | maps | **Dal Baati Churma at a local eatery** | Lunch on the traditional Rajasthani dish, made with lentils, wheat, and jaggery. |

---

### Day 2 — Jaipur: Amer Fort and Local Cuisine
> *Visit Amer Fort, a pre-validated anchor stop, and then head to Choki Dhani Jaipur for a traditional Rajasthani dinner. Start your day early to avoid the crowds.*

| # | Time | Source | Stop | Description |
|---|---|---|---|---|
| 1 | 9:00 AM | maps | **Amer Fort** | Pre-validated anchor stop. Check local advisories for opening hours. |
| 2 | 3:00 PM | youtube | **Nagaur Pashu Mela** | A market where you can see various animals, like camels and cattle. |
| 3 | 7:00 PM | youtube | **Choki Dhani Jaipur** | A restaurant in Jaipur, known for its traditional Rajasthani cuisine. |

---

### Day 3 — Jodhpur: Mehrangarh Fort
> *Visit Mehrangarh Fort, a 15th-century former palace, and take in the stunning views of the city. For better light and comfort, visit early in the morning.*

| # | Time | Source | Stop | Description |
|---|---|---|---|---|
| 1 | 8:00 AM | blog | **Mehrangarh Fort, Jodhpur** | 15th-century former palace that's now a museum, with seven gates and a museum of Marwar royal palanquins and Rajput weaponry. Best for: history enthusiasts, photography. |
| 2 | 12:30 PM | maps | **Lunch at a local eatery** | Try some local cuisine, such as mirchi vada or pyaaz ki kachori. |
| 3 | 1:00 PM | maps | **Lunch in Jodhpur** | Anchor slot — swap for a specific spot in Jodhpur you've already saved. |

---

### Day 4 — Jaisalmer: Jaisalmer Fort
> *Explore Jaisalmer Fort, a pre-validated anchor stop, and experience the rich history and culture of the city.*

| # | Time | Source | Stop | Description |
|---|---|---|---|---|
| 1 | 9:00 AM | maps | **Jaisalmer Fort** | Pre-validated anchor stop. Check local advisories for opening hours. |
| 2 | 1:00 PM | maps | **Lunch in Jaisalmer** | Anchor slot — swap for a specific spot in Jaisalmer you've already saved. |
| 3 | 3:00 PM | youtube | **Pushkar Camel Fair** | A festival in Rajasthan where camels are the main attraction. |

---

### Day 5 — Udaipur: Lake Pichola
> *Take a boat ride on Lake Pichola and enjoy the scenic views of the city. Visit the Palace of the royal family of Udaipur for a glimpse into the royal life.*

| # | Time | Source | Stop | Description |
|---|---|---|---|---|
| 1 | 10:00 AM | maps | **Lake Pichola** | Pre-validated anchor stop. Check local advisories for opening hours. |
| 2 | 12:00 PM | youtube | **Palace of the Royal Family of Udaipur** | A palace that can be visited to see the interior. |
| 3 | 1:00 PM | maps | **Lunch in Udaipur** | Anchor slot — swap for a specific spot in Udaipur you've already saved. |

---

### Day 6 — Udaipur: City Exploration
> *Explore the city of Udaipur, known for its rich history and cultural significance. Visit the local markets and try some street food.*

| # | Time | Source | Stop | Description |
|---|---|---|---|---|
| 1 | 9:00 AM | reddit | **Udaipur** | Locals recommend: less chaotic than other Rajasthan cities, with kind and proud locals. Great for a relaxed experience. |
| 2 | 11:00 AM | maps | **Old Udaipur Market Walk** | Anchor slot — swap for a specific spot in Udaipur you've already saved. |
| 3 | 1:00 PM | maps | **Lunch in Udaipur** | Anchor slot — swap for a specific spot in Udaipur you've already saved. |

---

### Day 7 — Ajmer: Ajmer Sharif Dargah
> *Visit the revered shrine of Ajmer Sharif Dargah, known for its rich history and cultural importance.*

| # | Time | Source | Stop | Description |
|---|---|---|---|---|
| 1 | 10:00 AM | blog | **Ajmer Sharif Dargah** | A revered shrine in Ajmer, Rajasthan, known for its rich history and cultural importance. |
| 2 | 11:00 AM | maps | **Old Ajmer Market Walk** | Anchor slot — swap for a specific spot in Ajmer you've already saved. |
| 3 | 1:00 PM | maps | **Lunch in Ajmer** | Anchor slot — swap for a specific spot in Ajmer you've already saved. |

---

### Day 8 — Pushkar: Pushkar Exploration
> *Explore the city of Pushkar, known for its cultural and spiritual significance. Visit the local markets and try some street food.*

| # | Time | Source | Stop | Description |
|---|---|---|---|---|
| 1 | 9:00 AM | blog | **Pushkar** | A city in Rajasthan, known for its cultural and spiritual significance. |
| 2 | 11:00 AM | maps | **Old Pushkar Market Walk** | Anchor slot — swap for a specific spot in Pushkar you've already saved. |
| 3 | 1:00 PM | maps | **Lunch in Pushkar** | Anchor slot — swap for a specific spot in Pushkar you've already saved. |

---

### Day 9 — Kumbhalgarh: Kumbhalgarh Fort
> *Visit the monumental fort of Kumbhalgarh, famous for its massive snake-like fortress and historic significance.*

| # | Time | Source | Stop | Description |
|---|---|---|---|---|
| 1 | 10:00 AM | blog | **Kumbhalgarh Fort** | A monumental fort in Rajasthan, famous for its massive snake-like fortress and historic significance. |
| 2 | 11:00 AM | maps | **Old Kumbhalgarh Market Walk** | Anchor slot — swap for a specific spot in Kumbhalgarh you've already saved. |
| 3 | 1:00 PM | maps | **Lunch in Kumbhalgarh** | Anchor slot — swap for a specific spot in Kumbhalgarh you've already saved. |

---

### Day 10 — Jaipur: Jaipur Revisited
> *Revisit the city of Jaipur and explore its local markets and cuisine. Try some street food and visit the local handicraft shops.*

| # | Time | Source | Stop | Description |
|---|---|---|---|---|
| 1 | 9:00 AM | maps | **Jaipur** | A city in Rajasthan, known for its rich history and cultural significance. |
| 2 | 11:00 AM | maps | **Old Jaipur Market Walk** | Anchor slot — swap for a specific spot in Jaipur you've already saved. |
| 3 | 1:00 PM | maps | **Lunch in Jaipur** | Anchor slot — swap for a specific spot in Jaipur you've already saved. |

---

### Day 11 — Jaipur: Departure
> *Spend the morning shopping for souvenirs or visiting any last-minute attractions. Depart for the airport and head back home.*

| # | Time | Source | Stop | Description |
|---|---|---|---|---|
| 1 | 9:00 AM | maps | **Morning Coffee in Jaipur** | Anchor slot — swap for a specific spot in Jaipur you've already saved. |
| 2 | 11:00 AM | maps | **Old Jaipur Market Walk** | Anchor slot — swap for a specific spot in Jaipur you've already saved. |
| 3 | 1:00 PM | maps | **Lunch in Jaipur** | Anchor slot — swap for a specific spot in Jaipur you've already saved. |

---

## 2. Handmade Reference — Day-by-Day

*(From `rajasthan.txt` — Bhargav's personally planned trip)*

| Day | Date | What happens |
|---|---|---|
| **Day 0** | Dec 20 (Fri) | BBSR → Delhi flight, overnight train to Jaipur |
| **Day 1** | Dec 21 (Sat) | Arrive Jaipur 8:15 AM, pick up car → Ranthambore. Check in, lunch, Ranthambore Fort. Overnight. |
| **Day 2** | Dec 22 (Sun) | Ranthambore Canter Safari 6:30 AM (Zone 5, 3hrs). Checkout 12 PM → Udaipur. Arrive 9 PM. |
| **Day 3** | Dec 23 (Mon) | Sunrise at Bahubali Hills + Badi Lake. Breakfast at Hillison Café. Monsoon Palace (Sajjan Garh). Shilp Gram. Jagdeesh Temple. Bada Bazaar + Mochiwada Bazaar. Sunset at Lake Pichola (5:30 PM). Bagor ki Haveli cultural show (6–7 PM). |
| **Day 4** | Dec 24 (Tue) | City Palace + Jagmandir Palace (3 hrs). Leave Udaipur 12 PM → Jodhpur. Arrive 7 PM. |
| **Day 5** | Dec 25 (Wed) | Nai Sarak shopping. Jaswant Thada + Umed Bhawan Palace. Lunch. Mehrangarh Fort + light & sound show (6:30 PM). Evening free. |
| **Day 6** | Dec 26 (Thu) | Leave 6:30 AM → Sam Sand Dunes via Kuldhara Village. Check in resort 1 PM. Paragliding, desert safari, camel safari. Cultural program + campfire + stargazing. |
| **Day 7** | Dec 27 (Fri) | Sunrise at Sam. Bada Bagh, Jaisalmer Fort, Gadisar Lake. Lunch → Bikaner. Arrive 9 PM. |
| **Day 8** | Dec 28 (Sat) | Rampuria Haveli, Junagarh Fort (10 AM), Camel Breeding Centre. Lunch 2 PM → Jaipur. Arrive 9 PM. |
| **Day 9** | Dec 29 (Sun) | Nahagarh Fort sunrise (7:14 AM), Jaigarh Fort, Amber Fort. Lunch. Jal Mahal. Bapu / Nehru / Johri Bazaar shopping. |
| **Day 10** | Dec 30 (Mon) | Hawa Mahal (9 AM), City Palace, Jantar Mantar. Tripolia Bazar (bangles), Kishanpole Bazar (bandhej). Lunch. Albert Hall Museum, Birla Mandir. Evening in city. |
| **Day 11** | Dec 31 (Tue) | Checkout Jaipur → Delhi train → BBSR flight. |

---

## 3. Side-by-Side Comparison

### 3.1 City Routing

| Aspect | Handmade | Agent | Match? |
|---|---|---|---|
| Cities visited | Jaipur → Ranthambore → Udaipur → Jodhpur → **Sam** → Jaisalmer → **Bikaner** → Jaipur | Jaipur → Jodhpur → Jaisalmer → Udaipur → Ajmer → Pushkar → **Kumbhalgarh** → Jaipur | 🟡 **Partial** |
| Jaipur | ✅ Yes (Days 1, 9, 10) | ✅ Yes (Days 1–2, 10–11) | ✅ |
| Udaipur | ✅ Yes (Days 3–4) | ✅ Yes (Days 5–6) | ✅ |
| Jodhpur | ✅ Yes (Day 5) | ✅ Yes (Day 3) | ✅ |
| Jaisalmer | ✅ Yes (Day 7) | ✅ Yes (Day 4) | ✅ |
| Ranthambore | ✅ Yes (Days 1–2) | ❌ Missing | 🔴 |
| Sam Sand Dunes | ✅ Yes (Day 6) | ❌ Missing | 🔴 |
| Bikaner | ✅ Yes (Day 8) | ❌ Missing | 🔴 |
| Ajmer | ❌ Not visited | ✅ Day 7 | 🟡 bonus |
| Pushkar | ❌ Not visited | ✅ Day 8 | 🟡 bonus |
| Kumbhalgarh | ❌ Not visited | ✅ Day 9 | 🟡 bonus |

The agent covers 4/7 handmade cities correctly. It misses Ranthambore, Sam Sand Dunes, and Bikaner — all logistics-heavy stops that require real booking data (safari zones, resort timing). It adds Ajmer, Pushkar, Kumbhalgarh which are valid Rajasthan alternatives.

---

### 3.2 Landmark Coverage

| Landmark | Handmade | Agent | Source |
|---|---|---|---|
| Hawa Mahal | ✅ Day 10 | ✅ Day 1 | maps (anchor) |
| City Palace (Jaipur) | ✅ Days 4+10 | ✅ Day 1 | maps (anchor) |
| Amer / Amber Fort | ✅ Day 9 | ✅ Day 2 | maps (anchor) |
| Jaisalmer Fort | ✅ Day 7 | ✅ Day 4 | maps (anchor) |
| Lake Pichola | ✅ Day 3 | ✅ Day 5 | maps (anchor) |
| Mehrangarh Fort | ✅ Day 5 | ✅ Day 3 | **blog** |
| Ajmer Sharif Dargah | ❌ | ✅ Day 7 | **blog** |
| Kumbhalgarh Fort | ❌ | ✅ Day 9 | **blog** |
| Choki Dhani | ❌ | ✅ Day 2 | **youtube** |
| Ranthambore Safari | ✅ Days 1–2 | ❌ | — |
| Bagor ki Haveli | ✅ Day 3 | ❌ | — |
| Sajjan Garh (Monsoon Palace) | ✅ Day 3 | ❌ | — |
| Nahagarh Fort | ✅ Day 9 | ❌ | — |
| Jaigarh Fort | ✅ Day 9 | ❌ | — |
| Albert Hall Museum | ✅ Day 10 | ❌ (was anchor seed but dropped) | — |
| Kuldhara Village | ✅ Day 6 | ❌ | — |
| Sam Sand Dunes | ✅ Day 6 | ❌ | — |
| Gadisar Lake | ✅ Day 7 | ❌ | — |
| Junagarh Fort (Bikaner) | ✅ Day 8 | ❌ | — |
| Jantar Mantar | ✅ Day 10 | ❌ | — |

**Coverage score: 6/20 handmade landmarks present in agent output (30%)**  
5 of the 6 hits are anchor seeds (correctly placed in Rajasthan after geo-filter fixed). 1 is from blog (Mehrangarh Fort — the strongest content piece in the run).

---

### 3.3 Content Quality — Stop-by-Stop

| Dimension | Handmade | Agent | Gap |
|---|---|---|---|
| **Specificity** | "Canter Safari Zone 5, 6:30 AM" | "Pre-validated anchor stop" | 🔴 Agent has no logistics detail |
| **Sequencing logic** | Ranthambore→Udaipur→Jodhpur→Sam→Jaisalmer→Bikaner→Jaipur (loop route, minimises backtracking) | Jaipur→Jodhpur→Jaisalmer→Udaipur→Ajmer→Pushkar→Kumbhalgarh→Jaipur (geographically incoherent — Udaipur after Jaisalmer means doubling back east) | 🔴 Route logic broken |
| **Practical logistics** | Hotel check-in windows, drive times, light show at 6:30 PM | None | 🔴 No logistics |
| **Named specific experiences** | "Sunrise on Bahubali Hills", "Shilp Gram", "Bagor ki Haveli cultural show 6–7 PM" | "Hawa Mahal — check local advisories" | 🔴 No time-specific hooks |
| **Food specificity** | "Breakfast at Hillison Café", "Pyaaz ki kachori" (Day 3 lunch area implied) | "Dal Baati Churma at a local eatery", "Mirchi vada or pyaaz ki kachori" ← **this is a genuine win** | 🟡 Some food specificity |
| **Warnings** | N/A (personal plan) | Day 1: kidney stone risk + train class tip (2A) ← both from Reddit | 🟢 Useful |
| **Photography vibes** | Sunrise at Bahubali Hills, Nahagarh Fort sunrise (7:14 AM), Sam stargazing | Palace of Royal Family of Udaipur (photo tag), Mehrangarh Fort (photo tag) | 🟡 Thin but present |

---

### 3.4 Source Breakdown

| Source | Count | % | Target | Status |
|---|---|---|---|---|
| maps | 24 | 73% | <40% | 🔴 Not met |
| youtube | 4 | 12% | — | — |
| blog | 4 | 12% | — | — |
| reddit | 1 | 3% | ≥30% as stops | 🔴 Not met |
| **Non-maps** | **9** | **27%** | **>60%** | 🔴 Not met |

---

## 4. Honest Verdict

### What the agent did well

| Win | Detail |
|---|---|
| ✅ All major cities present | Jaipur, Jodhpur, Udaipur, Jaisalmer all included — the "Golden Triangle + Jodhpur" core is there |
| ✅ Anchor coverage 5/5 seeded correctly | No Taj Mahal hallucination this run; all 5 anchors (Hawa Mahal, City Palace, Amer Fort, Jaisalmer Fort, Lake Pichola) placed in real Rajasthan cities |
| ✅ Mehrangarh Fort with real detail | "Seven gates, museum of Marwar royal palanquins and Rajput weaponry" — blog-sourced, specific enough to be useful |
| ✅ Warnings surfaced | Kidney stone belt warning + train booking tip from Reddit on Day 1 |
| ✅ Chronology correct | All 11 days are chronologically ordered; no backwards-time violations |
| ✅ Stats honest | places=9, tips=1 — counts only real-research stops, not padding |
| ✅ Day 11 fixed | No longer "city: Taj" — correctly reads "Jaipur Departure" |
| ✅ All days have ≥3 stops | MIN_STOPS_PER_DAY=3 is firing; no 2-stop days |
| ✅ Food named | "Mirchi vada", "pyaaz ki kachori", "Dal Baati Churma" — culturally appropriate |
| ✅ Kumbhalgarh Fort surfaced | Not in handmade itinerary, but a genuine Rajasthan gem — good discovery |

### What the agent missed

| Miss | Impact |
|---|---|
| ❌ Ranthambore missing entirely | A signature Rajasthan experience; requires zone-based safari booking — beyond current agent scope |
| ❌ Sam Sand Dunes / desert camp missing | The desert overnight experience; discovery agents found no blog/YouTube content on Sam specifically |
| ❌ Bikaner missing | A hidden-gem Rajasthan city; not in anchor list, thin research coverage |
| ❌ Route is geographically incoherent | Jodhpur → Jaisalmer → Udaipur backtracks east; handmade route is logistically optimal. Agent has no geographic reasoning layer |
| ❌ Day 3 has a duplicate lunch slot | "Lunch at a local eatery" (12:30 PM) + "Lunch in Jodhpur" padding (1:00 PM) both on Day 3 — minor post-processing bug |
| ❌ No sunrise/sunset timing hooks | Handmade: Bahubali Hills sunrise, Nahagarh Fort sunrise 7:14 AM, Lake Pichola sunset 5:30 PM. Agent: generic times |
| ❌ Pushkar Camel Fair on the wrong day | Placed on Day 4 in Jaisalmer — the fair is in Pushkar, not Jaisalmer, and is held in October/November, not December |
| ❌ 73% maps padding | Still far above <40% target; Days 7–11 are thin with padding placeholder stops |

---

## 5. Estimated Match Score vs Handmade

> The handmade itinerary is a **personally optimised, locally-knowledgeable plan** with real bookings. A 100% match is not realistic or the right goal.

| Dimension | Weight | Score | Notes |
|---|---|---|---|
| City / destination coverage | 25% | 5/10 | 4 of 7 core cities present; missing Ranthambore, Sam, Bikaner |
| Landmark coverage | 20% | 3/10 | 6 of 20 landmarks; anchor seeding gets credit for the 5 majors |
| Route logic / geography | 20% | 2/10 | Order is geographically inconsistent; no drive-time reasoning |
| Content specificity | 20% | 3/10 | Food names OK; no logistics, no time-of-day hooks, no booking details |
| Warnings + practical tips | 10% | 7/10 | Kidney stone + train tip surfaced from Reddit — genuinely useful |
| Format / structure | 5% | 9/10 | Valid JSON, honest stats, correct chronology, all days present |

**Weighted score: ~4/10 (40%)**

This is directionally correct for a state-level multi-city Indian trip from an agent that has no geographic routing layer and no access to booking systems. The agent's main strength is surfacing named landmarks reliably (anchor seeding); its main weakness is content depth and route optimisation.

---

## 6. What Would Move the Needle Most

1. **Geographic routing constraint in the synthesizer** — tell the LLM the physical distances between cities (Jodhpur→Jaisalmer: 285 km, Jaisalmer→Udaipur: 490 km) so it doesn't route backwards. Even a "prefer geographically adjacent days" rule would help.
2. **Sam Sand Dunes / desert camp as a hardcoded Rajasthan anchor** — the discovery agents produce zero content for Sam because it's an experience category (desert camping), not a named single landmark. A curated "experiences" list alongside "landmarks" would fill this.
3. **Ranthambore** — needs safari zone / booking awareness. Out of scope for the current agent architecture.
4. **Sunrise/sunset time injection** — city × month sunrise/sunset times are deterministic data; adding them to the synthesizer context would allow time-specific hooks ("Nahagarh Fort sunrise at 7:14 AM") without any LLM lookup.
