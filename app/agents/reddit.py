"""RedditAgent — extract traveler tips, warnings, and contrarian recommendations.

Why a separate agent (not a copy of YouTube)?
  Reddit content is fundamentally different:
    - YouTube Shorts mention place NAMES; Reddit threads carry TIPS, WARNINGS,
      "skip X go Y" contrarianism, scam alerts, road conditions, weather notes.
    - YouTube data is short and visual; Reddit is long-form text — post body +
      threaded comments — and our prompt has to compress that responsibly.
  So this agent's extraction schema centres on `topic` + `insight` (with a
  category: tip / warning / recommendation) rather than the YouTube agent's
  place-mention clustering.

Pipeline:
  1. _build_subreddits()  — destination → candidate subs (r/travel, r/solotravel,
                            r/india / r/bali / etc.). Pure Python.
  2. _build_queries()     — 4–6 queries from destination + signals:
                            tips, warnings, hidden gems, vibes, season.
                            Crowd_level=peak/very_peak adds an "avoid tourists"
                            modifier (per spec).
  3. search_many_with_rate_limit() — fan out across (query, sub) pairs with
                            1s sleep between calls (60 req/min budget).
  4. _filter_posts        — drop low-score / removed posts, dedupe.
  5. enrich_with_comments — fetch top 3 comments per surviving post.
  6. LLM extraction       — single-pass: thread → list of structured insights.
                            Pydantic schema enforces concrete content.
  7. _validate_and_dedupe — strip vague / generic outputs.

Failure modes (all return [] gracefully):
  - Reddit blocks our UA / 403s
  - All searches return 0
  - LLM call fails or produces only vague output
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, field_validator

from app.llm.factory import get_llm
from app.schemas import ResearchDiscovery, TripParams
from app.signals import TravelSignals
from app.skills import load_skill
from app.tools.reddit import (
    RedditPost,
    enrich_with_comments,
    search_many_with_rate_limit,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

MAX_QUERIES = 6
MAX_RESULTS_PER_QUERY = 8
MAX_POSTS_FOR_LLM = 10  # cap before fetching comments + sending to LLM
MAX_COMMENTS_PER_POST = 3  # spec'd top 3 comments
MIN_POST_SCORE = 5  # drop low-engagement noise
MAX_DISCOVERIES_RETURNED = 8

# Per-post text caps (token-budget hygiene — Reddit threads can be enormous).
MAX_BODY_CHARS = 500
MAX_COMMENT_CHARS = 350

# Destination-mention filter (post-fetch, pre-LLM).
# Drops posts whose title + first DEST_MENTION_BODY_PROBE chars of body don't
# mention any destination/sub-region token. Fixes the BENCHMARK failure mode
# where pan-India trip-report posts dominate destination-specific queries.
DEST_MENTION_BODY_PROBE = 300

# Destination-specific subreddits get this multiplier on their post quota
# relative to the two default generic subs (r/travel, r/solotravel).
DEST_SUB_WEIGHT = 2

# Single-shot LLM extraction batch (Reddit posts are textual; one call is fine
# unlike YouTube's two-pass clustering).
LLM_BATCH_SIZE = MAX_POSTS_FOR_LLM


# Destination-keyword → candidate subreddit. Substring match against
# lowercased destination. The 'travel' / 'solotravel' subs always run.
# Kept small + curated; expanded over time.
_DESTINATION_SUBREDDIT_MAP: dict[str, list[str]] = {
    # India + sub-regions
    "india": ["india", "IndiaTravel"],
    "goa": ["goa"],
    "manali": ["IndiaTravel", "ladakh", "himachal"],
    "himachal": ["himachal", "IndiaTravel"],
    "shimla": ["himachal", "IndiaTravel"],
    "kashmir": ["Kashmir", "IndiaTravel"],
    "ladakh": ["ladakh"],
    "kerala": ["Kerala"],
    "karnataka": ["karnataka", "IndiaTravel"],
    "tamil nadu": ["tamilnadu", "IndiaTravel"],
    "tamilnadu": ["tamilnadu", "IndiaTravel"],
    "chennai": ["Chennai", "tamilnadu"],
    "rajasthan": ["IndiaTravel", "rajasthan"],
    "udaipur": ["IndiaTravel", "rajasthan"],
    "jaisalmer": ["IndiaTravel", "rajasthan"],
    "jodhpur": ["IndiaTravel", "rajasthan"],
    "jaipur": ["IndiaTravel", "rajasthan"],
    "mumbai": ["mumbai"],
    "delhi": ["delhi"],
    "bangalore": ["bangalore"],
    # Indian states/regions — missing from original map
    "odisha": ["india", "IndiaTravel"],
    "puri": ["india", "IndiaTravel"],
    "west bengal": ["india", "IndiaTravel"],
    "kolkata": ["kolkata", "india"],
    "gujarat": ["gujarat", "india", "IndiaTravel"],
    "ahmedabad": ["india", "IndiaTravel"],
    "pondicherry": ["india", "IndiaTravel"],
    "coorg": ["india", "IndiaTravel"],
    # SEA
    "singapore": ["singapore", "travel", "solotravel"],
    "bali": ["bali", "indonesia"],
    "indonesia": ["indonesia"],
    "thailand": ["Thailand"],
    "bangkok": ["Bangkok", "Thailand"],
    "vietnam": ["VietNam"],
    "philippines": ["Philippines"],
    "japan": ["JapanTravel"],
    "tokyo": ["JapanTravel"],
    # Europe
    "paris": ["ParisTravelGuide", "Paris"],
    "italy": ["ItalyTravel"],
    "rome": ["ItalyTravel"],
    "spain": ["SpainTravel"],
    "barcelona": ["Barcelona"],
    "germany": ["germany"],
    "iceland": ["VisitingIceland"],
    "reykjavik": ["VisitingIceland"],
    "greece": ["GreeceTravel"],
    "portugal": ["portugal"],
    # Americas + Oceania
    "new york": ["AskNYC", "nyc"],
    "nyc": ["AskNYC", "nyc"],
    "mexico": ["mexicotravel"],
    "australia": ["australia"],
    "sydney": ["sydney"],
}

# Always include these generic subs.
_DEFAULT_SUBREDDITS = ["travel", "solotravel"]


# Generic / vague title patterns — drop these LLM outputs.
_VAGUE_INSIGHT_RE = re.compile(
    r"\bbe\s+careful\b"
    r"|\bbe\s+aware\b"
    r"|\bgood\s+vibes?\b"
    r"|\bvibrant\s+culture\b"
    r"|\brich\s+culture\b"
    r"|\bbeautiful\s+place\b"
    r"|\bmust[-\s]?visit\b"
    r"|\bsomething\s+for\s+everyone\b"
    r"|\bgreat\s+experience\b",
    re.IGNORECASE,
)

MIN_INSIGHT_LENGTH = 40

# Irrelevant / non-actionable negativity. Reddit threads about a destination are
# full of health-anxiety, political grievances, and generic country-bashing that
# are NOT itinerary-actionable — the "you might get kidney stones" / "so much
# corruption" class flagged in the Rajasthan benchmark (it leaked into Day 1).
# Kept high-precision so genuine, actionable warnings survive: a NAMED scam at a
# NAMED place, snow closures, monsoon flooding, etc. don't match these tokens.
_IRRELEVANT_NEGATIVITY_RE = re.compile(
    r"\bkidney\s+stones?\b"
    r"|\bcorrupt(?:ion)?\b"
    r"|\bbribe(?:s|ry)?\b"
    r"|\bdelhi\s+belly\b"
    r"|\btravell?er'?s?\s+diarr?h?oea\b"
    r"|\byou'?ll?\s+get\s+sick\b"
    r"|\bso\s+(?:dirty|filthy)\b"
    r"|\b(?:poverty|beggars?|slums?)\b"
    r"|\bthird[-\s]world\b"
    r"|\b(?:modi|bjp|congress\s+party|hindu[-\s]muslim|communal|riots?)\b"
    r"|\bscams?\s+everywhere\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# LLM output schema
# ---------------------------------------------------------------------------


_InsightCategory = Literal["tip", "warning", "recommendation"]


class _ExtractedInsight(BaseModel):
    """One Reddit-derived insight. Maps to ResearchDiscovery."""

    topic: str = Field(
        ...,
        min_length=2,
        max_length=120,
        description=(
            "Short topic title — typically a place / dish / route / scam / "
            "neighbourhood. Concrete proper noun preferred (e.g. "
            "'Anjuna Flea Market', 'Rohtang Pass road conditions', "
            "'Tuk-tuk scam at Grand Palace'). NOT 'beaches' / 'food' / 'safety'."
        ),
    )
    insight: str = Field(
        ...,
        max_length=600,
        description=(
            "1-3 sentences (40+ chars) capturing the actual Reddit insight. "
            "Reddit-flavoured: warnings ('avoid X in monsoon, the road floods'), "
            "contrarian recs ('skip Baga, go to Cola Beach'), road/weather "
            "conditions, scam alerts, hyper-local food tips. Quote the gist of "
            "what redditors said — don't paraphrase into generic guidebook text."
        ),
    )
    category: _InsightCategory = Field(
        ...,
        description="'warning' for things to avoid; 'tip' for practical advice "
        "(timing, transport, prices); 'recommendation' for places/dishes locals "
        "actually rate.",
    )
    evidence_post_indices: list[int] = Field(
        ...,
        min_length=1,
        description="Indices [N] of the Reddit posts in the input that support "
        "this insight. REQUIRED — empty means drop the insight.",
    )
    # `tags` and `confidence` defaulted because smaller models (8B-class) often
    # skip them entirely. Final mapping in `_to_research_discoveries` fills any
    # gaps so the wire schema stays valid.
    tags: list[str] = Field(default_factory=list, max_length=5)
    confidence: Literal["high", "medium", "low"] = "low"

    # Smaller models (notably Groq llama-3.1-8b-instant) often emit
    # `evidence_post_indices` as a JSON-encoded *string* like "[1, 4]" instead
    # of a list. Coerce on input so the whole batch doesn't fail validation
    # over a formatting nit. Also coerce single ints, single strings, etc.
    @field_validator("evidence_post_indices", mode="before")
    @classmethod
    def _coerce_indices(cls, v: Any) -> list[int]:
        if v is None:
            return []
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            s = v.strip().strip("[]")
            if not s:
                return []
            parts = [p.strip() for p in s.split(",") if p.strip()]
            return [int(p) for p in parts if p.lstrip("-").isdigit()]
        if isinstance(v, list):
            out: list[int] = []
            for x in v:
                if isinstance(x, int):
                    out.append(x)
                elif isinstance(x, str) and x.strip().lstrip("-").isdigit():
                    out.append(int(x.strip()))
            return out
        return []

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_tags(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            # Some small models emit comma-joined string instead of list.
            return [t.strip() for t in v.split(",") if t.strip()]
        if isinstance(v, list):
            return [str(t).strip() for t in v if str(t).strip()]
        return []


class _ExtractionResult(BaseModel):
    insights: list[_ExtractedInsight]


# ---------------------------------------------------------------------------
# Subreddit + query construction
# ---------------------------------------------------------------------------


def _build_subreddits(trip_params: TripParams) -> list[str]:
    """Resolve candidate subreddits for the destination.

    Always includes r/travel + r/solotravel; adds destination-specific subs
    when a substring of the destination matches our map.
    """
    dest_lower = trip_params.destination.lower()
    subs: list[str] = list(_DEFAULT_SUBREDDITS)
    for key, sub_list in _DESTINATION_SUBREDDIT_MAP.items():
        if key in dest_lower:
            for s in sub_list:
                if s not in subs:
                    subs.append(s)
    return subs


def _build_queries(trip_params: TripParams, signals: TravelSignals) -> list[str]:
    """Return 4–6 broad queries covering all vibe clusters.

    L0 broad-mode design: the first-vibe Q4 slot is replaced with a neutral
    activities query so the cached pool is vibe-agnostic. Q1–Q3 were already
    vibe-neutral; Q5 (season) and Q6 (festival) are destination+time signals,
    not user preference, so they stay.

    Known limitation: Q6 (festival) and Q3 (crowd branch) are shaped by the
    first cold-miss user's travel dates. This is a destination+time signal
    (not user preference) and the contamination risk is low.
    """
    dest = trip_params.destination.strip()
    queries: list[str] = []

    # Q1 (always): tips — Reddit's most common and useful format.
    queries.append(f"{dest} tips")

    # Q2 (always): warnings — Reddit's superpower.
    queries.append(f"{dest} what to avoid")

    # Q3: crowd-level-driven discovery. crowd_level is a destination+time
    # signal (derived from dates), not a user preference — keeping the branch.
    if signals.crowd_level in {"peak", "very_peak"}:
        queries.append(f"{dest} hidden gems avoid tourists")
    else:
        queries.append(f"{dest} hidden gems")

    # Q4: broad activities query — replaces the old user-vibe slot.
    # Covers adventure, cultural, and relaxation angles without user bias.
    queries.append(f"{dest} things to do activities")

    # Q5: season — informative buckets only. Destination+time signal, not user.
    if signals.season in {"monsoon", "winter", "summer", "peak"}:
        queries.append(f"{dest} {signals.season}")

    # Q6: festival — also destination+time, not user preference.
    if signals.active_festivals:
        queries.append(f"{dest} {signals.active_festivals[0]}")

    # Dedupe + cap.
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
        if len(out) >= MAX_QUERIES:
            break
    return out


def _build_query_subreddit_pairs(
    queries: list[str], subreddits: list[str]
) -> list[tuple[str, str]]:
    """Build the cross-product of (query, sub) but capped to keep request count
    under the rate budget (≈ 60 req/min, 1s sleep → 60 req/min headroom).

    Strategy: every query against the 2 default subs, plus every query against
    the FIRST destination-specific sub. Cap at MAX_QUERIES * 3 pairs.
    """
    pairs: list[tuple[str, str]] = []
    # Default subs (always 2): travel + solotravel
    default_subs = [s for s in subreddits if s in _DEFAULT_SUBREDDITS]
    extra_subs = [s for s in subreddits if s not in _DEFAULT_SUBREDDITS]

    for q in queries:
        for sub in default_subs:
            pairs.append((q, sub))
        if extra_subs:
            pairs.append((q, extra_subs[0]))  # only the most-specific extra sub

    return pairs[: MAX_QUERIES * 3]


# ---------------------------------------------------------------------------
# Quality filtering
# ---------------------------------------------------------------------------


_DEST_SPLIT_RE = re.compile(r"[,/&]+|\band\b", re.IGNORECASE)


def _destination_tokens(destination: str) -> set[str]:
    """Tokens that count as 'this post mentions the destination'.

    Includes the full destination string and each comma/and-separated part,
    each as a lowercased multi-token phrase. We deliberately include
    short single tokens too — for "Goa, India" we want any of {"goa, india",
    "goa", "india"} to count. For multi-word names like "New York" we keep
    the joined phrase so we don't falsely match "new" alone.
    """
    lowered = destination.lower().strip()
    if not lowered:
        return set()
    tokens: set[str] = {lowered}
    for part in _DEST_SPLIT_RE.split(lowered):
        p = part.strip()
        if p:
            tokens.add(p)
    return {t for t in tokens if t}


def _post_mentions_destination(post: RedditPost, dest_tokens: set[str]) -> bool:
    """True if title or first DEST_MENTION_BODY_PROBE chars of body name the destination.

    Handles the BENCHMARK failure: pan-India trip reports surface for queries
    like 'Manali tips' because they tag "India" / "travel" subs broadly. Such
    posts have no Manali content in title or opening paragraph — drop them
    before sending to the LLM.

    Matches on word boundaries so "india" doesn't match "indian SIM card".
    """
    if not dest_tokens:
        return True
    haystack = (post.title or "").lower()
    body = (post.selftext or "").lower()[:DEST_MENTION_BODY_PROBE]
    haystack = haystack + " " + body
    for tok in dest_tokens:
        # Word-boundary regex match; tokens are short (≤ a few words) so
        # compile-per-call is fine. Escape to handle punctuation in tokens
        # (e.g. the joined-phrase "goa, india").
        pattern = r"\b" + re.escape(tok) + r"\b"
        if re.search(pattern, haystack):
            return True
    return False


def _filter_posts(
    posts: list[RedditPost],
    dest_tokens: set[str] | None = None,
    default_subs: set[str] | None = None,
) -> list[RedditPost]:
    """Drop low-score posts and pan-destination noise; dedupe by title.

    Strategy:
      1. score floor (MIN_POST_SCORE).
      2. dedupe across cross-posts by title, keeping the highest-scoring copy.
      3. destination-mention filter (title or first chars of body).
      4. weighted budget: destination-specific subs get DEST_SUB_WEIGHT× the
         per-sub quota of generic default subs (travel, solotravel).
    """
    survivors = [p for p in posts if p.score >= MIN_POST_SCORE]

    by_title: dict[str, RedditPost] = {}
    for p in survivors:
        key = p.title.lower().strip()
        existing = by_title.get(key)
        if existing is None or p.score > existing.score:
            by_title[key] = p
    deduped = sorted(by_title.values(), key=lambda p: p.score, reverse=True)

    if dest_tokens:
        before = len(deduped)
        deduped = [p for p in deduped if _post_mentions_destination(p, dest_tokens)]
        dropped = before - len(deduped)
        if dropped:
            logger.info(
                "reddit.filter.dropped_off_topic count=%d kept=%d",
                dropped,
                len(deduped),
            )

    if not default_subs:
        return deduped[:MAX_POSTS_FOR_LLM]

    # Weighted quota: per-sub cap that's higher for destination-specific subs.
    # MAX_POSTS_FOR_LLM is the overall cap; allocate within that, prioritising
    # destination subs by giving them more headroom and filling generic last.
    per_default = max(1, MAX_POSTS_FOR_LLM // (DEST_SUB_WEIGHT + 1))
    per_dest = per_default * DEST_SUB_WEIGHT

    kept: list[RedditPost] = []
    seen_ids: set[str] = set()
    by_sub_count: dict[str, int] = {}
    # First pass: destination-specific subs only.
    for p in deduped:
        if p.post_id in seen_ids:
            continue
        if p.subreddit in default_subs:
            continue
        if by_sub_count.get(p.subreddit, 0) >= per_dest:
            continue
        kept.append(p)
        seen_ids.add(p.post_id)
        by_sub_count[p.subreddit] = by_sub_count.get(p.subreddit, 0) + 1
        if len(kept) >= MAX_POSTS_FOR_LLM:
            return kept
    # Second pass: fill with default subs up to per-default cap each.
    for p in deduped:
        if p.post_id in seen_ids:
            continue
        if p.subreddit not in default_subs:
            continue
        if by_sub_count.get(p.subreddit, 0) >= per_default:
            continue
        kept.append(p)
        seen_ids.add(p.post_id)
        by_sub_count[p.subreddit] = by_sub_count.get(p.subreddit, 0) + 1
        if len(kept) >= MAX_POSTS_FOR_LLM:
            break
    return kept


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------


_REDDIT_SYSTEM = load_skill("reddit_research")


def _format_posts_for_prompt(posts: list[RedditPost]) -> str:
    blocks: list[str] = []
    for i, p in enumerate(posts, start=1):
        body = " ".join((p.selftext or "").split())
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS] + "…"
        block = (
            f"[{i}] r/{p.subreddit} (score {p.score})\n"
            f"  Title: {p.title}\n"
            f"  Body: {body or '(link post)'}"
        )
        if p.comments:
            block += "\n  Top comments:"
            for j, c in enumerate(p.comments, start=1):
                c_clean = " ".join(c.split())
                if len(c_clean) > MAX_COMMENT_CHARS:
                    c_clean = c_clean[:MAX_COMMENT_CHARS] + "…"
                block += f'\n    [c{j}] "{c_clean}"'
        blocks.append(block)
    return "\n\n".join(blocks)


async def _extract_via_llm(
    trip_params: TripParams, signals: TravelSignals, posts: list[RedditPost]
) -> list[_ExtractedInsight]:
    """Single-pass extraction. Reddit content is textual + dense, so unlike
    YouTube we don't need a clustering pre-pass."""
    if not posts:
        return []

    llm = get_llm("reddit_agent")
    # `json_mode` instead of the default function-calling: smaller Groq models
    # (e.g. llama-3.1-8b-instant) regularly emit malformed function-call args
    # that Groq's API 400s on before we ever see them. JSON mode returns the
    # raw model JSON string, which our Pydantic coercion validators can heal.
    try:
        structured = llm.with_structured_output(_ExtractionResult, method="json_mode")
    except Exception:  # noqa: BLE001
        # Provider doesn't support method kwarg → fall back to default.
        structured = llm.with_structured_output(_ExtractionResult)

    festival_line = (
        f"Active festivals during trip: {', '.join(signals.active_festivals)}\n"
        if signals.active_festivals
        else ""
    )
    crowd_line = (
        "Trip is during PEAK season — bias insights toward avoiding crowds and "
        "tourist traps; surface hidden alternatives.\n"
        if signals.crowd_level in {"peak", "very_peak"}
        else ""
    )

    user = (
        f"Destination: {trip_params.destination}\n"
        f"Trip dates: {trip_params.date_from} to {trip_params.date_to}\n"
        f"Season: {signals.season} (crowd level: {signals.crowd_level})\n"
        f"{festival_line}"
        f"{crowd_line}"
        f"Vibes: {', '.join(trip_params.vibes) if trip_params.vibes else '—'}\n"
        + (f"Traveler's own words (highest priority): {trip_params.preferences}\n" if trip_params.preferences else "")
        + f"\nReddit posts (with top comments):\n\n"
        f"{_format_posts_for_prompt(posts)}\n\n"
        f"Extract up to 8 concrete insights about {trip_params.destination}. "
        f"Empty list is acceptable if posts don't carry concrete {trip_params.destination}-"
        f"specific content."
    )
    system = _REDDIT_SYSTEM.format(destination=trip_params.destination)
    messages: list[Any] = [
        SystemMessage(content=system),
        HumanMessage(content=user),
    ]
    result = await structured.ainvoke(messages)
    if not isinstance(result, _ExtractionResult):
        result = _ExtractionResult.model_validate(result)
    logger.info("reddit_agent.llm_extracted=%d", len(result.insights))
    return result.insights


# ---------------------------------------------------------------------------
# Validation + mapping
# ---------------------------------------------------------------------------


def _validate_and_dedupe(
    extracted: list[_ExtractedInsight], n_posts: int
) -> list[_ExtractedInsight]:
    """Drop vague / unsupported insights; dedupe by topic; rank."""
    survivors: list[_ExtractedInsight] = []
    for ins in extracted:
        body = ins.insight.strip()
        if len(body) < MIN_INSIGHT_LENGTH:
            logger.info("reddit.validate.drop reason=short topic=%r", ins.topic)
            continue
        vmatch = _VAGUE_INSIGHT_RE.search(body)
        if vmatch:
            logger.info(
                "reddit.validate.drop reason=vague phrase=%r topic=%r",
                vmatch.group(0),
                ins.topic,
            )
            continue
        # Drop non-actionable negativity (health scares, corruption, politics,
        # generic country-bashing). Checks topic + body so "kidney stones in
        # Rajasthan" is dropped whether the phrase lands in the title or body.
        nmatch = _IRRELEVANT_NEGATIVITY_RE.search(f"{ins.topic} {body}")
        if nmatch:
            logger.info(
                "reddit.validate.drop reason=irrelevant_negativity phrase=%r topic=%r",
                nmatch.group(0),
                ins.topic,
            )
            continue
        valid_indices = [i for i in ins.evidence_post_indices if 1 <= i <= n_posts]
        if not valid_indices:
            logger.info("reddit.validate.drop reason=no_evidence topic=%r", ins.topic)
            continue
        ins.evidence_post_indices = valid_indices
        survivors.append(ins)

    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    clusters: dict[str, _ExtractedInsight] = {}
    for ins in survivors:
        key = ins.topic.lower().strip()
        existing = clusters.get(key)
        if existing is None:
            clusters[key] = ins
            continue
        if (len(ins.evidence_post_indices), confidence_rank[ins.confidence]) > (
            len(existing.evidence_post_indices),
            confidence_rank[existing.confidence],
        ):
            clusters[key] = ins

    ranked = sorted(
        clusters.values(),
        key=lambda d: (
            confidence_rank[d.confidence],
            len(d.evidence_post_indices),
        ),
        reverse=True,
    )
    return ranked[:MAX_DISCOVERIES_RETURNED]


def _to_research_discoveries(
    extracted: list[_ExtractedInsight],
) -> list[ResearchDiscovery]:
    out: list[ResearchDiscovery] = []
    for ins in extracted:
        # Prefix the body with the category so the synthesizer can quickly
        # tell warnings apart from recommendations.
        prefix = {
            "warning": "Warning",
            "tip": "Tip",
            "recommendation": "Locals recommend",
        }.get(ins.category, "Tip")
        body = f"{prefix}: {ins.insight.strip()}"

        tags = [t.strip() for t in ins.tags if t.strip()][:3] or ["reddit"]
        # Ensure category surfaces in tags so downstream synth can weight it.
        if ins.category not in {t.lower() for t in tags} and len(tags) < 3:
            tags.append(ins.category)

        out.append(
            ResearchDiscovery(
                id=str(uuid.uuid4()),
                title=ins.topic.strip(),
                body=body,
                tags=tags[:3],
                source="reddit",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_reddit_agent(
    trip_params: TripParams, signals: TravelSignals
) -> list[ResearchDiscovery]:
    """Return Reddit-derived discoveries for the trip.

    All errors are caught and result in []; the synthesizer continues with
    whatever the other agents found.
    """
    try:
        subreddits = _build_subreddits(trip_params)
        queries = _build_queries(trip_params, signals)
        pairs = _build_query_subreddit_pairs(queries, subreddits)
        logger.info(
            "reddit_agent.start subs=%r queries=%r pairs=%d",
            subreddits,
            queries,
            len(pairs),
        )
        if not pairs:
            return []

        raw_posts = await search_many_with_rate_limit(pairs, limit_per_query=MAX_RESULTS_PER_QUERY)
        if not raw_posts:
            logger.warning("reddit_agent: 0 posts across all queries")
            return []

        dest_tokens = _destination_tokens(trip_params.destination)
        filtered = _filter_posts(
            raw_posts,
            dest_tokens=dest_tokens,
            default_subs=set(_DEFAULT_SUBREDDITS),
        )
        logger.info(
            "reddit_agent: %d posts after filter (from %d, dest_tokens=%r)",
            len(filtered),
            len(raw_posts),
            sorted(dest_tokens),
        )
        if not filtered:
            return []

        await enrich_with_comments(filtered, max_comments=MAX_COMMENTS_PER_POST)

        extracted = await _extract_via_llm(trip_params, signals, filtered)
        validated = _validate_and_dedupe(extracted, n_posts=len(filtered))
        discoveries = _to_research_discoveries(validated)
        logger.info(
            "reddit_agent.done extracted=%d kept=%d returned=%d",
            len(extracted),
            len(validated),
            len(discoveries),
        )
        return discoveries

    except RuntimeError as e:
        logger.error("reddit_agent config error: %s", e)
        return []
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("reddit_agent unexpected failure: %s", e)
        return []
