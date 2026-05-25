---
name: trip_shapes/region_multi_city
description: "Multi-city circuit planning, composed in when the destination spans several cities."
version: 1
---

=== Multi-city circuit ===
- This destination spans several cities. FIRST decide the city circuit and how many days each city gets, THEN write the days. Put the plan in `route_summary` (e.g. "Jaipur (3) → Jodhpur (2) → Jaisalmer (2) → Udaipur (2)").
- Order cities to MINIMISE backtracking — a roughly linear or loop route, never zig-zagging back to a city you've left.
- GROUP consecutive days in the same city. Major cities get 2–3 days; smaller stops get 1. Don't bounce between cities day-to-day.
- The first day in each city is arrival/settle + one light sight — don't cram a long travel day full of stops.
- Each day stays within ONE city/area unless it is explicitly a travel day (then name the leg, e.g. "overnight train to Jaisalmer").
