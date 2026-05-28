# Graph Report - .  (2026-05-28)

## Corpus Check
- 96 files · ~81,335 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1321 nodes · 3312 edges · 94 communities (78 shown, 16 thin omitted)
- Extraction: 83% EXTRACTED · 17% INFERRED · 0% AMBIGUOUS · INFERRED: 560 edges (avg confidence: 0.54)
- Token cost: 12,500 input · 4,200 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Reddit Agent Core|Reddit Agent Core]]
- [[_COMMUNITY_Signals & Personalization|Signals & Personalization]]
- [[_COMMUNITY_Synthesizer Core|Synthesizer Core]]
- [[_COMMUNITY_Reddit Tool & Primitives|Reddit Tool & Primitives]]
- [[_COMMUNITY_Blog & Anchor Agent|Blog & Anchor Agent]]
- [[_COMMUNITY_Synthesizer Helpers|Synthesizer Helpers]]
- [[_COMMUNITY_YouTube Shorts Agent|YouTube Shorts Agent]]
- [[_COMMUNITY_Skills Loader System|Skills Loader System]]
- [[_COMMUNITY_Data Schemas|Data Schemas]]
- [[_COMMUNITY_DB Writer & Cache|DB Writer & Cache]]
- [[_COMMUNITY_LangGraph Pipeline|LangGraph Pipeline]]
- [[_COMMUNITY_YouTube Longform Agent|YouTube Longform Agent]]
- [[_COMMUNITY_YouTube API Tool|YouTube API Tool]]
- [[_COMMUNITY_Agent Test Fixtures|Agent Test Fixtures]]
- [[_COMMUNITY_Solar & Timezone Calc|Solar & Timezone Calc]]
- [[_COMMUNITY_Eval & Scoring|Eval & Scoring]]
- [[_COMMUNITY_Place Mention Clustering|Place Mention Clustering]]
- [[_COMMUNITY_YouTube Tool Tests|YouTube Tool Tests]]
- [[_COMMUNITY_Internal Auth|Internal Auth]]
- [[_COMMUNITY_Video Quality Filters|Video Quality Filters]]
- [[_COMMUNITY_Longform Extraction Pipeline|Longform Extraction Pipeline]]
- [[_COMMUNITY_YouTube Query Builder|YouTube Query Builder]]
- [[_COMMUNITY_Observability & Tracing|Observability & Tracing]]
- [[_COMMUNITY_Pipeline Build|Pipeline Build]]
- [[_COMMUNITY_Cache Layer|Cache Layer]]
- [[_COMMUNITY_LLM Factory|LLM Factory]]
- [[_COMMUNITY_Synthesizer Fallback|Synthesizer Fallback]]
- [[_COMMUNITY_Video Quality Filter 2|Video Quality Filter 2]]
- [[_COMMUNITY_Claude Settings Hooks|Claude Settings Hooks]]
- [[_COMMUNITY_Sample Trip Params|Sample Trip Params]]
- [[_COMMUNITY_Geo Distance Module|Geo Distance Module]]
- [[_COMMUNITY_Architecture Governance|Architecture Governance]]
- [[_COMMUNITY_Trip Fixture Data|Trip Fixture Data]]
- [[_COMMUNITY_Trip Fixture Data|Trip Fixture Data]]
- [[_COMMUNITY_Trip Fixture Data|Trip Fixture Data]]
- [[_COMMUNITY_Trip Fixture Data|Trip Fixture Data]]
- [[_COMMUNITY_Trip Fixture Data|Trip Fixture Data]]
- [[_COMMUNITY_Trip Fixture Data|Trip Fixture Data]]
- [[_COMMUNITY_Trip Fixture Data|Trip Fixture Data]]
- [[_COMMUNITY_Trip Fixture Data|Trip Fixture Data]]
- [[_COMMUNITY_Trip Fixture Data|Trip Fixture Data]]
- [[_COMMUNITY_GeoBrief Data Model|GeoBrief Data Model]]
- [[_COMMUNITY_CICD Pipeline|CI/CD Pipeline]]
- [[_COMMUNITY_Geo & Cache Architecture|Geo & Cache Architecture]]
- [[_COMMUNITY_Longform Agent Flow|Longform Agent Flow]]
- [[_COMMUNITY_Personalization Gap|Personalization Gap]]
- [[_COMMUNITY_Research Cache Integration|Research Cache Integration]]
- [[_COMMUNITY_Content Quality Rules|Content Quality Rules]]
- [[_COMMUNITY_Benchmark Sprints|Benchmark Sprints]]
- [[_COMMUNITY_DB Governance|DB Governance]]
- [[_COMMUNITY_System Architecture Design|System Architecture Design]]
- [[_COMMUNITY_Discovery Validation|Discovery Validation]]
- [[_COMMUNITY_YouTube Data Types|YouTube Data Types]]
- [[_COMMUNITY_Geo Module|Geo Module]]
- [[_COMMUNITY_YouTube Discovery Output|YouTube Discovery Output]]
- [[_COMMUNITY_YouTube Local Dev|YouTube Local Dev]]
- [[_COMMUNITY_LLM Role Config|LLM Role Config]]
- [[_COMMUNITY_Draft to Itinerary|Draft to Itinerary]]
- [[_COMMUNITY_Geo Planning|Geo Planning]]
- [[_COMMUNITY_Geocode Tool|Geocode Tool]]
- [[_COMMUNITY_YouTube Agent Tests|YouTube Agent Tests]]
- [[_COMMUNITY_Reddit Local Dev|Reddit Local Dev]]
- [[_COMMUNITY_Agent Base Utilities|Agent Base Utilities]]
- [[_COMMUNITY_Claude Hooks Config|Claude Hooks Config]]
- [[_COMMUNITY_FastAPI App Entry|FastAPI App Entry]]
- [[_COMMUNITY_App Configuration|App Configuration]]
- [[_COMMUNITY_Skill Overlay Dispatch|Skill Overlay Dispatch]]
- [[_COMMUNITY_Architecture Analysis|Architecture Analysis]]
- [[_COMMUNITY_Quality Milestones|Quality Milestones]]
- [[_COMMUNITY_Project Settings|Project Settings]]
- [[_COMMUNITY_Local Dev Settings|Local Dev Settings]]
- [[_COMMUNITY_Health Check Endpoint|Health Check Endpoint]]
- [[_COMMUNITY_AIStop Schema|AIStop Schema]]
- [[_COMMUNITY_AIDay Schema|AIDay Schema]]
- [[_COMMUNITY_SourceType Enum|SourceType Enum]]
- [[_COMMUNITY_GeoLeg Model|GeoLeg Model]]
- [[_COMMUNITY_SkillError Exception|SkillError Exception]]
- [[_COMMUNITY_RedditPost Dataclass|RedditPost Dataclass]]
- [[_COMMUNITY_TavilyResult Dataclass|TavilyResult Dataclass]]
- [[_COMMUNITY_Sample Trip Puri|Sample Trip Puri]]
- [[_COMMUNITY_Sample Trip Singapore|Sample Trip Singapore]]
- [[_COMMUNITY_Functional Requirements|Functional Requirements]]
- [[_COMMUNITY_Architecture Trade-offs|Architecture Trade-offs]]
- [[_COMMUNITY_Scaling & Ops|Scaling & Ops]]
- [[_COMMUNITY_Pipeline Topology|Pipeline Topology]]

## God Nodes (most connected - your core abstractions)
1. `TripParams` - 186 edges
2. `ResearchDiscovery` - 141 edges
3. `extract_signals()` - 95 edges
4. `TravelSignals` - 94 edges
5. `AIItinerary` - 58 edges
6. `AIDay` - 50 edges
7. `AIStop` - 49 edges
8. `YouTubeShort` - 44 edges
9. `app/schemas.py` - 43 edges
10. `_llm_draft_to_itinerary()` - 35 edges

## Surprising Connections (you probably didn't know these)
- `Extraction LLM Vibe Bias Problem` --rationale_for--> `app/agents/youtube_shorts.py`  [INFERRED]
  BENCHMARK.md → app/agents/youtube_shorts.py
- `Sprint 7 Benchmark (Anchor Architecture Fix)` --references--> `app/geo`  [INFERRED]
  BENCHMARK.md → app/geo/__init__.py
- `Anchor Seeding Architecture` --rationale_for--> `app/graph/pipeline.py`  [INFERRED]
  BENCHMARK.md → app/graph/pipeline.py
- `str` --uses--> `TripParams`  [INFERRED]
  scripts/run_google_blog_agent_locally.py → app/schemas.py
- `str` --uses--> `TripParams`  [INFERRED]
  scripts/run_reddit_agent_locally.py → app/schemas.py

## Hyperedges (group relationships)
- **Parallel Research Agent Pipeline (youtube + reddit + google → merge → synthesizer)** — agents_yt_shorts_run_youtube_shorts_agent, agents_yt_longform_run_youtube_longform_agent, agents_reddit_run_reddit_agent, agents_google_blog_run_google_blog_agent, agents_synth_run_synthesizer, schemas_ResearchDiscovery [EXTRACTED 1.00]
- **Signals-Driven Personalization (TripParams → TravelSignals → agent queries)** — schemas_TripParams, signals_TravelSignals, signals_extract_signals, agents_google_blog_build_queries, agents_reddit_build_queries [EXTRACTED 1.00]
- **Geo Brief Pipeline (city circuit + distances + sun times → synthesizer prompt)** — geo_planner_GeoBrief, geo_planner_build_geo_brief, geo_distance_road_km, geo_sun_sun_times, agents_synth_build_prompt [EXTRACTED 0.95]
- **Parallel Research Agents Gated by L1 Cache (research_gate → youtube, youtube_longform, reddit, google)** — pipeline_research_gate_node, pipeline_youtube_node, pipeline_youtube_longform_node, pipeline_reddit_node, pipeline_google_node [EXTRACTED 1.00]
- **LLM Factory Provider Selection Pattern (role → provider/model → BaseChatModel)** — factory_get_llm, factory_resolve_role, factory_build_llm, factory_LLMRoles [EXTRACTED 1.00]
- **Merge + Geo Fan-in to Synthesizer (merge_node + geo_node → synthesizer_node)** — pipeline_merge_node, pipeline_geo_node, pipeline_synthesizer_node [EXTRACTED 1.00]
- **Schema Validation Tests** — test_schemas, app_schemas, fixture_sample_trip_goa [EXTRACTED 1.00]
- **Signals Unit Tests (5 Destination Contract)** — test_signals, app_signals, app_schemas [EXTRACTED 1.00]
- **Geo Layer Unit Tests** — test_geo, app_geo, app_geo_distance, app_geo_planner, app_geo_sun [EXTRACTED 1.00]
- **Reddit Agent Tests** — test_reddit_agent, app_agents_reddit, app_tools_reddit [EXTRACTED 1.00]
- **Reddit Tool Tests** — test_reddit_tool, app_tools_reddit [EXTRACTED 1.00]
- **Synthesizer Agent Tests** — test_synthesizer, app_agents_synthesizer, app_signals, app_schemas [EXTRACTED 1.00]
- **YouTube Shorts Agent Tests** — test_youtube_agent, app_agents_youtube_shorts, app_tools_youtube [EXTRACTED 1.00]
- **YouTube Longform Agent Tests** — test_youtube_longform_agent, app_agents_youtube_longform, app_tools_youtube [EXTRACTED 1.00]
- **YouTube Tool Tests** — test_youtube_tool, app_tools_youtube [EXTRACTED 1.00]
- **Redis Cache Tests** — test_cache, app_cache, app_graph_pipeline [EXTRACTED 1.00]
- **Itinerary Eval Tests** — test_eval, app_eval, app_schemas, app_signals [EXTRACTED 1.00]
- **Skill Loader Tests** — test_skill_loader, app_skills_loader [EXTRACTED 1.00]
- **India Destination Test Fixtures** — fixture_sample_trip_goa, fixture_sample_trip_manali, fixture_sample_trip_rajasthan [EXTRACTED 1.00]
- **Sprint Quality Benchmark Progression** — concept_sprint2_benchmark, concept_sprint3_benchmark, concept_sprint4_benchmark, concept_sprint5_benchmark, concept_sprint6_benchmark, concept_sprint7_benchmark [EXTRACTED 1.00]
- **IMPROVEMENT_PLAN Workstreams** — concept_personalization_wiring, concept_skill_files, concept_ws3_reddit_gating, concept_redis_cache, concept_ws7_geo [EXTRACTED 1.00]
- **Agent Skill Overlay System (synthesizer + region + trip-shape + vibe skills)** — skill_synthesizer, skill_region_india, skill_region_europe, skill_region_southeast_asia, skill_trip_shape_region_multi_city, skill_vibe_food_and_markets, concept_skill_loader [EXTRACTED 0.95]
- **Tiered Redis Context Engine (L1 cache + L2 vector + L3 user memory)** — sysdesign_l1_cache_design, sysdesign_l2_vector_retrieval, sysdesign_l3_user_memory, concept_geocode_cache, concept_vibe_neutral_research [EXTRACTED 1.00]
- **Claude Dev Agents Governance Pattern (pipeline-reviewer + schema-sync-checker + rules)** — agent_pipeline_reviewer, agent_schema_sync_checker, rule_agent_architecture, rule_coding_standards, rule_db_contract [EXTRACTED 0.95]

## Communities (94 total, 16 thin omitted)

### Community 0 - "Reddit Agent Core"
Cohesion: 0.06
Nodes (78): _build_queries(), _build_query_subreddit_pairs(), _build_subreddits(), _destination_tokens(), _extract_via_llm(), _ExtractedInsight, _filter_posts(), _format_posts_for_prompt() (+70 more)

### Community 1 - "Signals & Personalization"
Cohesion: 0.05
Nodes (71): _build_query_modifiers(), _build_seasonal_tips(), _build_warnings(), _classify_destination_via_llm(), _crowd_level(), _currency_hint(), _date_in_window(), _DestinationClassification (+63 more)

### Community 2 - "Synthesizer Core"
Cohesion: 0.09
Nodes (60): _dedupe_for_prompt(), _LLMItineraryDraft, _normalize_title(), Canonical key for cross-source merging.      Lowercases, drops inner-word punc, Collapse duplicates by normalised title, preserving multi-source signal., Stops-per-day plan respecting AIDay's [3,6] constraint.      pace_density is t, Pick skill-overlay names to append to the synthesizer prompt, by signal., _select_overlays() (+52 more)

### Community 3 - "Reddit Tool & Primitives"
Cohesion: 0.07
Nodes (54): Any, float, int, str, AsyncClient, Exception, run_google_blog_agent_locally (Google Blog agent QA harness), run_reddit_agent_locally (Reddit agent QA harness) (+46 more)

### Community 4 - "Blog & Anchor Agent"
Cohesion: 0.09
Nodes (44): _AnchorEntry, _AnchorExtractionResult, _BlogExtractionResult, _BlogPlace, _build_queries(), _extract_anchors_via_llm(), _extract_via_llm(), _format_articles_for_prompt() (+36 more)

### Community 5 - "Synthesizer Helpers"
Cohesion: 0.11
Nodes (47): _build_prompt(), _coerce_tags_for_stop(), _coerce_time(), _compute_stats(), _default_anchor_day(), _default_anchor_stop(), _extract_via_llm(), _fallback_city() (+39 more)

### Community 6 - "YouTube Shorts Agent"
Cohesion: 0.12
Nodes (42): _ExtractionResult, _LLMDay, _LLMStop, _ExtractionResult, _Pass1Output, bool, Any, bool (+34 more)

### Community 7 - "Skills Loader System"
Cohesion: 0.10
Nodes (33): app/skills/loader.py, int, Path, str, Skill Files (Runtime Prompt Externalization), object, Externalized agent prompts ("skills") + the loader that reads them.  Agents impo, Skill @include Pattern (C-style markdown includes with depth guard) (+25 more)

### Community 8 - "Data Schemas"
Cohesion: 0.12
Nodes (36): AIDay, AIStop, _LLMDay, _LLMStop, _PlaceCandidate, AIDay, AIStop, int (+28 more)

### Community 9 - "DB Writer & Cache"
Cohesion: 0.09
Nodes (30): AIItinerary, str, verify_internal_secret, get_cached_geocode, get_cached_research, _get_client (cache Redis), Redis Cache Layer (L1 — destination research + geocodes), set_cached_geocode (+22 more)

### Community 10 - "LangGraph Pipeline"
Cohesion: 0.08
Nodes (27): Anchor Seeding Pattern (maps-source fallback seeds), L1 Research Cache Gate (Redis-backed destination pool), PipelineState TypedDict, build_graph (LangGraph compiler), geo_node (pipeline node), google_node (pipeline node), merge_node (fan-in with anchor seeding), reddit_node (pipeline node) (+19 more)

### Community 11 - "YouTube Longform Agent"
Cohesion: 0.19
Nodes (22): _build_queries(), _enrich_with_transcripts(), _extract_via_llm(), _pass1_extract_all(), _pass1_extract_batch(), _pass2_synthesize(), YouTubeLongformAgent — extract concrete travel insights from 4-25 min vlogs., Return 3-4 long-form queries — vlog / food / vibe / season.      Different fro (+14 more)

### Community 12 - "YouTube API Tool"
Cohesion: 0.15
Nodes (21): Any, int, str, YouTubeShort dataclass (shared by Shorts + Longform agents), fetch_transcript_safe(), _fetch_video_details(), _items_to_longform(), _items_to_shorts() (+13 more)

### Community 13 - "Agent Test Fixtures"
Cohesion: 0.15
Nodes (17): app/agents/youtube_longform.py, app/agents/youtube_shorts.py, app/schemas.py, app/signals.py, score_itinerary Heuristic Eval, Fixture: Bali Indonesia Trip, Fixture: Goa India Trip (sample_trip.json), Fixture: Rajasthan Heritage Trip (+9 more)

### Community 14 - "Solar & Timezone Calc"
Cohesion: 0.14
Nodes (19): date, float, str, IANA timezone for a destination. Specific keyword wins over region default;, _tz_name(), _hhmm(), Sunrise / sunset times via the standard sunrise equation (pure math, no API).  R, Return (sunrise, sunset) as 'H:MM' local strings, or None at polar day/night. (+11 more)

### Community 15 - "Eval & Scoring"
Cohesion: 0.16
Nodes (19): app/eval.py, _currency_symbol(), AIItinerary, str, TravelSignals, TripParams, Heuristic itinerary scorer for the eval harness.  Pure, deterministic, token-fre, INR (₹)' → '₹'; 'EUR (€)' → '€'; falls back to the whole hint. (+11 more)

### Community 16 - "Place Mention Clustering"
Cohesion: 0.14
Nodes (21): _cluster_mentions(), _destination_tokens(), _extract_visual_hooks(), _format_clusters_for_pass2(), _is_destination_cluster(), _normalize_place_key(), _PlaceMention, One atomic place mention from a single video. Pass 1 output. (+13 more)

### Community 17 - "YouTube Tool Tests"
Cohesion: 0.16
Nodes (19): _build_mock_transport(), MockTransport, Unit tests for the YouTube Data API tool layer.  No real API calls — uses http, Return an httpx MockTransport that responds to /search and /videos., 45s and 200s should survive (short-form). 6-minute item should be dropped., test_iso8601_minute_only(), test_parse_iso8601_hours_minutes_seconds(), test_parse_iso8601_invalid_returns_zero() (+11 more)

### Community 18 - "Internal Auth"
Cohesion: 0.12
Nodes (18): str, Internal-secret auth for the Node ↔ Python boundary., FastAPI dependency: validate the Authorization: Bearer <secret> header.      Rai, verify_internal_secret(), str, TripParams, BackgroundTasks, JSONResponse (+10 more)

### Community 19 - "Video Quality Filters"
Cohesion: 0.12
Nodes (20): _filter_quality(), _is_listicle(), _passes_engagement(), Layer 1d — drop SEO listicle/clickbait by title pattern., Layer 1e — view floor + like:view ratio.      The ratio acts as a quality prox, Apply engagement floor + per-channel cap. Listicle videos are kept but     depr, int, str (+12 more)

### Community 20 - "Longform Extraction Pipeline"
Cohesion: 0.17
Nodes (19): _enrich_with_transcripts(), _extract_via_llm(), _format_videos_for_pass1(), _pass1_extract_all(), _pass1_extract_batch(), _pass2_synthesize(), YouTubeShortsAgent — extract concrete travel insights from short-form YouTube., Best-effort: populate `transcript` on the top N shorts in parallel.      Mutat (+11 more)

### Community 21 - "YouTube Query Builder"
Cohesion: 0.19
Nodes (19): _build_queries(), _build_query(), Return 3–5 narrow queries derived from trip params + signals.      Destination, Single-query view of _build_queries (kept for back-compat)., TripParams, Unit tests for YouTubeShortsAgent.  We mock both the YouTube tool AND the LLM, No Goa-specific hardcoding — must work worldwide., End-to-end: search returns shorts, _extract_via_llm produces discoveries,     a (+11 more)

### Community 22 - "Observability & Tracing"
Cohesion: 0.18
Nodes (17): configure_observability(), Observability wiring (Milestone follow-up).  LangChain/LangSmith reads its traci, Enable LangSmith tracing if configured; otherwise a no-op (logged)., enrich_anchor_hints(), Always runs. Populates signals.top_anchors with 5-6 canonical landmark names., _amain(), _banner(), main() (+9 more)

### Community 23 - "Pipeline Build"
Cohesion: 0.27
Nodes (16): Any, str, geo_node(), google_node(), merge_node(), PipelineState, LangGraph pipeline.  Topology:     [signal_extractor]  (entry — pure Python,, # NOTE: anchor-hint enrichment (an LLM call) moved to research_gate so it (+8 more)

### Community 24 - "Cache Layer"
Cohesion: 0.23
Nodes (17): _geo_key(), get_cached_geocode(), get_cached_research(), _get_client(), Any, float, ResearchDiscovery, str (+9 more)

### Community 25 - "LLM Factory"
Cohesion: 0.22
Nodes (17): Any, str, BaseChatModel, _apply_structured(), _build_llm(), get_llm(), get_structured_llm(), LLM factory — single point of model selection per agent role.  Every agent obt (+9 more)

### Community 26 - "Synthesizer Fallback"
Cohesion: 0.18
Nodes (18): _dedupe_for_prompt, Synthesizer Graceful Degradation (skeleton fallback), run_synthesizer, _skeleton_itinerary, TravelSignals, TripParams, Geo Layer No-Premium-API Principle, GeoBrief (+10 more)

### Community 27 - "Video Quality Filter 2"
Cohesion: 0.22
Nodes (13): _filter_quality(), _is_blacklisted_channel(), _is_listicle(), _passes_engagement(), bool, app/tools/youtube.py, Unit tests for YouTubeLongformAgent.  Mocks both the long-form YouTube tool AN, test_blacklisted_channels_are_dropped() (+5 more)

### Community 28 - "Claude Settings Hooks"
Cohesion: 0.15
Nodes (12): args, command, type, hooks, PreToolUse, mcpServers, context7, supabase (+4 more)

### Community 29 - "Sample Trip Params"
Cohesion: 0.15
Nodes (12): accommodation, budget, date_from, date_to, destination, duration_days, pace, preferences (+4 more)

### Community 30 - "Geo Distance Module"
Cohesion: 0.21
Nodes (12): float, int, str, drive_time_hint(), haversine_km(), Great-circle distance + rough drive-time estimation (pure Python, no I/O)., Great-circle distance between two lat/lng points, in kilometres., Approximate driving distance (km), rounded — haversine × road factor. (+4 more)

### Community 31 - "Architecture Governance"
Cohesion: 0.26
Nodes (13): pipeline-reviewer Agent, Key Architectural Decisions, CLAUDE.md — Service Overview, asyncio.to_thread for Sync Supabase Calls, Graceful Degradation Contract, LLM Factory Pattern (get_llm), PipelineState TypedDict, Agent Architecture Rules (+5 more)

### Community 32 - "Trip Fixture Data"
Cohesion: 0.15
Nodes (12): accommodation, budget, date_from, date_to, destination, duration_days, pace, preferences (+4 more)

### Community 33 - "Trip Fixture Data"
Cohesion: 0.15
Nodes (12): accommodation, budget, date_from, date_to, destination, duration_days, pace, preferences (+4 more)

### Community 34 - "Trip Fixture Data"
Cohesion: 0.15
Nodes (12): accommodation, budget, date_from, date_to, destination, duration_days, pace, preferences (+4 more)

### Community 35 - "Trip Fixture Data"
Cohesion: 0.15
Nodes (12): accommodation, budget, date_from, date_to, destination, duration_days, pace, preferences (+4 more)

### Community 36 - "Trip Fixture Data"
Cohesion: 0.15
Nodes (12): accommodation, budget, date_from, date_to, destination, duration_days, pace, preferences (+4 more)

### Community 37 - "Trip Fixture Data"
Cohesion: 0.15
Nodes (12): accommodation, budget, date_from, date_to, destination, duration_days, pace, preferences (+4 more)

### Community 38 - "Trip Fixture Data"
Cohesion: 0.15
Nodes (12): accommodation, budget, date_from, date_to, destination, duration_days, pace, preferences (+4 more)

### Community 39 - "Trip Fixture Data"
Cohesion: 0.15
Nodes (12): accommodation, budget, date_from, date_to, destination, duration_days, pace, preferences (+4 more)

### Community 40 - "Trip Fixture Data"
Cohesion: 0.15
Nodes (12): accommodation, budget, date_from, date_to, destination, duration_days, pace, preferences (+4 more)

### Community 41 - "GeoBrief Data Model"
Cohesion: 0.20
Nodes (10): bool, float, str, _nearest_neighbour_order(), _offset_hours(), DST-aware UTC offset (hours) for a timezone on a given date., Render the brief for the synthesizer prompt, or '' when empty., Greedy NN tour starting from the first city. Returns (order, improved?).      `i (+2 more)

### Community 42 - "CI/CD Pipeline"
Cohesion: 0.17
Nodes (12): CI Lint Step (ruff check), CI Unit Tests Step (pytest), CI Dependency Sync Step (uv sync), CI GitHub Actions Workflow, Free-Tier LLM Constraint, _coerce_time Regression Fix, Milestone A — Quick Wins, Milestone B — Skills Subsystem (+4 more)

### Community 43 - "Geo & Cache Architecture"
Cohesion: 0.26
Nodes (12): Geocode Cache (Redis, near-permanent TTL), Nearest-Neighbour City Route De-backtracking, Sunrise/Sunset via Deterministic Formula, Geo-Routing Layer (OSM + Haversine), Tier 1 — Content Overhaul, App Skill: Region Overlay — Europe, App Skill: Region Overlay — India, App Skill: Region Overlay — Southeast Asia (+4 more)

### Community 44 - "Longform Agent Flow"
Cohesion: 0.18
Nodes (11): _enrich_with_transcripts (longform), _filter_quality (longform), _pass1_extract_all (longform), _pass2_synthesize (longform), run_youtube_longform_agent, Transcript as Hard Gate (longform agent), _cluster_mentions (youtube_shorts), _PASS1_SYSTEM (youtube_shorts) (+3 more)

### Community 45 - "Personalization Gap"
Cohesion: 0.29
Nodes (7): app/agents/reddit.py, app/agents/synthesizer.py, Reddit Destination-Mention Filter, maps-source Padding Problem, Dormant Personalization Layer (WS1), WS3 Reddit Season Gating, WS7 Geographic Reasoning

### Community 46 - "Research Cache Integration"
Cohesion: 0.25
Nodes (9): app/cache.py, app/graph/pipeline.py, fakeredis (Test Dependency), Redis Research Cache (L1), Research Gate Node (Cache Hit Short-Circuit), Execute the full pipeline for a trip and return final state., run_pipeline(), main() (+1 more)

### Community 47 - "Content Quality Rules"
Cohesion: 0.27
Nodes (11): Anchor Coverage Rule (famous landmarks), Banned Words List (brochure-speak), Eval Rubric (score_itinerary checks), Signals — Personalization Layer, SkillLoader (runtime markdown prompt loader), App Skill: Blog Anchor Extraction, App Skill: Blog Research Extraction, App Skill: Reddit Research Extraction (+3 more)

### Community 48 - "Benchmark Sprints"
Cohesion: 0.22
Nodes (9): Anchor Seeding Architecture, Sprint 2 Benchmark (AI-6), Sprint 3 Benchmark (AI-7 Rajasthan), Sprint 4 Benchmark (Singapore + Puri), Sprint 5 Benchmark (Anchor Coverage), Sprint 6 Benchmark (Validation Run), Sprint 7 Benchmark (Anchor Architecture Fix), Extraction LLM Vibe Bias Problem (+1 more)

### Community 49 - "DB Governance"
Cohesion: 0.27
Nodes (10): schema-sync-checker Agent, Reference Priority Order, Idempotent Itinerary Write, Internal Auth via INTERNAL_AGENT_SECRET, research_jobs Status Machine (pending→completed), Pydantic ↔ Zod Schema Parity, SourceType Literal Enum, Supabase Column Contract (snake_case) (+2 more)

### Community 50 - "System Architecture Design"
Cohesion: 0.27
Nodes (10): Polyglot Boundary (Node vs Python), Vibe-Neutral Research for Cache Reuse, Deferred: L2 Semantic Retrieval + L3 User Memory, L1 Redis Destination Research Cache, Cold Path Data Flow, L1 Exact-Key KV Cache Design, L2 Vector Retrieval (RedisVL) Design, L3 Long-Term User Memory Design (+2 more)

### Community 51 - "Discovery Validation"
Cohesion: 0.29
Nodes (10): _ExtractedDiscovery, One discovery extracted by the LLM. Maps to ResearchDiscovery.      The schema, Apply Layer 2d (vagueness) + 2e (generic title) + cross-discovery dedupe., _validate_and_dedupe(), _disc(), Schema requires min_length=1, but indices outside range count as no-evidence., test_validate_dedupes_by_place_name(), test_validate_drops_generic_title() (+2 more)

### Community 52 - "YouTube Data Types"
Cohesion: 0.31
Nodes (8): float, int, str, YouTubeShort, test_enrich_with_transcripts_drops_videos_without_captions(), _video(), Engagement proxy. >=0.01 (1%) is healthy on Shorts; viral mass-market         c, YouTubeShort

### Community 53 - "Geo Module"
Cohesion: 0.33
Nodes (8): app/geo, app/geo/distance.py, app/geo/planner.py, app/geo/sun.py, GeoBrief (Geographic Reasoning Layer), Haversine Distance Calculation, Nearest-Neighbour Route Ordering, Sunrise/Sunset Times (DST-Aware)

### Community 54 - "YouTube Discovery Output"
Cohesion: 0.28
Nodes (9): Map validated _ExtractedDiscovery → ResearchDiscovery (wire schema).      Comb, Return YouTube-derived discoveries for the trip.      All errors are caught an, run_youtube_agent(), _to_research_discoveries(), ResearchDiscovery, _ExtractedDiscovery, Even if the LLM emits generic content, the validator must drop it., test_run_youtube_agent_drops_vague_llm_output() (+1 more)

### Community 55 - "YouTube Local Dev"
Cohesion: 0.36
Nodes (7): main(), Path, str, Run the YouTubeShortsAgent end-to-end against the real YouTube API + real LLM., Map a CLI arg to a fixture path. Accepts short names, full paths, or None., _resolve_fixture(), _run_one()

### Community 56 - "LLM Role Config"
Cohesion: 0.32
Nodes (8): LLM Roles (youtube_agent, reddit_agent, google_agent, synthesizer, signals_classifier, geo_planner, youtube_longform_agent), Synthesizer Fallback Pattern (Cerebras-235B primary → Groq-70B fallback), _apply_structured (with_structured_output wrapper), _build_llm (provider-specific LangChain instantiation), get_llm (LLM factory entry point), get_structured_llm (structured output with fallback), _resolve_fallback (synthesizer Cerebras→Groq fallback), _resolve_role (provider/model mapping per role)

### Community 57 - "Draft to Itinerary"
Cohesion: 0.29
Nodes (8): _compute_stats, _is_filler_stop, _llm_draft_to_itinerary, _resort_stops_chronologically, _time_to_minutes, Heuristic Itinerary Scorer (eval harness), score_itinerary, AIItinerary

### Community 58 - "Geo Planning"
Cohesion: 0.36
Nodes (6): Geographic reasoning layer (Milestone D).  Turns a destination into a *geo brief, GeoBrief, GeoLeg, Build a geo brief for a trip: a verified city circuit + distances + sun times., test_geobrief_empty_renders_blank(), test_geobrief_prompt_block_contains_facts()

### Community 59 - "Geocode Tool"
Cohesion: 0.36
Nodes (7): float, str, Nominatim Rate Policy (1 req/s, custom UA, in-process + Redis cache), _fetch(), geocode(), OpenStreetMap Nominatim geocoder — free, no API key.  Used by the geo layer to t, Return (lat, lng) for a place query, or None on miss/error.      Results (includ

### Community 60 - "YouTube Agent Tests"
Cohesion: 0.29
Nodes (7): RuntimeError, TripParams, When every candidate fails the transcript gate, the agent returns []     withou, test_run_catches_runtime_error_for_missing_api_key(), test_run_returns_empty_on_zero_search_results(), test_run_returns_empty_when_no_videos_have_transcripts(), _trip()

### Community 61 - "Reddit Local Dev"
Cohesion: 0.43
Nodes (6): main(), Path, str, Run the RedditAgent end-to-end against the live Reddit JSON API + real LLM.  U, _resolve_fixture(), _run_one()

### Community 62 - "Agent Base Utilities"
Cohesion: 0.33
Nodes (5): Shared utilities for agents (retry, JSON parsing, validation).  Filled out as ag, Parse JSON, stripping common LLM wrapping (``` fences, leading text)., safe_json_loads(), Any, str

### Community 63 - "Claude Hooks Config"
Cohesion: 0.40
Nodes (4): hooks, PostToolUse, permissions, allow

### Community 64 - "FastAPI App Entry"
Cohesion: 0.50
Nodes (3): health(), str, FastAPI entrypoint for nomad-agent.

### Community 65 - "App Configuration"
Cohesion: 0.50
Nodes (3): Centralized configuration via pydantic-settings.  All env vars are loaded here, Settings, BaseSettings

### Community 66 - "Skill Overlay Dispatch"
Cohesion: 0.50
Nodes (4): _build_prompt (synthesizer), _extract_via_llm (synthesizer), _select_overlays, Signals-Driven Skill Overlays (region/vibe/trip-shape playbooks)

### Community 67 - "Architecture Analysis"
Cohesion: 0.50
Nodes (4): Destination Knowledge vs Personalization Split, Bottleneck and Failure Analysis, Current Architecture (As-Is), Target Architecture (To-Be)

### Community 68 - "Quality Milestones"
Cohesion: 0.67
Nodes (3): Quality Trajectory (4/10 → 8/10), Refinement Plan Sign-off, System Design — Problem Framing

## Knowledge Gaps
- **197 isolated node(s):** `PreToolUse`, `type`, `url`, `type`, `command` (+192 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **16 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `TripParams` connect `YouTube Shorts Agent` to `Reddit Agent Core`, `Signals & Personalization`, `Synthesizer Core`, `Blog & Anchor Agent`, `Synthesizer Helpers`, `Data Schemas`, `YouTube Longform Agent`, `Agent Test Fixtures`, `Solar & Timezone Calc`, `Eval & Scoring`, `Place Mention Clustering`, `Internal Auth`, `Video Quality Filters`, `Longform Extraction Pipeline`, `YouTube Query Builder`, `Observability & Tracing`, `Pipeline Build`, `Synthesizer Fallback`, `Video Quality Filter 2`, `GeoBrief Data Model`, `Research Cache Integration`, `Discovery Validation`, `YouTube Data Types`, `YouTube Discovery Output`, `YouTube Local Dev`, `Geo Planning`, `YouTube Agent Tests`, `Reddit Local Dev`?**
  _High betweenness centrality (0.206) - this node is a cross-community bridge._
- **Why does `Graceful Degradation Contract` connect `Architecture Governance` to `Personalization Gap`?**
  _High betweenness centrality (0.105) - this node is a cross-community bridge._
- **Why does `ResearchDiscovery` connect `YouTube Shorts Agent` to `Reddit Agent Core`, `Signals & Personalization`, `Synthesizer Core`, `Blog & Anchor Agent`, `Synthesizer Helpers`, `Data Schemas`, `DB Writer & Cache`, `YouTube Longform Agent`, `Eval & Scoring`, `Place Mention Clustering`, `Discovery Validation`, `Longform Extraction Pipeline`, `YouTube Discovery Output`, `Pipeline Build`, `Cache Layer`, `Observability & Tracing`, `Video Quality Filter 2`?**
  _High betweenness centrality (0.081) - this node is a cross-community bridge._
- **Are the 144 inferred relationships involving `TripParams` (e.g. with `_AnchorEntry` and `_AnchorExtractionResult`) actually correct?**
  _`TripParams` has 144 INFERRED edges - model-reasoned connections that need verification._
- **Are the 103 inferred relationships involving `ResearchDiscovery` (e.g. with `_AnchorEntry` and `_AnchorExtractionResult`) actually correct?**
  _`ResearchDiscovery` has 103 INFERRED edges - model-reasoned connections that need verification._
- **Are the 81 inferred relationships involving `TravelSignals` (e.g. with `_AnchorEntry` and `_AnchorExtractionResult`) actually correct?**
  _`TravelSignals` has 81 INFERRED edges - model-reasoned connections that need verification._
- **Are the 43 inferred relationships involving `AIItinerary` (e.g. with `_LLMDay` and `_LLMItineraryDraft`) actually correct?**
  _`AIItinerary` has 43 INFERRED edges - model-reasoned connections that need verification._