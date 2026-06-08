# Nomad Agent — DB & Wire Contract

> Loaded when working on `app/db/**`, `app/schemas.py`, or `app/routes/**`.

This service shares a Supabase Postgres database with `nomad-api`. The Node side defines the schema in `prisma/schema.prisma`; we **read and write** it without owning it. Mismatches are silent — a typo in a column name causes Supabase to return 400 with the bad row dropped, and the trip ends up half-written.

## Source of truth

Authoritative schema: [`nomad-api/prisma/schema.prisma`](../../../nomad-api/prisma/schema.prisma). When a Prisma model changes there, [app/db/supabase_writer.py](../../app/db/supabase_writer.py) and [app/schemas.py](../../app/schemas.py) here may need to follow.

## Tables this service writes to

```
research_jobs         status / phase / progress / message / discoveries / stats / error / timestamps
itinerary_days        per-day rows: trip_id, day_number, city, title, description, highlights, stop_count
stops                 per-stop rows: day_id, trip_id, sort_order, time, ampm, duration, name, description, source, tags, locked
trips                 status / stats_places / stats_tips / stats_photo_stops / route_summary / transport_strategy / seasonal_tips / stay_by_city / budget_estimate (we set status='ready' on success)
```

Column names are **snake_case** in Postgres. Pydantic field names in `AIDay` / `AIStop` are **camelCase** (`dayNumber`, `sortOrder`, `ampm`) — they get translated by `supabase_writer.py` when writing. Don't change either side without changing both.

## Idempotency

Writing an itinerary for a `trip_id` that already has days should **delete existing days+stops first** (cascade delete in the schema covers stops). Re-running the pipeline must not produce duplicate rows.

## Phase / status transitions

`research_jobs.status`:
```
pending → researching → building → completed
                                 ↘
                                  failed
```

The route's background task (`_run_and_persist` in [app/routes/research.py](../../app/routes/research.py)) drives these transitions. Per-agent progress updates (after each research agent finishes) are encouraged so the frontend ticker reflects real progress.

## Wire contract with Node

### Inbound: `POST /agent/research`

```http
POST http://localhost:8000/agent/research
Authorization: Bearer <INTERNAL_AGENT_SECRET>
Content-Type: application/json

{
  "trip_id": "uuid",
  "user_id": "uuid",
  "destination": "Goa, India",
  "date_from": "2026-06-15",
  "date_to": "2026-06-22",
  "duration_days": 7,
  "travelers": "2",
  "vibes": ["beaches", "hidden gems"],
  "accommodation": "Airbnb / Homestay",
  "pace": "Balanced",
  "budget": "$$",
  "preferences": null
}
```

Validated by `TripParams` ([app/schemas.py](../../app/schemas.py)). Mirror of the Zod schema in `nomad-api`'s research route.

Response: `202 Accepted` with `{"accepted": true, "trip_id": "..."}`. Pipeline runs in the background. **Never return the itinerary inline** — Node polls Supabase.

### Outbound: nothing

This service makes **no HTTP calls back to Node**. All cross-service communication is mediated by the database. If you find yourself wanting to call Node, the answer is "write to Supabase and let Node's polling endpoint surface it."

## Internal auth

[app/auth.py](../../app/auth.py) checks `Authorization: Bearer <INTERNAL_AGENT_SECRET>` on every non-health request. The secret is shared with Node via env (`INTERNAL_AGENT_SECRET` on both sides).

This is **not** user JWT verification — that happens on the Node side. By the time a request reaches us, Node has already verified ownership.

## Pydantic ↔ Zod parity

| Python (`app/schemas.py`) | Node (Zod, in `nomad-api/src/...`) |
|---|---|
| `TripParams` | inbound research request schema |
| `ResearchDiscovery` | discovery rows in `research_jobs.discoveries` (jsonb) |
| `AIStop` | mapped to `stops` table by writer |
| `AIDay` | mapped to `itinerary_days` |
| `AIItinerary` | top-level synthesizer output |

When changing any field name, length constraint, or enum value:
1. Edit `app/schemas.py` here.
2. Edit the corresponding Zod schema in `nomad-api`.
3. If the field maps to a column, run `npx prisma db push` from `nomad-api`.
4. If nullable changes, also update `supabase_writer.py` to handle the new shape.

## SourceType literal

`SourceType = Literal["youtube", "reddit", "blog", "maps"]` in `app/schemas.py`.

This is also enforced by the Postgres column (likely a check constraint or text). Any new source means coordinated changes:
- Add to `SourceType` here.
- Add to the Zod literal in `nomad-api`.
- Update DB constraint if present.
- Update synthesizer prompt to mention it.

## supabase-py is sync

`from supabase import create_client` returns a sync `Client`. Always wrap calls in `await asyncio.to_thread(...)` so we don't block the event loop. `app/db/supabase_writer.py` already does this everywhere — follow the same pattern.

## Don't

- Don't query auth/user tables — that's Node's domain.
- Don't generate UUIDs for `trip_id` or `user_id` — they come from Node.
- Don't write to `trips.status` except to set `'ready'` on success or via `mark_trip_failed`.
- Don't add SELECT-and-modify race conditions — prefer single upsert/update operations.
