"""Unit tests for the skill loader (app/skills/loader.py).

Covers frontmatter stripping, @include resolution (via tmp fixtures so no
production prompt is touched), error handling, render_skill formatting, and a
smoke check that the real externalized prompts load + format correctly.
"""

from __future__ import annotations

import pytest

from app.skills.loader import (
    SkillError,
    _strip_frontmatter,
    load_skill,
    load_text,
    render_skill,
)


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


def test_strip_frontmatter_parses_and_removes_block() -> None:
    text = '---\nname: x\ndescription: "hi"\nversion: 1\n---\n\nBody starts here.'
    meta, body = _strip_frontmatter(text)
    assert meta == {"name": "x", "description": "hi", "version": "1"}
    assert body == "Body starts here."


def test_strip_frontmatter_noop_without_block() -> None:
    text = "No frontmatter here.\nSecond line."
    meta, body = _strip_frontmatter(text)
    assert meta == {}
    assert body == text


def test_strip_frontmatter_preserves_body_braces() -> None:
    # Format placeholders + escaped braces must survive untouched.
    text = "---\nname: x\n---\nUse {destination} and literal {{json}}."
    _meta, body = _strip_frontmatter(text)
    assert body == "Use {destination} and literal {{json}}."


# ---------------------------------------------------------------------------
# @include resolution (tmp fixtures)
# ---------------------------------------------------------------------------


def test_include_inlines_shared_block(tmp_path) -> None:
    (tmp_path / "_shared").mkdir()
    (tmp_path / "_shared" / "block.md").write_text("SHARED CONTENT", encoding="utf-8")
    main = tmp_path / "main.md"
    main.write_text(
        "---\nname: main\n---\nBefore.\n@include _shared/block.md\nAfter.",
        encoding="utf-8",
    )
    out = load_text(main)
    assert "SHARED CONTENT" in out
    assert "Before." in out and "After." in out
    assert "@include" not in out


def test_include_resolves_relative_to_including_file(tmp_path) -> None:
    # Nested include: a/main.md includes ../shared.md
    (tmp_path / "a").mkdir()
    (tmp_path / "shared.md").write_text("ROOT BLOCK", encoding="utf-8")
    main = tmp_path / "a" / "main.md"
    main.write_text("@include ../shared.md", encoding="utf-8")
    assert load_text(main).strip() == "ROOT BLOCK"


def test_include_missing_target_raises(tmp_path) -> None:
    main = tmp_path / "main.md"
    main.write_text("@include _shared/nope.md", encoding="utf-8")
    with pytest.raises(SkillError, match="not found"):
        load_text(main)


def test_include_cycle_is_bounded(tmp_path) -> None:
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("@include b.md", encoding="utf-8")
    b.write_text("@include a.md", encoding="utf-8")
    with pytest.raises(SkillError):
        load_text(a)


def test_missing_skill_file_raises(tmp_path) -> None:
    with pytest.raises(SkillError, match="not found"):
        load_text(tmp_path / "does_not_exist.md")


# ---------------------------------------------------------------------------
# Real externalized prompts (smoke — guards the Milestone B migration)
# ---------------------------------------------------------------------------


def test_real_skills_load() -> None:
    for name in (
        "synthesizer",
        "reddit_research",
        "youtube_pass1",
        "youtube_pass2",
        "blog_research",
        "blog_anchor",
    ):
        body = load_skill(name)
        assert body and isinstance(body, str)


def test_synthesizer_skill_formats_with_stop_bounds() -> None:
    # The synthesizer prompt has {min_stops}/{max_stops} fields and {{ }} escapes
    # for its JSON-shape example. .format() must succeed and produce real braces.
    rendered = load_skill("synthesizer").format(min_stops=3, max_stops=6)
    assert "between 3 and 6 stops" in rendered
    assert '{"route_summary"' in rendered  # escaped {{ }} collapsed to single braces
    assert "{min_stops}" not in rendered


def test_reddit_skill_formats_destination() -> None:
    rendered = render_skill("reddit_research", destination="Goa, India")
    assert "Goa, India" in rendered
    assert "{destination}" not in rendered
