"""Unit tests for MangaTranslator._build_system_prompt.

Verifies the extra_system_prompt is *prepended* (not replacing) to the built-in
prompt, and that empty/whitespace-only values leave it untouched. No LLM calls:
only prompt construction.
"""
from app.service import MangaTranslator


def _system_content(translator):
    return translator._build_system_prompt("some context")


def test_baseline_prompt_intact():
    base = _system_content(MangaTranslator())
    assert base.startswith("You are an expert manga translator")


def test_extra_prompt_is_prepended_not_replaced():
    extra = "Extra rules: keep character names in katakana."
    sp = _system_content(MangaTranslator(extra_system_prompt=extra))
    assert sp.startswith(extra), "extra prompt must come first"
    assert "\n\nYou are an expert manga translator" in sp, "default prompt must follow"


def test_whitespace_only_extra_is_ignored():
    base = _system_content(MangaTranslator())
    sp_ws = _system_content(
        MangaTranslator(extra_system_prompt="   \n\t  ")
    )
    assert sp_ws == base, "whitespace-only extra prompt must be ignored"
