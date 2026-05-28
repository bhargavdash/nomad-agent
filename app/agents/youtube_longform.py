"""YouTubeLongformAgent — extract concrete travel insights from 4-25 min vlogs.

Why a separate agent, not extending youtube_shorts.py?
  * Different *substrate*: Shorts often have no captions and rely on title +
    description + tags. Long-form is the inverse — captions are nearly always
    present and are the only realistic way to extract place names at scale.
    Transcript is a HARD requirement here; videos without captions get dropped.
  * Different *failure modes*: long-form is dominated by listicle/SEO formats
    ("Top 10 Things To Do…", "ULTIMATE Guide", "Everything You Need To Know"),
    by corporate channels (TripAdvisor, Tasting Table, India Times), and by
    full-trip recap vlogs that mention dozens of cities. We need a stricter
    listicle regex and a channel blacklist that the Shorts agent doesn't need.
  * Different *cost shape*: longer transcripts → smaller Pass-1 batches.

Pipeline (same shape as Shorts):
  1. _build_queries() — 3-4 long-form-flavoured queries (vlog / food / vibe /
     season). No "shorts" / "hidden places" suffixes — those bias toward Shorts.
  2. search_youtube_longform() per query, dedupe by video_id.
  3. Drop listicle/clickbait videos (stricter regex) + channel blacklist.
  4. Quality filter: per-channel best, lower like:view floor than Shorts
     (long-form naturally has lower engagement ratios).
  5. Transcript fetch (mandatory). Drop videos without captions.
  6. Pass-1 atomic place-mention extraction (smaller batches for the larger
     transcripts) → cluster → Pass-2 synthesis → validate.
  7. Reuses the SAME _cluster_mentions / _validate_and_dedupe / Pass-1/2 prompts
     from youtube_shorts.py — quality gates that work for Shorts are equally
     valid here.

Failure modes (all return []):
  - YOUTUBE_API_KEY missing
  - Quota exceeded / API errors
  - All searches return 0 / all videos filtered out
  - No surviving videos have captions
  - LLM call fails or all outputs fail validation
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.youtube_shorts import (
    _PASS1_SYSTEM,
    _PASS2_SYSTEM,
    _ExtractedDiscovery,
    _ExtractionResult,
    _Pass1Output,
    _PlaceMention,
    _cluster_mentions,
    _destination_tokens,
    _format_clusters_for_pass2,
    _format_videos_for_pass1,
    _validate_and_dedupe,
)
from app.llm.factory import get_llm
from app.schemas import ResearchDiscovery, TripParams
from app.signals import TravelSignals
from app.tools.youtube import (
    YouTubeShort,
    fetch_transcript_safe,
    search_youtube_longform,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables (long-form specific — see youtube_shorts.py for the Shorts values)
# ---------------------------------------------------------------------------

MAX_RESULTS_PER_QUERY = 12  # 4 queries × 12 = 48 candidates max
MAX_DEDUPED_CANDIDATES = 20
MAX_VIDEOS_FOR_LLM = 10  # smaller than Shorts (18) — transcripts are bigger
MAX_TRANSCRIPTS_TO_FETCH = 20  # try more; transcript availability is the gate
TRANSCRIPT_MAX_CHARS = 3000  # bigger than Shorts (800) — long-form has real content
MIN_VIEW_COUNT = 1000  # higher floor; long-form gets more reach naturally
MIN_LIKE_VIEW_RATIO = 0.001  # 0.1% — long-form ratios run lower than Shorts
MAX_DISCOVERIES_RETURNED = 8
PASS1_BATCH_SIZE = 3  # smaller batches than Shorts — transcripts are 3-4× longer
MAX_MENTIONS_PER_VIDEO = 6  # long-form videos mention more places legitimately

# Stricter listicle regex than Shorts'. Long-form SEO patterns differ — they
# include "ultimate guide", "everything you need", "complete" prefixes, and
# numbered titles are far more common.
LONGFORM_LISTICLE_TITLE_RE = re.compile(
    r"\btop\s*\d+\b"
    r"|\bbest\s*\d+\b"
    r"|\b\d+\s*(?:places|things|spots|reasons|tips|days|hours|minutes|sights)\b"
    r"|\bmust[-\s]?visit\b"
    r"|\bmust[-\s]?see\b"
    r"|\btourist\s+places\b"
    r"|\bplaces\s+to\s+visit\b"
    r"|\byou\s+must\b"
    r"|\bultimate\s+guide\b"
    r"|\bcomplete\s+guide\b"
    r"|\beverything\s+you\s+need\s+to\s+know\b"
    r"|\beverything\s+about\b"
    r"|\bcheap(?:est)?\s+(?:way|things|places)\b"
    r"|\bthings?\s+to\s+do\s+in\s+\d+\b",
    re.IGNORECASE,
)

# Channels that publish high-volume SEO listicles. Per-channel-best already
# de-duplicates but these channels' single best is still listicle-shaped.
# Lowercased substring match against channel_title.
LONGFORM_CHANNEL_BLACKLIST: set[str] = {
    "tripadvisor",
    "expedia",
    "booking.com",
    "wikitravel",
    "wikipedia",
    "times of india",
    "india times",
    "ndtv",
    "tasting table",
    "buzzfeed",
    "insider business",
    "insider travel",
    "lonely planet",  # editorial but every video is "top N" — drowns out creators
}


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------


def _build_queries(trip_params: TripParams, signals: TravelSignals) -> list[str]:
    """Return 3-4 long-form queries — vlog / food / vibe / season.

    Different from Shorts: drops the "{dest} hidden places" query (biases too
    far toward Shorts results on the medium-duration filter) and replaces it
    with "{dest} travel guide" — but the listicle regex catches the worst of
    the "Ultimate Guide" titles afterwards.
    """
    dest = trip_params.destination.strip()
    queries: list[str] = []
    # Q1: POV vlog. Long-form vlogs are the canonical authentic content.
    queries.append(f"{dest} travel vlog")
    # Q2: food vertical. Strong proper-noun yield (dish + restaurant names).
    queries.append(f"{dest} food guide")
    # Q3: first vibe — or itinerary angle as fallback.
    if trip_params.vibes:
        first_vibe = trip_params.vibes[0].strip()
        if first_vibe:
            queries.append(f"{dest} {first_vibe}")
        else:
            queries.append(f"{dest} itinerary")
    else:
        queries.append(f"{dest} itinerary")
    # Q4: season — only when informative.
    informative_seasons = {"winter", "summer", "monsoon", "autumn", "spring"}
    if signals.season in informative_seasons:
        queries.append(f"{dest} {signals.season}")
    # Dedupe + cap.
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
        if len(out) >= 4:
            break
    return out


# ---------------------------------------------------------------------------
# Quality filtering
# ---------------------------------------------------------------------------


def _is_listicle(title: str) -> bool:
    return bool(LONGFORM_LISTICLE_TITLE_RE.search(title or ""))


def _is_blacklisted_channel(channel_title: str) -> bool:
    name = (channel_title or "").lower()
    return any(tok in name for tok in LONGFORM_CHANNEL_BLACKLIST)


def _passes_engagement(s: YouTubeShort) -> bool:
    if s.view_count < MIN_VIEW_COUNT:
        return False
    if s.like_count > 0 and s.like_view_ratio < MIN_LIKE_VIEW_RATIO:
        return False
    return True


def _filter_quality(videos: list[YouTubeShort]) -> list[YouTubeShort]:
    survivors: list[YouTubeShort] = []
    for v in videos:
        if _is_listicle(v.title):
            continue
        if _is_blacklisted_channel(v.channel_title):
            continue
        if not _passes_engagement(v):
            continue
        survivors.append(v)
    # Best per channel by view count.
    by_channel: dict[str, YouTubeShort] = {}
    for v in survivors:
        existing = by_channel.get(v.channel_title)
        if existing is None or v.view_count > existing.view_count:
            by_channel[v.channel_title] = v
    deduped = sorted(by_channel.values(), key=lambda v: v.view_count, reverse=True)
    return deduped[:MAX_DEDUPED_CANDIDATES]


# ---------------------------------------------------------------------------
# Transcript enrichment — MANDATORY for long-form (unlike Shorts).
# ---------------------------------------------------------------------------


async def _enrich_with_transcripts(videos: list[YouTubeShort]) -> list[YouTubeShort]:
    """Fetch transcripts in parallel. Return only videos that GOT a transcript.

    This is the hard gate that justifies the long-form agent's existence: we
    rely on transcript text for place extraction. Without it, the per-video
    yield is too low to be worth a Pass-1 call.
    """
    targets = videos[:MAX_TRANSCRIPTS_TO_FETCH]

    async def _fetch_one(short: YouTubeShort) -> None:
        text = await asyncio.to_thread(fetch_transcript_safe, short.video_id, TRANSCRIPT_MAX_CHARS)
        if text:
            short.transcript = text

    await asyncio.gather(*(_fetch_one(v) for v in targets), return_exceptions=True)
    with_transcripts = [v for v in targets if v.transcript]
    logger.info(
        "youtube_longform.transcripts kept %d/%d (mandatory gate)",
        len(with_transcripts),
        len(targets),
    )
    return with_transcripts


# ---------------------------------------------------------------------------
# Pass-1 / Pass-2 (reuse the Shorts prompts + clustering — same problem shape)
# ---------------------------------------------------------------------------


async def _pass1_extract_batch(
    trip_params: TripParams, batch: list[YouTubeShort], offset: int
) -> list[_PlaceMention]:
    llm = get_llm("youtube_longform_agent")
    structured = llm.with_structured_output(_Pass1Output)
    dest_tokens = _destination_tokens(trip_params.destination)
    skip_list = ", ".join(sorted(t.title() for t in dest_tokens)) or "—"
    user = (
        f"Destination: {trip_params.destination}\n\n"
        f"For this destination, extract SUB-PLACES only — specific forts, "
        f"palaces, monuments, dishes, restaurants, beaches, viewpoints, "
        f"neighborhoods, markets, temples, festivals, waterfalls, lakes. "
        f"Do NOT extract bare city / state / country names. Especially do "
        f"NOT extract: {skip_list}.\n\n"
        f"Videos (long-form — transcripts may be 1500+ chars, many place "
        f"mentions per video):\n\n"
        f"{_format_videos_for_pass1(batch, offset)}\n\n"
        f"List specific sub-place mentions for each video. Up to "
        f"{MAX_MENTIONS_PER_VIDEO} per video. Empty list is fine."
    )
    messages: list[Any] = [
        SystemMessage(content=_PASS1_SYSTEM),
        HumanMessage(content=user),
    ]
    try:
        result = await structured.ainvoke(messages)
        if not isinstance(result, _Pass1Output):
            result = _Pass1Output.model_validate(result)
        per_video: dict[int, int] = {}
        kept: list[_PlaceMention] = []
        for m in result.mentions:
            n = per_video.get(m.video_index, 0)
            if n >= MAX_MENTIONS_PER_VIDEO:
                continue
            per_video[m.video_index] = n + 1
            kept.append(m)
        return kept
    except Exception as e:  # noqa: BLE001
        logger.warning("youtube_longform.pass1_batch_failed offset=%d err=%s", offset, e)
        return []


async def _pass1_extract_all(
    trip_params: TripParams, videos: list[YouTubeShort]
) -> list[_PlaceMention]:
    batches: list[tuple[int, list[YouTubeShort]]] = []
    for start in range(0, len(videos), PASS1_BATCH_SIZE):
        batches.append((start + 1, videos[start : start + PASS1_BATCH_SIZE]))
    results = await asyncio.gather(
        *(_pass1_extract_batch(trip_params, batch, offset) for offset, batch in batches),
        return_exceptions=True,
    )
    mentions: list[_PlaceMention] = []
    for r in results:
        if isinstance(r, BaseException):
            continue
        mentions.extend(r)
    logger.info("youtube_longform.pass1 batches=%d mentions=%d", len(batches), len(mentions))
    return mentions


async def _pass2_synthesize(
    trip_params: TripParams,
    signals: TravelSignals,
    clusters: list[tuple[str, list[_PlaceMention]]],
) -> list[_ExtractedDiscovery]:
    if not clusters:
        return []
    llm = get_llm("youtube_longform_agent")
    structured = llm.with_structured_output(_ExtractionResult)
    festival_line = (
        f"Active festivals during trip: {', '.join(signals.active_festivals)}\n"
        if signals.active_festivals
        else ""
    )
    user = (
        f"Destination: {trip_params.destination}\n"
        f"Trip dates: {trip_params.date_from} to {trip_params.date_to}\n"
        f"Season: {signals.season} (crowd level: {signals.crowd_level})\n"
        f"{festival_line}"
        f"Vibes: {', '.join(trip_params.vibes) if trip_params.vibes else '—'}\n\n"
        f"Clusters of place mentions extracted from long-form YouTube vlogs "
        f"(transcripts):\n\n"
        f"{_format_clusters_for_pass2(clusters)}\n\n"
        f"Write up to 8 discoveries from these clusters. Use only the evidence "
        f"shown. Empty list is acceptable."
    )
    messages: list[Any] = [
        SystemMessage(content=_PASS2_SYSTEM),
        HumanMessage(content=user),
    ]
    result = await structured.ainvoke(messages)
    if not isinstance(result, _ExtractionResult):
        result = _ExtractionResult.model_validate(result)
    logger.info("youtube_longform.pass2 returned=%d", len(result.discoveries))
    return result.discoveries


async def _extract_via_llm(
    trip_params: TripParams,
    signals: TravelSignals,
    videos: list[YouTubeShort],
) -> list[_ExtractedDiscovery]:
    mentions = await _pass1_extract_all(trip_params, videos)
    if not mentions:
        logger.info("youtube_longform: pass1 produced 0 mentions")
        return []
    clusters = _cluster_mentions(
        mentions, n_videos=len(videos), destination=trip_params.destination
    )
    logger.info(
        "youtube_longform clusters=%d (top: %s)",
        len(clusters),
        [name for name, _ in clusters[:5]],
    )
    if not clusters:
        return []
    return await _pass2_synthesize(trip_params, signals, clusters)


# ---------------------------------------------------------------------------
# Mapping → ResearchDiscovery (tag with `youtube` source — same wire contract)
# ---------------------------------------------------------------------------


def _to_research_discoveries(
    extracted: list[_ExtractedDiscovery],
) -> list[ResearchDiscovery]:
    out: list[ResearchDiscovery] = []
    for d in extracted:
        body_parts = [d.why_specific.strip()]
        if d.best_time:
            body_parts.append(f"Best time: {d.best_time.strip()}.")
        if d.practical_tip:
            body_parts.append(f"Tip: {d.practical_tip.strip()}.")
        body = " ".join(p for p in body_parts if p)
        tags = [t.strip() for t in d.tags if t.strip()][:3] or ["youtube"]
        out.append(
            ResearchDiscovery(
                id=str(uuid.uuid4()),
                title=d.place_name.strip(),
                body=body,
                tags=tags,
                source="youtube",  # same source as Shorts — synthesizer merges
            )
        )
    return out


# ---------------------------------------------------------------------------
# Search fan-out
# ---------------------------------------------------------------------------


async def _search_fanout(queries: list[str]) -> list[YouTubeShort]:
    results = await asyncio.gather(
        *(search_youtube_longform(q, max_results=MAX_RESULTS_PER_QUERY) for q in queries),
        return_exceptions=True,
    )
    seen: set[str] = set()
    merged: list[YouTubeShort] = []
    for q, res in zip(queries, results):
        if isinstance(res, BaseException):
            logger.warning("youtube_longform.search_failed query=%r err=%s", q, res)
            continue
        for s in res:
            if s.video_id in seen:
                continue
            seen.add(s.video_id)
            merged.append(s)
    logger.info(
        "youtube_longform.fanout queries=%d unique_videos=%d",
        len(queries),
        len(merged),
    )
    return merged


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_youtube_longform_agent(
    trip_params: TripParams, signals: TravelSignals
) -> list[ResearchDiscovery]:
    """Return long-form-YouTube-derived discoveries for the trip.

    All errors are caught and result in []; the synthesizer continues with
    whatever the other agents found. Same graceful-degradation contract as
    the other research agents.
    """
    try:
        queries = _build_queries(trip_params, signals)
        logger.info("youtube_longform.start queries=%r", queries)

        videos = await _search_fanout(queries)
        if not videos:
            logger.warning("youtube_longform: 0 unique videos across all queries")
            return []

        filtered = _filter_quality(videos)
        logger.info(
            "youtube_longform: %d videos after quality filter (from %d)",
            len(filtered),
            len(videos),
        )
        if not filtered:
            return []

        # Transcripts are the hard gate — videos without captions are dropped.
        with_transcripts = await _enrich_with_transcripts(filtered)
        if not with_transcripts:
            logger.warning("youtube_longform: 0 videos had transcripts — nothing to extract")
            return []

        candidates = with_transcripts[:MAX_VIDEOS_FOR_LLM]
        extracted = await _extract_via_llm(trip_params, signals, candidates)
        validated = _validate_and_dedupe(extracted, n_videos=len(candidates))
        # Cap at MAX_DISCOVERIES_RETURNED (defensive — _validate_and_dedupe
        # uses the Shorts cap, which is the same value today but the import
        # contract is opaque so we re-cap here).
        validated = validated[:MAX_DISCOVERIES_RETURNED]
        discoveries = _to_research_discoveries(validated)
        logger.info(
            "youtube_longform.done extracted=%d kept=%d returned=%d",
            len(extracted),
            len(validated),
            len(discoveries),
        )
        return discoveries

    except RuntimeError as e:
        logger.error("youtube_longform config error: %s", e)
        return []
    except Exception as e:  # noqa: BLE001
        logger.exception("youtube_longform unexpected failure: %s", e)
        return []
