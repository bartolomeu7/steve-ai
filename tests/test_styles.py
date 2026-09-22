"""Tests for ui/desktop/styles.py's palette selection — pure data, no Tkinter needed."""
from __future__ import annotations

from ui.desktop.styles import DARK, DARK_CYBER, LIGHT, palette_for


def test_palette_for_light_is_the_default():
    assert palette_for("light") is LIGHT
    assert palette_for("anything-unknown") is LIGHT


def test_palette_for_dark():
    assert palette_for("dark") is DARK


def test_palette_for_cyber_and_thomas_are_the_same_palette():
    assert palette_for("cyber") is DARK_CYBER
    assert palette_for("thomas") is DARK_CYBER


def test_palette_for_is_case_and_whitespace_insensitive():
    assert palette_for("  Cyber  ") is DARK_CYBER
    assert palette_for("DARK") is DARK


def test_dark_cyber_is_a_real_dark_palette_not_a_placeholder():
    assert DARK_CYBER.bg != LIGHT.bg
    assert DARK_CYBER.bg != DARK.bg
    assert DARK_CYBER.accent == "#00E5FF"
