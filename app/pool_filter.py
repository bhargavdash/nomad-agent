"""Read-time vibe filter — narrows the L0 destination pool for the synthesizer.

Why this exists
---------------
The L0 cache stores a broad research pool (up to ~35 discoveries) covering all
four vibe clusters for a given destination+season. That breadth is what lets
the cache be shared across users. But the synthesizer degrades past ~12–15
inputs because the LLM loses context over a too-large candidate set.

This module is the bridge: it scores the full pool against the current user's
signals and returns a tighter subset that the synthesizer can reason over
precisely.

Scoring
-------
Two independent signals — higher is better:

  1. Cross-source agreement (primary): the same named place appearing from
     youtube, reddit, AND blog sources is well-validated by the community.
     A place covered by N distinct sources scores N on this axis.

  2. Vibe token overlap (secondary): the user's `signals.query_modifiers`
     contain keyword tokens like ["adventure", "rain-friendly activities",
     "offbeat waterfall"]. A discovery whose tags share tokens with these
     modifiers scores 1 per matching token. This is a lightweight proxy for
     "how relevant is this place to what the user actually wants?"

No LLM, no I/O. Pure Python, O(n). Deterministic for a given (pool, signals)
pair; non-deterministic across different users if their signals differ (which
is the intended personalisation surface).

Known limitations
-----------------
• Cross-source agreement detects places by fuzzy title match (stop-word
  stripped, lowercased). Very different names for the same place (e.g.
  "Basilica of Bom Jesus" vs "Bom Jesus Church") won't merge — they stay
  as two entries and each scores 1.

• Vibe overlap is best-effort. Agents now run neutral broad queries, so
  extracted tags reflect a place's natural attributes rather than which
  vibe query surfaced it. `query_modifiers` is the best available proxy
  without a dedicated vibe_tags field.

• Festival / crowd-level contamination: the L0 pool may contain some
  date-specific items (e.g. Diwali festival recommendations) from the
  first cold-filling user's query set. The filter scores these neutrally;
  the synthesizer's prompt — which receives the user's `signals` including
  `active_festivals` — handles their appropriate weight at generation time.
"""

from __future__ import annotations

import logging
import re

from app.schemas import ResearchDiscovery
from app.signals import TravelSignals

logger = logging.getLogger(__name__)

# Max discoveries fed to the synthesizer. Above this the LLM loses focus.
# This is deliberately a few above AIItinerary.discoveries max (12) so the
# synthesizer still has room to select and has some cross-source agreement
# signal to reason over.
DEFAULT_MAX_ITEMS = 15

_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "in",
        "of",
        "at",
        "to",
        "for",
        "and",
        "or",
        "is",
        "it",
        "its",
        "on",
        "by",
        "with",
        "near",
        "from",
    }
)


def _normalize(title: str) -> str:
    """Lowercase, strip non-alphanumerics, drop stop words, return token set key."""
    tokens = re.sub(r"[^a-z0-9 ]+", " ", title.lower()).split()
    meaningful = [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]
    return " ".join(sorted(meaningful))  # sorted so "beach anjuna" == "anjuna beach"


def _modifier_tokens(signals: TravelSignals) -> frozenset[str]:
    """Extract a flat token set from the user's query_modifiers."""
    tokens: set[str] = set()
    for modifier in signals.query_modifiers:
        for tok in re.sub(r"[^a-z]+", " ", modifier.lower()).split():
            if tok not in _STOP_WORDS and len(tok) > 2:
                tokens.add(tok)
    return frozenset(tokens)


def filter_pool_for_user(
    pool: list[ResearchDiscovery],
    signals: TravelSignals,
    max_items: int = DEFAULT_MAX_ITEMS,
) -> list[ResearchDiscovery]:
    """Return up to `max_items` discoveries scored by vibe relevance.

    If the pool is already within budget, it is returned unchanged.
    Scoring criteria (desc priority):
      1. Number of distinct sources mentioning the same normalised place name.
      2. Token overlap between discovery tags and the user's query_modifiers.
    Ties fall back to original insertion order (stable sort).
    """
    if len(pool) <= max_items:
        return pool

    # --- build cross-source agreement map ------------------------------------
    # normalized_key → set of source strings
    source_map: dict[str, set[str]] = {}
    for d in pool:
        key = _normalize(d.title)
        if key:
            if key not in source_map:
                source_map[key] = set()
            source_map[key].add(d.source)

    mod_tokens = _modifier_tokens(signals)

    def _score(d: ResearchDiscovery) -> tuple[int, int]:
        # primary: how many sources agree on this place
        key = _normalize(d.title)
        source_count = len(source_map.get(key, {d.source}))

        # secondary: vibe token overlap via tags
        if d.tags and mod_tokens:
            tag_tokens: set[str] = set()
            for tag in d.tags:
                for tok in re.sub(r"[^a-z]+", " ", tag.lower()).split():
                    if len(tok) > 2:
                        tag_tokens.add(tok)
            vibe_overlap = len(tag_tokens & mod_tokens)
        else:
            vibe_overlap = 0

        return (source_count, vibe_overlap)

    scored = sorted(pool, key=_score, reverse=True)
    filtered = scored[:max_items]

    logger.info(
        "pool_filter → total=%d filtered=%d vibe_cluster=%r modifier_tokens=%d",
        len(pool),
        len(filtered),
        signals.vibe_cluster,
        len(mod_tokens),
    )
    return filtered
