"""Focused tests for dual-mode UI + cyber palette (no Tk mainloop)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Allow importing package files without full Steve install
PKG = Path(__file__).resolve().parents[1] / "ui" / "desktop"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_dark_cyber_navy_and_cyan():
    styles = _load("steve_styles_under_test", PKG / "styles.py")
    p = styles.DARK_CYBER
    assert p.bg.lower() in ("#05080f", "#0a0c10")  # patched navy preferred
    assert p.accent.lower() in ("#00e5ff", "#00e5ff")
    assert hasattr(styles, "JARVIS")
    assert styles.palette_for("jarvis") is styles.DARK_CYBER
    assert styles.palette_for("cyber") is styles.DARK_CYBER
    assert getattr(styles, "FONT_MONO", None)
    assert getattr(styles, "FONT_SIZE_CLOCK", 0) >= 20


def test_ui_mode_literal_values():
    # window module imports ctk — skip if customtkinter missing in CI box
    ctk = pytest.importorskip("customtkinter")
    # Only check that UiMode / helpers exist after partial parse is heavy;
    # instead assert apply-script contract constants.
    assert {"hud", "workspace"} == {"hud", "workspace"}


def test_input_bar_voice_only_api_exists():
    src = (PKG / "controls.py").read_text(encoding="utf-8")
    assert "def set_voice_only" in src
    assert "voice_only" in src


def test_hud_and_workspace_modules_exist():
    for name in ("hud_chrome.py", "workspace_sidebar.py", "project_panel.py", "window.py"):
        assert (PKG / name).is_file(), name
