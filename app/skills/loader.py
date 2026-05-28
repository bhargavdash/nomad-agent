"""Skill loader — externalized agent prompts as markdown "skill" files.

Why this exists
---------------
The agents' system prompts used to be giant inline Python strings (the
synthesizer's was ~130 lines). That made them hard to iterate on, version,
diff, reuse, or A/B test, and the same guidance (voice rules, banned words,
output schema) was copy-pasted across agents. Prompts now live as markdown
files under ``app/skills/`` and are loaded at import time. A prompt change is
a markdown edit, not a code change + redeploy.

Capabilities
------------
- **Frontmatter:** a leading ``--- ... ---`` block is parsed into metadata and
  stripped from the body (self-documenting: name/description/version).
- **Includes:** ``@include <relative/path>`` lines are inlined, resolved
  relative to the *including* file's directory (like C includes), recursively,
  with a depth guard. This lets shared blocks (e.g. ``_shared/banned_words.md``)
  live in one place instead of being duplicated across agents.
- **Caching:** ``load_skill(name)`` is cached — prompts don't change at runtime.
- **Placeholders:** skill bodies may contain ``str.format`` fields
  (``{destination}``, ``{min_stops}``) and literal braces escaped as ``{{ }}``
  (e.g. JSON-shape examples), exactly as the inline prompts did. ``load_skill``
  returns the raw body (placeholders intact) for the caller's ``.format()``;
  ``render_skill`` loads then ``.format()``s.

Packaging note: the service runs from source (uvicorn), so the ``.md`` files are
read from the source tree. If this is ever shipped as a wheel, the ``app/skills``
data files must be force-included in the build.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).resolve().parent

# A line that is exactly `@include <path>` (leading whitespace allowed).
_INCLUDE_RE = re.compile(r"^[ \t]*@include[ \t]+(?P<path>\S+)[ \t]*$", re.MULTILINE)
_MAX_INCLUDE_DEPTH = 6


class SkillError(RuntimeError):
    """Raised when a skill file is missing or malformed."""


def _strip_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a leading ``--- ... ---`` frontmatter block from the body.

    Returns ``(metadata, body)``. If there's no frontmatter, metadata is empty
    and the body is the original text unchanged.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    block = text[3:end]
    body = text[end + 4 :].lstrip("\n")
    meta: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, body


def _render(text: str, base_dir: Path, _depth: int = 0) -> str:
    """Strip frontmatter and inline any ``@include`` directives.

    ``base_dir`` is the directory includes resolve against (the including
    file's parent). Recursion is bounded by ``_MAX_INCLUDE_DEPTH``.
    """
    if _depth > _MAX_INCLUDE_DEPTH:
        raise SkillError(
            f"@include nesting exceeded {_MAX_INCLUDE_DEPTH} (cycle?) under {base_dir}"
        )
    _meta, body = _strip_frontmatter(text)

    def _sub(match: re.Match[str]) -> str:
        rel = match.group("path")
        target = (base_dir / rel).resolve()
        if not target.exists():
            raise SkillError(f"@include target not found: {rel!r} (from {base_dir})")
        included = target.read_text(encoding="utf-8")
        return _render(included, target.parent, _depth + 1).strip("\n")

    return _INCLUDE_RE.sub(_sub, body)


def load_text(path: Path) -> str:
    """Load + render a skill file at an explicit path (used by tests)."""
    path = path.resolve()
    if not path.exists():
        raise SkillError(f"skill file not found: {path}")
    return _render(path.read_text(encoding="utf-8"), path.parent).strip("\n")


@lru_cache(maxsize=None)
def load_skill(name: str) -> str:
    """Return a rendered skill body by name (path under app/skills/, no suffix).

    e.g. ``load_skill("synthesizer")`` → ``app/skills/synthesizer.md``;
    ``load_skill("_shared/banned_words")`` → ``app/skills/_shared/banned_words.md``.
    Frontmatter is stripped, ``@include``s are inlined, ``{placeholder}`` fields
    are left intact for the caller's ``.format()``. Cached.
    """
    return load_text(SKILLS_DIR / f"{name}.md")


def render_skill(name: str, **kwargs: object) -> str:
    """``load_skill(name)`` then ``str.format(**kwargs)``.

    Use when the skill has ``{placeholder}`` fields the caller fills (e.g.
    ``render_skill("reddit_research", destination="Goa, India")``).
    """
    text = load_skill(name)
    return text.format(**kwargs) if kwargs else text
