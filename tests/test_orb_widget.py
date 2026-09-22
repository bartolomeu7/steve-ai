"""Orb widget tests — real Canvas (this machine has a display), but frames are drawn
one at a time via render_once() rather than the real after()-driven timer, so tests
stay fast and deterministic. Never depends on a microphone or TTS engine."""
from __future__ import annotations

import tkinter as tk

import pytest

from ui.desktop.orb.orb import Orb
from ui.desktop.orb.states import OrbState
from ui.desktop.styles import LIGHT


@pytest.fixture
def root():
    r = tk.Tk()
    r.withdraw()
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


def test_orb_does_not_auto_start(root):
    orb = Orb(root, palette=LIGHT, size=100)
    assert orb._running is False


def test_render_once_produces_a_photo_image(root):
    orb = Orb(root, palette=LIGHT, size=80)
    frame = orb.render_once()
    assert frame.scale > 0
    assert orb._photo_image is not None


def test_every_state_renders_without_error(root):
    orb = Orb(root, palette=LIGHT, size=80)
    for state in OrbState:
        orb.set_state(state)
        orb.set_amplitude(0.6)
        orb.render_once()  # must not raise


def test_start_sets_running_and_stop_clears_it(root):
    orb = Orb(root, palette=LIGHT, size=80)
    orb.start()
    assert orb._running is True
    orb.stop()
    assert orb._running is False


def test_start_is_idempotent(root):
    orb = Orb(root, palette=LIGHT, size=80)
    orb.start()
    orb.start()  # must not double-schedule or raise
    root.update()
    orb.stop()


def test_stop_then_pending_after_callback_is_a_noop(root):
    orb = Orb(root, palette=LIGHT, size=80)
    orb.start()
    orb.stop()
    # pump the event loop a bit — any already-scheduled after() must see _running=False
    # and quietly stop rescheduling instead of raising
    for _ in range(5):
        root.update()
    assert orb._running is False


def test_rendered_array_matches_canvas_size(root):
    orb = Orb(root, palette=LIGHT, size=64)
    orb.set_state(OrbState.SPEAKING)
    array = orb._render_array(orb.animator.tick(0.02))
    assert array.shape == (64, 64, 3)
