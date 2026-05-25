"""Externalized agent prompts ("skills") + the loader that reads them.

Agents import their system prompts from markdown files in this package via
``load_skill`` / ``render_skill`` instead of inlining megastrings in code.
See ``loader.py`` for the why and the supported markdown features.
"""

from app.skills.loader import SkillError, load_skill, load_text, render_skill

__all__ = ["SkillError", "load_skill", "load_text", "render_skill"]
