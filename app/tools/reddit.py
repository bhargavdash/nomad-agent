"""Reddit JSON API tool — fetch authentic traveler tips, warnings, and contrarian
recommendations from `r/travel`, `r/solotravel`, and destination-specific subs.

Why Reddit?
  Reddit gives us what YouTube/blogs don't: warnings, "skip X go Y" contrarianism,
  scam alerts, neighbourhood-level granularity, and locals correcting outdated
  guidebook recs. The signal is in *threads* — post body + top comments together.

API:
  Public JSON endpoints, no auth. Reddit blocks the default httpx User-Agent,
  so we send a custom one. Rate limit is 60 req/min — we sleep 1s between
  outbound calls to stay well under it.

  - Search:    https://www.reddit.com/r/{sub}/search.json
                 ?q={query}&restrict_sr=1&sort=relevance&t=year&limit={n}
  - Comments:  https://www.reddit.com/{permalink}.json   (returns post + comment tree)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


REDDIT_BASE = "https://www.reddit.com"
# Reddit-recommended UA format: <platform>:<app-id>:<version> (by /u/<user>).
# Avoid the words "bot"/"crawler"/"scraper" — those hit Reddit's anonymous-traffic
# blocklist and return 403 Blocked even from polite single-shot requests.
DEFAULT_USER_AGENT = "web:nomad-agent:v0.1 (by /u/nomad_dev)"
RATE_LIMIT_SLEEP_SECONDS = 1.0
DEFAULT_TIMEOUT_SECONDS = 15.0


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------


@dataclass
class RedditPost:
    """One Reddit post + (optionally) the top N comments on it.

    `selftext` is the post body; for link-only posts it's empty. `comments`
    is filled lazily by `fetch_top_comments`.
    """

    post_id: str
    subreddit: str
    title: str
    selftext: str
    score: int
    num_comments: int
    permalink: str  # starts with '/r/...'
    url: str  # external URL or self link
    author: str
    created_utc: float
    comments: list[str] = field(default_factory=list)

    @property
    def full_url(self) -> str:
        return f"{REDDIT_BASE}{self.permalink}"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _headers(user_agent: str | None = None) -> dict[str, str]:
    """Browser-like headers. Reddit's anti-bot layer 403s plain `User-Agent`-only
    httpx requests; sending Accept-Language + a proper Accept matters. We also
    keep the UA a Reddit-format string (no "bot"/"crawler"/"scraper") which is
    flagged on its own."""
    return {
        "User-Agent": user_agent or DEFAULT_USER_AGENT,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
    }


async def _get_json(
    client: httpx.AsyncClient, url: str, params: dict[str, Any] | None = None
) -> dict[str, Any] | list[Any]:
    """GET a Reddit JSON endpoint. Raises on non-2xx."""
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_listing(payload: dict[str, Any] | list[Any]) -> list[RedditPost]:
    """Map a Reddit `Listing` JSON payload (search response) → RedditPost list."""
    if not isinstance(payload, dict):
        return []
    children = payload.get("data", {}).get("children", []) or []
    posts: list[RedditPost] = []
    for child in children:
        data = child.get("data", {}) if isinstance(child, dict) else {}
        post_id = data.get("id")
        title = data.get("title")
        if not post_id or not title:
            continue
        posts.append(
            RedditPost(
                post_id=post_id,
                subreddit=data.get("subreddit", ""),
                title=title.strip(),
                selftext=(data.get("selftext") or "").strip(),
                score=int(data.get("score") or 0),
                num_comments=int(data.get("num_comments") or 0),
                permalink=data.get("permalink", ""),
                url=data.get("url", ""),
                author=data.get("author", ""),
                created_utc=float(data.get("created_utc") or 0.0),
            )
        )
    return posts


def _parse_top_comments(payload: list[Any] | dict[str, Any], max_comments: int) -> list[str]:
    """Reddit /comments/{id}.json returns a 2-element array: [post, comments].

    We only care about the `comments` listing's top-level children. Stickied /
    deleted / removed comments are skipped. Body is trimmed to keep prompts lean.
    """
    if not isinstance(payload, list) or len(payload) < 2:
        return []
    listing = payload[1]
    if not isinstance(listing, dict):
        return []
    children = listing.get("data", {}).get("children", []) or []

    out: list[str] = []
    for child in children:
        if not isinstance(child, dict):
            continue
        if child.get("kind") != "t1":  # comment kind
            continue
        data = child.get("data", {})
        if data.get("stickied"):
            continue
        body = (data.get("body") or "").strip()
        if not body or body in {"[deleted]", "[removed]"}:
            continue
        # Cap individual comment length to avoid mega-essays dominating the prompt.
        if len(body) > 800:
            body = body[:800] + "…"
        out.append(body)
        if len(out) >= max_comments:
            break
    return out


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


# Sentinel returned by `search_reddit` when Reddit blocks us with 403. Distinct
# from "no results" so the fan-out loop can short-circuit instead of grinding
# through 15 doomed requests (which only deepens the block).
class RedditBlockedError(Exception):
    """Reddit returned 403 — we're being rate-limited / blocked."""


async def search_reddit(
    query: str,
    subreddit: str,
    *,
    limit: int = 10,
    sort: str = "relevance",
    time_filter: str = "year",
    user_agent: str | None = None,
) -> list[RedditPost]:
    """Search a single subreddit and return parsed posts.

    Mirrors: https://www.reddit.com/r/{subreddit}/search.json
              ?q=...&restrict_sr=1&sort=relevance&t=year&limit=10

    On 403, falls back once to old.reddit.com (whose anti-bot layer is
    sometimes more lenient). Persistent 403 raises RedditBlockedError so the
    caller can stop fan-out early.
    """
    params: dict[str, Any] = {
        "q": query,
        "restrict_sr": 1,
        "sort": sort,
        "t": time_filter,
        "limit": min(limit, 25),
    }
    logger.info("reddit.search sub=%s q=%r limit=%d", subreddit, query, limit)

    last_status: int | None = None
    for base in (REDDIT_BASE, "https://old.reddit.com"):
        url = f"{base}/r/{subreddit}/search.json"
        async with httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT_SECONDS, headers=_headers(user_agent)
        ) as client:
            try:
                payload = await _get_json(client, url, params=params)
            except httpx.HTTPStatusError as e:
                last_status = e.response.status_code if e.response else None
                logger.warning(
                    "reddit.search HTTP %s base=%s sub=%s q=%r",
                    last_status, base, subreddit, query,
                )
                if last_status in (403, 429):
                    continue  # try old.reddit.com fallback
                return []
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "reddit.search err=%s base=%s sub=%s q=%r",
                    e, base, subreddit, query,
                )
                return []
        posts = _parse_listing(payload)
        logger.info(
            "reddit.search sub=%s q=%r got=%d (via %s)",
            subreddit, query, len(posts), base,
        )
        return posts

    # Both bases blocked → propagate so caller can short-circuit fan-out.
    # 429 = rate-limited, 403 = anti-bot. Either way: stop hammering.
    if last_status in (403, 429):
        raise RedditBlockedError(
            f"Reddit blocked sub={subreddit} q={query!r} "
            f"({last_status} on both endpoints)"
        )
    return []


async def fetch_top_comments(
    permalink: str,
    *,
    max_comments: int = 3,
    user_agent: str | None = None,
) -> list[str]:
    """Fetch top N comment bodies for a post given its permalink.

    Returns [] on any error (graceful — agent still has the post body).
    """
    if not permalink:
        return []
    # permalink already starts with '/r/...'; strip trailing slash if any
    path = permalink.rstrip("/")
    url = f"{REDDIT_BASE}{path}.json"
    params = {"limit": max(max_comments * 2, 5), "depth": 1, "sort": "top"}
    async with httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT_SECONDS, headers=_headers(user_agent)
    ) as client:
        try:
            payload = await _get_json(client, url, params=params)
        except httpx.HTTPStatusError as e:
            logger.debug(
                "reddit.comments HTTP %s permalink=%s",
                e.response.status_code if e.response else "?",
                permalink,
            )
            return []
        except Exception as e:  # noqa: BLE001
            logger.debug("reddit.comments unexpected err=%s permalink=%s", e, permalink)
            return []
    return _parse_top_comments(payload, max_comments)


async def search_many_with_rate_limit(
    queries: list[tuple[str, str]],
    *,
    limit_per_query: int = 10,
    sleep_seconds: float = RATE_LIMIT_SLEEP_SECONDS,
    user_agent: str | None = None,
) -> list[RedditPost]:
    """Run many (query, subreddit) searches sequentially, sleeping between calls.

    Reddit's public JSON allows ~60 req/min; we default to 1s between calls.
    Failures per-call are logged and skipped — we always return the union of
    whatever succeeded, deduped by post_id.
    """
    seen: set[str] = set()
    out: list[RedditPost] = []
    consecutive_blocks = 0
    for i, (q, sub) in enumerate(queries):
        if i > 0:
            await asyncio.sleep(sleep_seconds)
        try:
            posts = await search_reddit(
                q, sub, limit=limit_per_query, user_agent=user_agent
            )
            consecutive_blocks = 0
        except RedditBlockedError as e:
            consecutive_blocks += 1
            logger.warning(
                "reddit.search blocked sub=%s q=%r (streak=%d): %s",
                sub, q, consecutive_blocks, e,
            )
            # After 3 consecutive 403s, abort — Reddit is rate-limiting us
            # and continuing only deepens the block. Return what we have.
            if consecutive_blocks >= 3:
                logger.warning(
                    "reddit.search aborting fan-out after %d consecutive 403s",
                    consecutive_blocks,
                )
                break
            continue
        except Exception as e:  # noqa: BLE001
            logger.warning("reddit.search_failed sub=%s q=%r err=%s", sub, q, e)
            continue
        for p in posts:
            if p.post_id in seen:
                continue
            seen.add(p.post_id)
            out.append(p)
    return out


async def enrich_with_comments(
    posts: list[RedditPost],
    *,
    max_comments: int = 3,
    sleep_seconds: float = RATE_LIMIT_SLEEP_SECONDS,
    user_agent: str | None = None,
) -> None:
    """Mutate each post in `posts` to fill `.comments`. Sequential w/ sleep.

    We do this sequentially (not parallel) to stay polite under the 60 req/min
    rate limit. Failures per-post are silent — the post body alone still has
    signal.
    """
    for i, p in enumerate(posts):
        if i > 0:
            await asyncio.sleep(sleep_seconds)
        try:
            p.comments = await fetch_top_comments(
                p.permalink, max_comments=max_comments, user_agent=user_agent
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("reddit.comments_failed post=%s err=%s", p.post_id, e)
            p.comments = []
