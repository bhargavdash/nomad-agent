"""YouTubeShortsAgent — extract concrete travel insights from short-form YouTube.

Pipeline:
  1. _build_queries() → 3-5 narrow queries from destination + signals (no LLM,
     no destination-specific hardcoding).
  2. search_youtube_shorts() per query, dedupe by video_id.
  3. Drop listicle/clickbait videos by title pattern (LISTICLE_TITLE_RE).
  4. Quality filter: per-channel best, view + like:view engagement floor.
  5. Best-effort transcript fetch for the top N candidates (parallel).
  6. **Two-pass LLM extraction (Layer 3)**:
       Pass 1: per-batch atomic place-mention extraction. Each video can
               yield 0–N (place_name, quote) atoms. Empty is encouraged.
       Cluster: pure-Python group by lowercased place_name.
       Pass 2: cluster → final ResearchDiscovery, with evidence indices
               already known from Pass 1 mentions.
     Two passes structurally prevent the failure mode where one big call
     pads thin source data with vague filler to reach a quota.
  7. Vagueness validator drops generic outputs ("stunning beaches",
     "vibrant culture", "must-visit") even if the LLM emits them.
  8. Cluster by place_name (case-insensitive) → ResearchDiscovery list.

Failure modes (all return [] gracefully):
  - YOUTUBE_API_KEY missing
  - Quota exceeded / API errors
  - All searches return 0 / all videos filtered out
  - LLM call fails or output empty after validation
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.llm.factory import get_llm
from app.schemas import ResearchDiscovery, TripParams
from app.signals import TravelSignals
from app.tools.youtube import YouTubeShort, fetch_transcript_safe, search_youtube_shorts

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

MAX_RESULTS_PER_QUERY = 15        # API max per call is 50; 15 keeps quota sane
MAX_DEDUPED_CANDIDATES = 30       # cap pool after dedupe to control LLM tokens
MAX_VIDEOS_FOR_LLM = 18           # what we actually feed the LLM
MAX_TRANSCRIPTS_TO_FETCH = 18     # most Shorts have no captions; fetch all candidates
MIN_VIEW_COUNT = 500              # spam/bot floor
MIN_LIKE_VIEW_RATIO = 0.003       # 0.3% — lenient; viral mass-market often <0.5%
MAX_DISCOVERIES_RETURNED = 8

# Layer 3 — two-pass extraction tunables
PASS1_BATCH_SIZE = 6              # videos per Pass-1 LLM call (3 calls for 18 videos)
MAX_MENTIONS_PER_VIDEO = 4        # cap per-video place mentions to avoid spam
MAX_CLUSTERS_FOR_PASS2 = 14       # top clusters by mention-count fed to Pass 2

# Listicle / clickbait title patterns. Ranked by how reliably the format
# corresponds to generic SEO content with no concrete place mentions.
LISTICLE_TITLE_RE = re.compile(
    r"\btop\s*\d+\b"
    r"|\bbest\s*\d+\b"
    r"|\b\d+\s*(?:places|things|spots|reasons|tips)\b"
    r"|\bmust[-\s]?visit\b"
    r"|\bmust[-\s]?see\b"
    r"|\btourist\s+places\b"
    r"|\bplaces\s+to\s+visit\b"
    r"|\byou\s+must\b",
    re.IGNORECASE,
)

# Vagueness blacklist (Layer 2d). Applied to LLM body output. If a discovery
# body matches any of these phrases, it is dropped — even if the LLM thought
# it was concrete. This is deliberately strict; we'd rather return 3 good
# items than 8 mushy ones.
VAGUE_PHRASE_RE = re.compile(
    r"\bstunning\b"
    r"|\bvibrant\b"
    r"|\bbreathtaking\b"
    r"|\bscenic\b"
    r"|\bpicturesque\b"
    r"|\bparadise[-\s]?like\b"
    r"|\bnatural\s+beauty\b"
    r"|\brich\s+culture\b"
    r"|\blocal\s+culture\b"
    r"|\bmust[-\s]?visit\b"
    r"|\bsomething\s+for\s+everyone\b"
    r"|\bunique\s+experience\b"
    r"|\bbeautiful\s+(?:beaches?|landscapes?|architectures?|views?|sceneries?|scenery|"
    r"places?|spots?|sights?)\b"
    r"|\bhistoric\s+forts?\s+and\b"
    r"|\bvibrant\s+markets?\b",
    re.IGNORECASE,
)

# Pure tautology: bodies that ONLY say "<adjective>? <category> in <destination>"
# with no extra clause. We deliberately stop at the destination word — bodies
# like "Popular beach in Goa, often crowded" carry an extra fact ("often
# crowded") and should not be dropped. Examples this rejects:
#   "Beautiful beach in Goa.", "Scenic spot in Goa", "Famous waterfall in Goa"
TAUTOLOGY_RE = re.compile(
    r"^\s*(?:a\s+|the\s+)?(?:very\s+|really\s+)?"
    r"(?:beautiful|popular|famous|amazing|nice|good|great|lovely|charming|scenic|"
    r"picturesque|paradise[-\s]?like|stunning|wonderful)?\s*"
    r"(?:beach(?:es)?|spot|place|destination|temple|fort|waterfall|market|"
    r"village|town|island|attraction)s?\s+"
    r"(?:in|of|near)\s+\w+(?:\s+\w+)?"  # destination word(s), NO comma — stops here
    r"\s*\.?\s*$",
    re.IGNORECASE,
)

# Minimum useful body length after stripping. Set low enough that one
# concrete detail clause ("popular Wednesday flea market in Anjuna") clears
# the bar, but pure category labels do not.
MIN_WHY_LENGTH = 35

# Generic title phrases — if the LLM titles a discovery this, drop it.
# These are signals that the model failed to find a specific place.
GENERIC_TITLE_RE = re.compile(
    # Bare region/category labels (1–3 words, no specific noun)
    r"^\s*(?:"
    r"(?:north|south|east|west|central|offbeat|old|new|free|hidden)(?:\s+\w+){0,2}"
    r"|hidden\s+gems?(?:\s+\w+)?"
    r"|tourist\s+(?:places?|spots?|attractions?)(?:\s+\w+)?"
    r"|things\s+to\s+do(?:\s+\w+)?"
    r"|food(?:\s+\w+)?"
    r"|nightlife(?:\s+\w+)?"
    r"|beaches(?:\s+\w+)?"
    r"|temples(?:\s+\w+)?"
    r"|markets?(?:\s+\w+)?"
    r"|monsoon(?:\s+\w+)?"
    r"|\w+\s+tourist\s+places?"
    r")\s*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# LLM output schema (Layer 2a — forces grounded extraction)
# ---------------------------------------------------------------------------


class _ExtractedDiscovery(BaseModel):
    """One discovery extracted by the LLM. Maps to ResearchDiscovery.

    The schema is deliberately strict to prevent the failure mode where the
    LLM fills its quota with generic platitudes ("stunning beaches") when
    source data is thin.
    """

    place_name: str = Field(
        ...,
        min_length=2,
        max_length=120,
        description=(
            "A specific proper noun: restaurant, beach, viewpoint, dish, "
            "neighborhood, fort, festival, etc. NOT 'the beaches' or 'local "
            "markets'. If you cannot name a specific place, do not output "
            "this discovery."
        ),
    )
    why_specific: str = Field(
        ...,
        # No min_length: keeping the schema lenient avoids Groq tool-call
        # 400s when the model emits a short string. Vagueness/length is
        # enforced by _validate_and_dedupe instead.
        max_length=400,
        description=(
            "1-3 sentences (40+ chars) explaining what makes this place "
            "worth a visit, grounded in what the Shorts actually say. "
            "Avoid words like 'stunning', 'vibrant', 'breathtaking', "
            "'must-visit'. Anything shorter or vaguer than that will be "
            "discarded — drop the discovery instead."
        ),
    )
    best_time: str | None = Field(
        default=None,
        max_length=80,
        description="When to visit (e.g. 'before 10am', 'sunset', 'weekday'). "
        "Null if Shorts don't say.",
    )
    practical_tip: str | None = Field(
        default=None,
        max_length=200,
        description="Concrete tip — price, transport, queue, etc. Null if unknown.",
    )
    evidence_short_indices: list[int] = Field(
        ...,
        min_length=1,
        description=(
            "Indices [N] of Shorts in the input that mention this place. "
            "REQUIRED. Empty list means no evidence — drop the discovery."
        ),
    )
    tags: list[str] = Field(..., min_length=1, max_length=3)
    confidence: Literal["high", "medium", "low"]


class _ExtractionResult(BaseModel):
    discoveries: list[_ExtractedDiscovery]


# ---------------------------------------------------------------------------
# Query construction (Layer 1b — destination-agnostic fan-out)
# ---------------------------------------------------------------------------


def _build_queries(trip_params: TripParams, signals: TravelSignals) -> list[str]:
    """Return 3–5 narrow queries derived from trip params + signals.

    Destination-agnostic: every query is a template of `{destination}` plus
    one broad axis (vlog, food, vibe, season, offbeat). No place-specific
    keyword lists.

    Examples:
      Goa, India + vibes=[beaches,...] + season=monsoon →
        ['Goa, India travel vlog',
         'Goa, India food',
         'Goa, India beaches',
         'Goa, India monsoon',
         'Goa, India hidden places']
      Reykjavik, Iceland + vibes=[northern lights] + season=winter →
        ['Reykjavik, Iceland travel vlog',
         'Reykjavik, Iceland food',
         'Reykjavik, Iceland northern lights',
         'Reykjavik, Iceland winter',
         'Reykjavik, Iceland hidden places']
    """
    dest = trip_params.destination.strip()
    queries: list[str] = []

    # Q1 (always): vlog-style POV content. Lower listicle density than "tourist places".
    queries.append(f"{dest} travel vlog")

    # Q2 (always): food vertical — universal, often produces concrete dish/restaurant names.
    queries.append(f"{dest} food")

    # Q3: first user vibe if given, else generic discovery prompt.
    if trip_params.vibes:
        first_vibe = trip_params.vibes[0].strip()
        if first_vibe:
            queries.append(f"{dest} {first_vibe}")
    elif len(queries) < 5:
        queries.append(f"things to do in {dest}")

    # Q4: season — only if it's an informative bucket. Skip generic/unknown buckets.
    informative_seasons = {"winter", "summer", "monsoon", "autumn", "spring"}
    if signals.season in informative_seasons:
        queries.append(f"{dest} {signals.season}")

    # Q5 (always): offbeat angle — biases away from listicles, toward niche creators.
    queries.append(f"{dest} hidden places")

    # Dedupe while preserving order; cap at 5 queries / agent run.
    seen: set[str] = set()
    deduped: list[str] = []
    for q in queries:
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(q)
        if len(deduped) >= 5:
            break
    return deduped


# Back-compat shim so existing tests / call-sites keep working.
def _build_query(trip_params: TripParams, signals: TravelSignals) -> str:
    """Single-query view of _build_queries (kept for back-compat)."""
    return _build_queries(trip_params, signals)[0]


# ---------------------------------------------------------------------------
# Quality filtering
# ---------------------------------------------------------------------------


def _is_listicle(title: str) -> bool:
    """Layer 1d — drop SEO listicle/clickbait by title pattern."""
    return bool(LISTICLE_TITLE_RE.search(title or ""))


def _passes_engagement(s: YouTubeShort) -> bool:
    """Layer 1e — view floor + like:view ratio.

    The ratio acts as a quality proxy: viral mass-market reposts often
    have very low like:view, niche authentic creators have high.
    Videos with view_count below the floor fail outright.
    """
    if s.view_count < MIN_VIEW_COUNT:
        return False
    # If like_count is 0 it might just be hidden — don't penalize. Only
    # reject when likes are present-but-low relative to views.
    if s.like_count > 0 and s.like_view_ratio < MIN_LIKE_VIEW_RATIO:
        return False
    return True


def _filter_quality(shorts: list[YouTubeShort]) -> list[YouTubeShort]:
    """Apply listicle, engagement, and per-channel-best filters."""
    survivors: list[YouTubeShort] = []
    for s in shorts:
        if _is_listicle(s.title):
            continue
        if not _passes_engagement(s):
            continue
        survivors.append(s)

    # Keep best Short per channel by view count (proxy for quality).
    by_channel: dict[str, YouTubeShort] = {}
    for s in survivors:
        existing = by_channel.get(s.channel_title)
        if existing is None or s.view_count > existing.view_count:
            by_channel[s.channel_title] = s

    deduped = sorted(by_channel.values(), key=lambda s: s.view_count, reverse=True)
    return deduped[:MAX_DEDUPED_CANDIDATES]


# ---------------------------------------------------------------------------
# Transcript enrichment (Layer 1c)
# ---------------------------------------------------------------------------


async def _enrich_with_transcripts(shorts: list[YouTubeShort]) -> None:
    """Best-effort: populate `transcript` on the top N shorts in parallel.

    Mutates the shorts in place. Failures are silent — most Shorts have
    no captions, and the LLM still has title + description as fallback.
    """
    targets = shorts[:MAX_TRANSCRIPTS_TO_FETCH]

    async def _fetch_one(short: YouTubeShort) -> None:
        text = await asyncio.to_thread(fetch_transcript_safe, short.video_id)
        if text:
            short.transcript = text

    await asyncio.gather(*(_fetch_one(s) for s in targets), return_exceptions=True)
    got = sum(1 for s in targets if s.transcript)
    logger.info("youtube_agent.transcripts fetched %d/%d", got, len(targets))


# ---------------------------------------------------------------------------
# Two-pass LLM extraction (Layer 3)
#
# Pass 1: per-batch atomic place-mention extraction. The LLM answers ONE
#         narrow question per video — "what specific places are mentioned?"
#         — and is encouraged to return [] when the answer is nothing.
# Cluster: pure Python, no LLM. Group by lowercased place_name.
# Pass 2: cluster → final _ExtractedDiscovery. The LLM sees the clustered
#         evidence and only has to write the synthesis sentence per place.
#
# Splitting like this stops the failure mode where a single LLM call,
# asked for 8 discoveries from thin data, fills with vague platitudes.
# Pass 1 is allowed to be empty; Pass 2 only sees clusters that exist.
# ---------------------------------------------------------------------------

class _PlaceMention(BaseModel):
    """One atomic place mention from a single video. Pass 1 output."""

    video_index: int = Field(..., ge=1)
    place_name: str = Field(..., min_length=2, max_length=120)
    quote: str = Field(
        ...,
        max_length=300,
        description=(
            "Short snippet (<=300 chars) from the title/description/tags/"
            "transcript that supports this mention. Quote what the source "
            "says, do not paraphrase."
        ),
    )
    # Free-form short label. Kept as plain str (not Literal) so the LLM can
    # emit "street", "music", etc. without 400'ing the whole batch on enum
    # mismatch. The label is diagnostic only — not used for filtering.
    category: str = Field(default="other", max_length=40)


class _Pass1Output(BaseModel):
    mentions: list[_PlaceMention]


_PASS1_SYSTEM = """You are a travel-research data extractor for short-form YouTube videos about a single destination.

For each numbered video, list every SPECIFIC proper-noun place, dish, restaurant, festival, or named experience the video mentions.

Rules:
- Use the title, description, tags, and transcript (when present) — do NOT use prior knowledge.
- Output ONE atomic mention per (place_name, video). At most 4 mentions per video.
- `place_name` MUST be a concrete proper noun: "Dudhsagar Falls", "Butter Café Assagao",
  "Hawa Mahal Jaipur", "Pyaaz Kachori at Rawat Misthan Bhandar". NEVER output category
  labels like "beaches", "north Goa", "tourist places", "the markets".
- GO GRANULAR. Skip whole-city or whole-region names when the destination is itself a
  region. Examples:
    Destination "Rajasthan, India" — DO NOT extract: "Jaipur", "Jodhpur", "Udaipur" alone.
                                     DO extract: "Hawa Mahal", "Amber Fort", "City Palace
                                     Udaipur", "Mehrangarh Fort", "Pushkar Camel Fair",
                                     "Dal baati churma at Chokhi Dhani".
    Destination "Goa, India" — DO NOT extract: "north Goa", "south Goa", "Goa".
                               DO extract: "Anjuna Flea Market", "Dudhsagar Falls",
                               "Cabo de Rama Fort", "Vinayak fish thali".
  If a video only names a city/region (no monument, no dish, no specific neighborhood),
  return an empty list for that video. That is the correct answer.
- `quote` is a short, faithful excerpt from the video's text — proves the mention exists.
  Do not invent. If no clear quote, do not extract the mention.
- `category` is a short label (e.g. "fort", "dish", "beach", "market"). Free text is fine.

OUTPUT: JSON {"mentions": [...]}. Empty list is fine."""


_PASS2_SYSTEM = """You write final travel-research discoveries from clustered place-mention evidence.

You receive a list of CLUSTERS. Each cluster is one place_name with the quotes from the videos that mentioned it. Your job: pick the strongest clusters and write one discovery per place.

Strict rules:
- ONLY use clusters provided. Do NOT introduce new places.
- DROP clusters that name a whole city, state, or country when the trip destination is
  a wider region. Example: for "Rajasthan, India", drop "Jaipur", "Jodhpur", "Udaipur",
  "Rajasthan" as discoveries on their own — they are too coarse to be actionable. Keep
  only sub-city specifics: forts, palaces, markets, dishes, neighborhoods, festivals.
  Same logic for "Goa, India" — drop "north Goa", "south Goa", "Goa".
- `why_specific` MUST add at least ONE concrete clause beyond the place name (which
  neighborhood, what dish, when it's busy, who goes, what to order, what makes it different).
- BANNED words in `why_specific`: stunning, vibrant, breathtaking, scenic, picturesque,
  paradise-like, must-visit, natural beauty, rich culture, something for everyone, beautiful
  landscape, beautiful architecture, beautiful view.
- `evidence_short_indices` MUST be the union of video_index values from the cluster's
  quotes. Do not invent indices.
- Tautologies like "Popular beach in Goa" or "Famous fort in Rajasthan" are FORBIDDEN.
  If the cluster's quotes don't support a concrete clause, drop it.
- `confidence`: "high" if 3+ distinct video mentions, "medium" if 2, "low" if 1.
  Single-mention places need a concrete detail in the quote to qualify. Otherwise drop.
- Quality > quantity. Returning 2 strong discoveries from 14 weak clusters is correct.

GOOD example:
{
  "place_name": "Dudhsagar Falls",
  "why_specific": "Four-tier waterfall on the Goa-Karnataka border; reachable only by 4x4 jeep safari from Mollem during monsoon when the falls are at full flow.",
  "best_time": "monsoon (June-Sept), morning",
  "practical_tip": "Jeep safari ~₹500/person from Mollem; closed in heavy rain",
  "evidence_short_indices": [3, 7, 12],
  "tags": ["waterfall", "monsoon", "south-goa"],
  "confidence": "high"
}

BAD example (DO NOT EMIT):
{
  "place_name": "Baga Beach",
  "why_specific": "Popular beach in Goa with a lively atmosphere",
  "evidence_short_indices": [2]
}

OUTPUT: JSON {"discoveries": [...]}. Up to 8. Empty list is acceptable."""


def _format_videos_for_pass1(shorts: list[YouTubeShort], offset: int) -> str:
    """Render videos for Pass 1. `offset` is the global video_index of shorts[0]."""
    lines: list[str] = []
    for i, s in enumerate(shorts):
        idx = offset + i
        desc = (s.description or "").strip().replace("\n", " ")
        if len(desc) > 300:
            desc = desc[:300] + "…"
        tag_line = ""
        if s.tags:
            tag_line = "\n    Tags: " + ", ".join(s.tags[:10])
        block = (
            f"[{idx}] @{s.channel_title} ({s.view_count:,} views, {s.duration_seconds}s)\n"
            f"    Title: {s.title}\n"
            f"    Description: {desc or '(none)'}"
            f"{tag_line}"
        )
        if s.transcript:
            block += f"\n    Transcript: {s.transcript}"
        lines.append(block)
    return "\n\n".join(lines)


async def _pass1_extract_batch(
    trip_params: TripParams, batch: list[YouTubeShort], offset: int
) -> list[_PlaceMention]:
    """Run Pass 1 on a single batch of videos. Returns mentions or [] on error."""
    llm = get_llm("youtube_agent")
    structured = llm.with_structured_output(_Pass1Output)

    dest_tokens = _destination_tokens(trip_params.destination)
    skip_list = ", ".join(sorted(t.title() for t in dest_tokens)) or "—"
    user = (
        f"Destination: {trip_params.destination}\n\n"
        f"For this destination, extract SUB-PLACES only — specific forts, palaces, "
        f"monuments, dishes, restaurants, beaches, viewpoints, neighborhoods, "
        f"markets, temples, festivals, waterfalls, lakes. Do NOT extract bare "
        f"city / state / country names. Especially do NOT extract: {skip_list}.\n\n"
        f"Videos:\n\n{_format_videos_for_pass1(batch, offset)}\n\n"
        f"List specific sub-place mentions for each video. Empty list is fine."
    )
    messages: list[Any] = [
        SystemMessage(content=_PASS1_SYSTEM),
        HumanMessage(content=user),
    ]
    try:
        result = await structured.ainvoke(messages)
        if not isinstance(result, _Pass1Output):
            result = _Pass1Output.model_validate(result)
        # Cap per-video to avoid one chatty video flooding the cluster.
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
        # One bad batch shouldn't kill the run — other batches still feed the cluster.
        logger.warning("youtube_agent.pass1_batch_failed offset=%d err=%s", offset, e)
        return []


async def _pass1_extract_all(
    trip_params: TripParams, shorts: list[YouTubeShort]
) -> list[_PlaceMention]:
    """Pass 1 across all videos in parallel batches."""
    batches: list[tuple[int, list[YouTubeShort]]] = []
    for start in range(0, len(shorts), PASS1_BATCH_SIZE):
        batches.append((start + 1, shorts[start : start + PASS1_BATCH_SIZE]))

    results = await asyncio.gather(
        *(_pass1_extract_batch(trip_params, batch, offset) for offset, batch in batches),
        return_exceptions=True,
    )
    mentions: list[_PlaceMention] = []
    for r in results:
        if isinstance(r, BaseException):
            continue
        mentions.extend(r)
    logger.info("youtube_agent.pass1 batches=%d mentions=%d", len(batches), len(mentions))
    return mentions


def _normalize_place_key(name: str) -> str:
    """Cluster key — lowercase, strip punctuation/extra spaces."""
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _destination_tokens(destination: str) -> set[str]:
    """Return a set of single-word lowercase tokens from the destination string.

    'Rajasthan, India' → {'rajasthan', 'india'}
    'Goa, India' → {'goa', 'india'}
    'Reykjavik, Iceland' → {'reykjavik', 'iceland'}

    These tokens, when matched as a cluster's full name, mean the cluster is
    the destination itself (too coarse) — drop it. Sub-place names like
    'Anjuna Beach' or 'Hawa Mahal' have multiple tokens and won't be dropped.
    """
    parts = re.split(r"[,/&]+|\band\b", destination.lower())
    tokens: set[str] = set()
    for p in parts:
        words = p.strip().split()
        if len(words) == 1 and words[0]:
            tokens.add(words[0])
    return tokens


def _is_destination_cluster(cluster_name: str, dest_tokens: set[str]) -> bool:
    """True if the cluster name is just the destination itself (too coarse)."""
    key = _normalize_place_key(cluster_name)
    if not key:
        return True
    # A cluster is the destination if its full key equals one of the
    # destination tokens, e.g. cluster "Goa" with dest tokens {'goa','india'}.
    return key in dest_tokens


def _cluster_mentions(
    mentions: list[_PlaceMention],
    n_videos: int,
    destination: str = "",
) -> list[tuple[str, list[_PlaceMention]]]:
    """Group mentions by normalized place_name. Drops mentions with bad indices.

    Also drops:
      - clusters matching GENERIC_TITLE_RE (categorical labels)
      - clusters that are just the destination itself when `destination` is
        a region/country (e.g. drop 'Goa' for trip to 'Goa, India')

    Returns a list of (canonical_place_name, mentions) sorted by:
      1. Distinct video count desc
      2. Total mentions desc
    Capped to MAX_CLUSTERS_FOR_PASS2.
    """
    dest_tokens = _destination_tokens(destination) if destination else set()

    by_key: dict[str, list[_PlaceMention]] = {}
    for m in mentions:
        if not (1 <= m.video_index <= n_videos):
            continue
        if GENERIC_TITLE_RE.match(m.place_name.strip()):
            continue
        if _is_destination_cluster(m.place_name, dest_tokens):
            continue
        key = _normalize_place_key(m.place_name)
        if not key:
            continue
        by_key.setdefault(key, []).append(m)

    clusters: list[tuple[str, list[_PlaceMention]]] = []
    for _key, ms in by_key.items():
        # Pick the most-common original spelling as the canonical name.
        names = [m.place_name.strip() for m in ms]
        canonical = max(set(names), key=names.count)
        clusters.append((canonical, ms))

    # Ranking key:
    #   1. multi-word bonus — "Hawa Mahal" should beat "Jaipur" so granular
    #      sub-places bubble up before whole-city clusters.
    #   2. distinct video count
    #   3. total mention count
    def _rank(c: tuple[str, list[_PlaceMention]]) -> tuple[int, int, int]:
        name, ms = c
        word_count = len(_normalize_place_key(name).split())
        multi_word_bonus = 1 if word_count >= 2 else 0
        return (
            multi_word_bonus,
            len({m.video_index for m in ms}),
            len(ms),
        )

    clusters.sort(key=_rank, reverse=True)
    return clusters[:MAX_CLUSTERS_FOR_PASS2]


def _format_clusters_for_pass2(
    clusters: list[tuple[str, list[_PlaceMention]]],
) -> str:
    """Render clusters as the LLM input for Pass 2."""
    blocks: list[str] = []
    for name, ms in clusters:
        videos = sorted({m.video_index for m in ms})
        cat = ms[0].category if ms else "other"
        header = (
            f"PLACE: {name}\n"
            f"  category: {cat}\n"
            f"  videos: {videos} ({len(videos)} distinct)"
        )
        quote_lines = []
        for m in ms[:6]:  # cap quotes shown to keep prompt compact
            q = m.quote.strip().replace("\n", " ")
            if len(q) > 220:
                q = q[:220] + "…"
            quote_lines.append(f'    [video {m.video_index}] "{q}"')
        blocks.append(header + "\n" + "\n".join(quote_lines))
    return "\n\n".join(blocks)


async def _pass2_synthesize(
    trip_params: TripParams,
    signals: TravelSignals,
    clusters: list[tuple[str, list[_PlaceMention]]],
) -> list[_ExtractedDiscovery]:
    """Pass 2 — turn clusters into final discoveries."""
    if not clusters:
        return []
    llm = get_llm("youtube_agent")
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
        f"Clusters of place mentions extracted from short-form YouTube videos:\n\n"
        f"{_format_clusters_for_pass2(clusters)}\n\n"
        f"Write up to 8 discoveries from these clusters. Drop weak clusters. "
        f"Use only the evidence shown. Empty list is acceptable."
    )
    messages: list[Any] = [
        SystemMessage(content=_PASS2_SYSTEM),
        HumanMessage(content=user),
    ]
    result = await structured.ainvoke(messages)
    if not isinstance(result, _ExtractionResult):
        result = _ExtractionResult.model_validate(result)
    logger.info("youtube_agent.pass2 returned=%d", len(result.discoveries))
    return result.discoveries


async def _extract_via_llm(
    trip_params: TripParams, signals: TravelSignals, shorts: list[YouTubeShort]
) -> list[_ExtractedDiscovery]:
    """Two-pass orchestrator. Public name kept stable for the run loop + tests."""
    mentions = await _pass1_extract_all(trip_params, shorts)
    if not mentions:
        logger.info("youtube_agent.two_pass: pass1 produced 0 mentions")
        return []

    clusters = _cluster_mentions(
        mentions, n_videos=len(shorts), destination=trip_params.destination
    )
    logger.info(
        "youtube_agent.two_pass clusters=%d (top: %s)",
        len(clusters),
        [name for name, _ in clusters[:5]],
    )
    if not clusters:
        return []

    return await _pass2_synthesize(trip_params, signals, clusters)


def _validate_and_dedupe(
    extracted: list[_ExtractedDiscovery], n_videos: int
) -> list[_ExtractedDiscovery]:
    """Apply Layer 2d (vagueness) + 2e (generic title) + cross-discovery dedupe.

    Filters in order:
      1. Drop if `place_name` matches GENERIC_TITLE_RE.
      2. Drop if `why_specific` matches VAGUE_PHRASE_RE.
      3. Drop if `evidence_short_indices` is empty or has invalid indices.
      4. Cluster by lowercased place_name; keep the entry with most evidence.
      5. Cap to MAX_DISCOVERIES_RETURNED, prefer high-confidence with most cites.
    """
    survivors: list[_ExtractedDiscovery] = []
    for d in extracted:
        body = d.why_specific.strip()
        if GENERIC_TITLE_RE.match(d.place_name.strip()):
            logger.info("validate.drop reason=generic_title place=%r", d.place_name)
            continue
        if len(body) < MIN_WHY_LENGTH:
            logger.info(
                "validate.drop reason=short_body chars=%d place=%r body=%r",
                len(body), d.place_name, body,
            )
            continue
        vmatch = VAGUE_PHRASE_RE.search(body)
        if vmatch:
            logger.info(
                "validate.drop reason=vague_phrase phrase=%r place=%r body=%r",
                vmatch.group(0), d.place_name, body,
            )
            continue
        if TAUTOLOGY_RE.match(body):
            logger.info(
                "validate.drop reason=tautology place=%r body=%r",
                d.place_name, body,
            )
            continue
        # Validate evidence indices fall in [1, n_videos].
        valid_indices = [i for i in d.evidence_short_indices if 1 <= i <= n_videos]
        if not valid_indices:
            logger.info(
                "validate.drop reason=no_evidence place=%r submitted_indices=%r",
                d.place_name, d.evidence_short_indices,
            )
            continue
        d.evidence_short_indices = valid_indices
        survivors.append(d)

    # Cluster by lowercased place_name; keep best (most evidence, then high confidence).
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    clusters: dict[str, _ExtractedDiscovery] = {}
    for d in survivors:
        key = d.place_name.lower().strip()
        existing = clusters.get(key)
        if existing is None:
            clusters[key] = d
            continue
        # Keep whichever has more evidence; tiebreak on confidence.
        if (len(d.evidence_short_indices), confidence_rank[d.confidence]) > (
            len(existing.evidence_short_indices),
            confidence_rank[existing.confidence],
        ):
            clusters[key] = d

    ranked = sorted(
        clusters.values(),
        key=lambda d: (
            confidence_rank[d.confidence],
            len(d.evidence_short_indices),
        ),
        reverse=True,
    )
    return ranked[:MAX_DISCOVERIES_RETURNED]


def _to_research_discoveries(
    extracted: list[_ExtractedDiscovery],
) -> list[ResearchDiscovery]:
    """Map validated _ExtractedDiscovery → ResearchDiscovery (wire schema).

    Combines `why_specific` + best_time + practical_tip into the body so
    downstream synthesizer sees the practical bits.
    """
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
                source="youtube",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Search fan-out
# ---------------------------------------------------------------------------


async def _search_fanout(queries: list[str]) -> list[YouTubeShort]:
    """Run all queries in parallel; dedupe results by video_id (keep first seen)."""
    results = await asyncio.gather(
        *(
            search_youtube_shorts(q, max_results=MAX_RESULTS_PER_QUERY)
            for q in queries
        ),
        return_exceptions=True,
    )

    seen: set[str] = set()
    merged: list[YouTubeShort] = []
    for q, res in zip(queries, results):
        if isinstance(res, BaseException):
            logger.warning("youtube_agent.search_failed query=%r err=%s", q, res)
            continue
        for s in res:
            if s.video_id in seen:
                continue
            seen.add(s.video_id)
            merged.append(s)
    logger.info(
        "youtube_agent.fanout queries=%d unique_videos=%d", len(queries), len(merged)
    )
    return merged


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_youtube_agent(
    trip_params: TripParams, signals: TravelSignals
) -> list[ResearchDiscovery]:
    """Return YouTube-derived discoveries for the trip.

    All errors are caught and result in []; the synthesizer continues with
    whatever the other agents found.
    """
    try:
        queries = _build_queries(trip_params, signals)
        logger.info("youtube_agent.start queries=%r", queries)

        shorts = await _search_fanout(queries)
        if not shorts:
            logger.warning("youtube_agent: 0 unique videos across all queries")
            return []

        filtered = _filter_quality(shorts)
        logger.info(
            "youtube_agent: %d videos after quality filter (from %d)",
            len(filtered),
            len(shorts),
        )
        if not filtered:
            return []

        # Cap before transcript fetch + LLM.
        candidates = filtered[:MAX_VIDEOS_FOR_LLM]
        await _enrich_with_transcripts(candidates)

        extracted = await _extract_via_llm(trip_params, signals, candidates)
        validated = _validate_and_dedupe(extracted, n_videos=len(candidates))
        discoveries = _to_research_discoveries(validated)
        logger.info(
            "youtube_agent.done extracted=%d kept=%d returned=%d",
            len(extracted),
            len(validated),
            len(discoveries),
        )
        return discoveries

    except RuntimeError as e:
        logger.error("youtube_agent config error: %s", e)
        return []
    except Exception as e:  # noqa: BLE001
        logger.exception("youtube_agent unexpected failure: %s", e)
        return []
