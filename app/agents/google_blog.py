"""GoogleBlogAgent — curated travel blog insights via Tavily Search.

Why blogs (not Reddit/YouTube)?
  Travel blogs carry what Reddit/YouTube miss: curated *lists with reasoning*,
  historical and cultural context, logistics, and itinerary structure. They
  excel for luxury/premium content, first-timer overviews, cultural depth, and
  festival guides — exactly the vibes where Reddit tips feel thin and YouTube
  Shorts are too visual.

Pipeline:
  1. _build_queries()     — 3–4 queries: season/vibe-aware base, vibe-specific,
                            budget-aware, optional festival query.
  2. search_fanout()      — parallel Tavily searches; dedupe by URL.
  3. LLM extraction       — single pass: article excerpts → structured
                            discoveries. Schema differs from Reddit: place_name
                            + description + best_for + practical_info.
                            No clustering needed — blog text is already curated.
  4. _validate_and_dedupe — drop vague / generic outputs; cap at 8.

Failure modes (all return [] gracefully):
  - TAVILY_API_KEY missing (tool short-circuits before any network call)
  - Tavily quota / API error
  - 0 results across all queries
  - LLM fails or all outputs fail validation
"""

from __future__ import annotations

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
from app.tools.tavily_search import TavilyResult, search_fanout

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

MAX_QUERIES = 4
MAX_RESULTS_PER_QUERY = 5
MAX_ARTICLES_FOR_LLM = 15          # cap before sending to LLM
MAX_ARTICLE_CONTENT_CHARS = 500    # trim long excerpts to keep prompts lean
MAX_DISCOVERIES_RETURNED = 8
MIN_DESCRIPTION_LENGTH = 40


# ---------------------------------------------------------------------------
# Vagueness filter — blog-specific patterns
# ---------------------------------------------------------------------------

_VAGUE_PHRASE_RE = re.compile(
    r"\bstunning\b"
    r"|\bbreathtaking\b"
    r"|\bpicturesque\b"
    r"|\bvibrant\s+culture\b"
    r"|\brich\s+culture\b"
    r"|\bnatural\s+beauty\b"
    r"|\bmust[-\s]?visit\b"
    r"|\bsomething\s+for\s+everyone\b"
    r"|\bworld[-\s]?class\b"
    r"|\bunique\s+experience\b"
    r"|\bbeautiful\s+(?:beaches?|landscapes?|architectures?|views?|scenery|places?)\b"
    r"|\b(?:with|featuring)\s+(?:scenic|beautiful|stunning)\s+views?\b"
    r"|\bwonderful\s+destination\b"
    r"|\bamazing\s+(?:place|destination|experience|views?)\b",
    re.IGNORECASE,
)

# Stock travel-blog templates the LLM falls into when articles are thin. These
# fired on nearly every Manali blog discovery in the BENCHMARK run, e.g.
# "A temple to visit in Manali, part of a travel guide that includes where to
# go, eat, stay, and shop." Reject the discovery if the body matches.
_BLOG_TEMPLATE_RE = re.compile(
    r"\ba\s+\S{2,30}\s+to\s+visit\s+in\s+\S.{0,30}?,?\s*part\s+of\s+a\s+travel\s+guide\b"
    r"|\bperfect\s+(?:place|spot)\s+for\s+everyone\b"
    r"|\bwhere\s+to\s+(?:go|eat|stay|shop)(?:\s*,\s*(?:go|eat|stay|shop))+\b"
    r"|\bbest\s+for\s+(?:everyone|all|families\s+and\s+couples)\b"
    r"|\bguide\s+that\s+includes\s+(?:where|what)\s+to\s+(?:go|eat|stay|shop)\b"
    r"|\bpart\s+of\s+a\s+travel\s+guide\b"
    # "Best for: <audience>" template bleeding from the `best_for` field into the
    # description. The `best_for` field exists for this exact content — when it
    # appears inline, the description is structurally a stub.
    r"|\bbest\s+for:\s+\w+"
    # "A <noun> to visit in <place>," / "An <noun> in <place>," openers.
    r"|^(?:A|An)\s+\w+\s+(?:to\s+visit\s+in|in)\s+\w+,",
    re.IGNORECASE,
)

# Heuristic: "contains a proper noun beyond the place_name itself".
# A capitalized 3+ letter word with no leading sentence-start period.
# Used by _has_named_entity_beyond_place_name.
_CAPWORD_RE = re.compile(r"\b([A-Z][a-zA-Z]{2,})\b")
_COMMON_NON_ENTITY_CAPS = {
    "The", "This", "That", "These", "Those", "It", "Its", "If", "For", "With",
    "When", "Where", "Why", "How", "What", "And", "But", "Or", "So",
    "Best", "First", "Most", "Local", "Locals", "Tip", "Tips", "Note", "Notes",
    "Info", "Guide", "Day", "Days", "Visit", "Visiting", "Try",
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
}


def _has_named_entity_beyond_place_name(body: str, place_name: str) -> bool:
    """True if `body` contains ≥1 proper-noun-ish token not part of `place_name`.

    This is the lightweight enforcement of "demand named entities" — we don't
    insist on cuisine vs. dynasty vs. trek specifically (too brittle in regex),
    but we DO insist the body name something concrete beyond the place title
    itself. Catches stock templated descriptions like "A temple to visit in
    Manali" which name no other entity at all.
    """
    place_tokens = {t.lower() for t in _CAPWORD_RE.findall(place_name)}
    for match in _CAPWORD_RE.findall(body):
        if match in _COMMON_NON_ENTITY_CAPS:
            continue
        if match.lower() in place_tokens:
            continue
        return True
    return False


# ---------------------------------------------------------------------------
# LLM output schema
# ---------------------------------------------------------------------------


class _BlogPlace(BaseModel):
    """One blog-derived travel discovery. Maps to ResearchDiscovery."""

    place_name: str = Field(
        ...,
        min_length=2,
        max_length=120,
        description=(
            "A specific proper noun: restaurant, monument, viewpoint, district, "
            "dish, festival, trail, hotel, café, experience. NOT generic labels "
            "like 'local markets', 'the beaches', 'tourist spots'."
        ),
    )
    description: str = Field(
        ...,
        max_length=600,
        description=(
            "1-3 sentences (40+ chars) of curated insight grounded in what the "
            "articles say. Include what makes it special, the context, who it's "
            "best for. Avoid banned words: stunning, breathtaking, vibrant culture, "
            "must-visit, rich culture, natural beauty. Drop the discovery if you "
            "can't write a concrete sentence."
        ),
    )
    best_for: str | None = Field(
        default=None,
        max_length=120,
        description=(
            "Who benefits most: e.g. 'couples, sunset photography', "
            "'first-timers needing orientation', 'food lovers', "
            "'history enthusiasts'. Null if the articles don't indicate."
        ),
    )
    practical_info: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Logistics from the articles: entry fee, opening hours, "
            "best season, reservation advice, transport note. Null if unknown."
        ),
    )
    evidence_article_indices: list[int] = Field(
        ...,
        min_length=1,
        description=(
            "1-based indices of articles in the input that support this discovery. "
            "REQUIRED — empty means drop it."
        ),
    )
    tags: list[str] = Field(default_factory=list, max_length=5)
    confidence: Literal["high", "medium", "low"] = "low"
    source_type: Literal["blog", "maps"] = Field(
        default="blog",
        description=(
            "'blog' for editorial content with reasoning; "
            "'maps' for generic tourist anchors with no distinctive insight "
            "(e.g. a famous landmark mentioned with no context). Default 'blog'."
        ),
    )

    @field_validator("evidence_article_indices", mode="before")
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
            return [t.strip() for t in v.split(",") if t.strip()]
        if isinstance(v, list):
            return [str(t).strip() for t in v if str(t).strip()]
        return []


class _BlogExtractionResult(BaseModel):
    places: list[_BlogPlace]


# Lightweight schema for the anchor-only pass. The LLM naturally uses `name`
# rather than `place_name`, so this schema matches that and we convert later.
class _AnchorEntry(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    description: str = Field(..., max_length=600)
    tags: list[str] = Field(default_factory=lambda: ["anchor_hint"])
    confidence: Literal["high", "medium", "low"] = "medium"
    evidence_article_indices: list[int] = Field(default_factory=list)


class _AnchorExtractionResult(BaseModel):
    places: list[_AnchorEntry]


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------


def _build_queries(trip_params: TripParams, signals: TravelSignals) -> list[str]:
    """Return 3–4 Tavily queries shaped by destination, vibes, and budget.

    Queries are kept clean (no `-site:` syntax) because Tavily's
    `exclude_domains` handles filtering. Q1+Q2 cover the must-see anchors
    so famous attractions (Sentosa, Eiffel Tower, etc.) surface; Q3+Q4
    cover vibe/budget personalization. Festival/season context is conveyed
    to the LLM via the user message, not the queries.
    """
    dest = trip_params.destination.strip()
    queries: list[str] = []

    # Q1 (always): anchor / must-see coverage. Sprint 4 benchmark showed
    # Singapore missed Sentosa / Universal / SEA Aquarium because the prior
    # season-aware query never specifically asked for top attractions.
    queries.append(f"top attractions in {dest}")

    # Q2 (always): second anchor angle — different phrasing surfaces a
    # different mix of editorial sources.
    queries.append(f"must see {dest}")

    # Q3: first vibe if given — surfaces niche blog content matching user intent.
    if trip_params.vibes:
        first_vibe = trip_params.vibes[0].strip()
        if first_vibe:
            queries.append(f"{dest} {first_vibe} travel tips")

    # Q4: budget-aware. Luxury → hotel/restaurant recs; shoestring → budget tips.
    if signals.budget_tier == "luxury":
        queries.append(f"{dest} luxury hotels restaurants experiences")
    elif signals.budget_tier == "shoestring":
        queries.append(f"{dest} budget travel tips cheap eats")
    else:
        # Mid/premium → general "things to do" or itinerary angle.
        queries.append(f"{dest} itinerary things to do")

    # Dedupe while preserving order.
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


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------


_BLOG_SYSTEM = load_skill("blog_research")


def _format_articles_for_prompt(articles: list[TavilyResult]) -> str:
    blocks: list[str] = []
    for i, a in enumerate(articles, start=1):
        content = " ".join(a.content.split())
        if len(content) > MAX_ARTICLE_CONTENT_CHARS:
            content = content[:MAX_ARTICLE_CONTENT_CHARS] + "…"
        block = (
            f"[{i}] {a.title or '(no title)'}\n"
            f"  URL: {a.url or '—'}\n"
            f"  Excerpt: {content}"
        )
        blocks.append(block)
    return "\n\n".join(blocks)


async def _extract_via_llm(
    trip_params: TripParams,
    signals: TravelSignals,
    articles: list[TavilyResult],
) -> list[_BlogPlace]:
    """Single-pass LLM extraction from blog article excerpts."""
    if not articles:
        return []

    llm = get_llm("google_agent")
    try:
        structured = llm.with_structured_output(_BlogExtractionResult, method="json_mode")
    except Exception:  # noqa: BLE001
        structured = llm.with_structured_output(_BlogExtractionResult)

    festival_line = (
        f"Active festivals during trip: {', '.join(signals.active_festivals)}\n"
        if signals.active_festivals
        else ""
    )
    crowd_line = (
        "Peak season — highlight less-crowded alternatives and note booking requirements.\n"
        if signals.crowd_level in {"peak", "very_peak"}
        else ""
    )

    user = (
        f"Destination: {trip_params.destination}\n"
        f"Trip dates: {trip_params.date_from} to {trip_params.date_to}\n"
        f"Season: {signals.season} (crowd: {signals.crowd_level})\n"
        f"Budget: {signals.budget_tier}\n"
        f"{festival_line}"
        f"{crowd_line}"
        f"Vibes: {', '.join(trip_params.vibes) if trip_params.vibes else '—'}\n\n"
        f"Travel blog articles:\n\n"
        f"{_format_articles_for_prompt(articles)}\n\n"
        f"Extract up to 8 concrete discoveries. Empty list is acceptable if articles "
        f"carry no specific content."
    )
    messages: list[Any] = [
        SystemMessage(content=_BLOG_SYSTEM),
        HumanMessage(content=user),
    ]
    result = await structured.ainvoke(messages)
    if not isinstance(result, _BlogExtractionResult):
        result = _BlogExtractionResult.model_validate(result)
    logger.info("google_agent.llm_extracted=%d", len(result.places))
    return result.places


# ---------------------------------------------------------------------------
# Second-pass: vibe-agnostic anchor extraction
# ---------------------------------------------------------------------------

_ANCHOR_SYSTEM = load_skill("blog_anchor")


async def _extract_anchors_via_llm(
    destination: str,
    articles: list[TavilyResult],
) -> list[_BlogPlace]:
    """Vibe-agnostic second pass: extract famous tourist anchors from anchor-query articles.

    Uses _AnchorExtractionResult (with `name` field) so the LLM matches the schema
    naturally. Results are converted to _BlogPlace objects before returning.
    """
    if not articles:
        return []

    llm = get_llm("google_agent")
    try:
        structured = llm.with_structured_output(_AnchorExtractionResult, method="json_mode")
    except Exception:  # noqa: BLE001
        structured = llm.with_structured_output(_AnchorExtractionResult)

    user = (
        f"Destination: {destination}\n\n"
        f"Travel blog articles:\n\n"
        f"{_format_articles_for_prompt(articles)}\n\n"
        f"Extract 3-4 famous tourist attractions (theme parks, iconic landmarks, "
        f"must-see museums, famous districts). No food or restaurants."
    )
    messages: list[Any] = [
        SystemMessage(content=_ANCHOR_SYSTEM),
        HumanMessage(content=user),
    ]
    try:
        result = await structured.ainvoke(messages)
        if not isinstance(result, _AnchorExtractionResult):
            result = _AnchorExtractionResult.model_validate(result)
        blog_places: list[_BlogPlace] = []
        for entry in result.places:
            tags = entry.tags if "anchor_hint" in entry.tags else ["anchor_hint"] + entry.tags
            blog_places.append(
                _BlogPlace(
                    place_name=entry.name,
                    description=entry.description,
                    best_for=None,
                    practical_info=None,
                    evidence_article_indices=entry.evidence_article_indices or [1],
                    tags=tags,
                    confidence=entry.confidence,
                    source_type="blog",
                )
            )
        logger.info("google_agent.anchor_pass extracted=%d", len(blog_places))
        return blog_places
    except Exception:  # noqa: BLE001
        logger.exception("google_agent.anchor_pass failed")
        return []


# ---------------------------------------------------------------------------
# Validation + deduplication
# ---------------------------------------------------------------------------


def _validate_and_dedupe(
    extracted: list[_BlogPlace], n_articles: int
) -> list[_BlogPlace]:
    """Drop vague / templated / unsupported discoveries; dedupe by place_name; rank."""
    survivors: list[_BlogPlace] = []
    for place in extracted:
        desc = place.description.strip()
        if len(desc) < MIN_DESCRIPTION_LENGTH:
            logger.info(
                "google_agent.validate.drop reason=short place=%r", place.place_name
            )
            continue
        vmatch = _VAGUE_PHRASE_RE.search(desc)
        if vmatch:
            logger.info(
                "google_agent.validate.drop reason=vague phrase=%r place=%r",
                vmatch.group(0),
                place.place_name,
            )
            continue
        tmatch = _BLOG_TEMPLATE_RE.search(desc)
        if tmatch:
            logger.info(
                "google_agent.validate.drop reason=stock_template phrase=%r place=%r",
                tmatch.group(0),
                place.place_name,
            )
            continue
        # Demand at least one named entity beyond the place_name itself.
        # Catches templated descriptions that mention only the place title.
        if not _has_named_entity_beyond_place_name(desc, place.place_name):
            logger.info(
                "google_agent.validate.drop reason=no_named_entity place=%r body=%r",
                place.place_name,
                desc,
            )
            continue
        valid_indices = [i for i in place.evidence_article_indices if 1 <= i <= n_articles]
        if not valid_indices:
            logger.info(
                "google_agent.validate.drop reason=no_evidence place=%r",
                place.place_name,
            )
            continue
        place.evidence_article_indices = valid_indices
        survivors.append(place)

    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    clusters: dict[str, _BlogPlace] = {}
    for place in survivors:
        key = place.place_name.lower().strip()
        existing = clusters.get(key)
        if existing is None:
            clusters[key] = place
            continue
        if (len(place.evidence_article_indices), confidence_rank[place.confidence]) > (
            len(existing.evidence_article_indices),
            confidence_rank[existing.confidence],
        ):
            clusters[key] = place

    ranked = sorted(
        clusters.values(),
        key=lambda p: (
            confidence_rank[p.confidence],
            len(p.evidence_article_indices),
        ),
        reverse=True,
    )
    return ranked[:MAX_DISCOVERIES_RETURNED]


def _to_research_discoveries(
    validated: list[_BlogPlace],
) -> list[ResearchDiscovery]:
    out: list[ResearchDiscovery] = []
    for place in validated:
        body_parts = [place.description.strip()]
        if place.best_for:
            body_parts.append(f"Best for: {place.best_for.strip()}.")
        if place.practical_info:
            body_parts.append(f"Info: {place.practical_info.strip()}.")
        body = " ".join(p for p in body_parts if p)

        tags = [t.strip() for t in place.tags if t.strip()][:3] or ["blog"]
        source = place.source_type if place.source_type in {"blog", "maps"} else "blog"

        out.append(
            ResearchDiscovery(
                id=str(uuid.uuid4()),
                title=place.place_name.strip(),
                body=body,
                tags=tags,
                source=source,  # type: ignore[arg-type]
            )
        )
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_google_blog_agent(
    trip_params: TripParams, signals: TravelSignals
) -> list[ResearchDiscovery]:
    """Return blog-derived discoveries for the trip.

    All errors are caught and result in []; the synthesizer continues with
    whatever the other agents found.
    """
    try:
        queries = _build_queries(trip_params, signals)
        logger.info("google_agent.start queries=%r", queries)

        articles = await search_fanout(
            queries, max_results_per_query=MAX_RESULTS_PER_QUERY
        )
        if not articles:
            logger.warning("google_agent: 0 articles across all queries")
            return []

        # Cap before LLM to stay within token budget.
        capped = articles[:MAX_ARTICLES_FOR_LLM]
        logger.info(
            "google_agent: %d articles after fanout+dedupe (using %d)",
            len(articles),
            len(capped),
        )

        # Q1+Q2 are anchor queries ("top attractions", "must see") — use only
        # those articles for the anchor pass so the LLM sees anchor-rich content.
        anchor_article_count = min(len(articles), MAX_RESULTS_PER_QUERY * 2)
        anchor_articles = articles[:anchor_article_count]

        # Run anchor pass first (smaller, fewer tokens), then the vibe-aware pass.
        # Sequential order avoids spiking Groq TPM — parallel calls on the same
        # model exhaust the per-minute token budget and cause 429 cascades.
        anchor_extracted = await _extract_anchors_via_llm(trip_params.destination, anchor_articles)
        extracted = await _extract_via_llm(trip_params, signals, capped)

        # Merge: anchor results first so they survive dedup when place_name matches.
        merged_extracted = anchor_extracted + extracted
        validated = _validate_and_dedupe(merged_extracted, n_articles=len(capped))
        discoveries = _to_research_discoveries(validated)
        logger.info(
            "google_agent.done extracted=%d anchor=%d merged=%d kept=%d returned=%d",
            len(extracted),
            len(anchor_extracted),
            len(merged_extracted),
            len(validated),
            len(discoveries),
        )
        return discoveries

    except RuntimeError as e:
        logger.error("google_agent config error: %s", e)
        return []
    except Exception as e:  # noqa: BLE001
        logger.exception("google_agent unexpected failure: %s", e)
        return []
