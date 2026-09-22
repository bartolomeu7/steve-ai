"""Orb: the animated Canvas widget that is Steve's visual 'core'.

Runs its own render loop via `widget.after(...)` at a capped FPS — this is the only
per-frame work happening; it never touches Orchestrator/ToolManager/VoiceService
directly (see ui/desktop/controller.py for how real state reaches it). Each frame is
built as a small numpy RGB array (fast, vectorized), converted through Pillow to a
Tk PhotoImage, and swapped onto a single reused canvas image item — no per-vertex
Tkinter primitives, no accumulating canvas items.
"""
from __future__ import annotations

import math
import time
import tkinter as tk

import numpy as np
from PIL import Image, ImageTk

from ui.desktop.orb.animation import OrbAnimator, OrbFrame
from ui.desktop.orb.states import OrbState
from ui.desktop.styles import Palette

DEFAULT_FPS = 30  # constructor default; runtime adapts by OrbState
IDLE_FPS = 15
ACTIVE_FPS = 24
IDLE_SUPERSAMPLE = 2
ACTIVE_SUPERSAMPLE = 4
DEFAULT_SIZE = 240


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


class Orb(tk.Canvas):
    def __init__(
        self,
        master,
        palette: Palette,
        size: int = DEFAULT_SIZE,
        fps: int = DEFAULT_FPS,
        **kwargs,
    ):
        super().__init__(master, width=size, height=size, highlightthickness=0, bd=0, bg=palette.orb_bg, **kwargs)
        self.palette = palette
        self.size = size
        self.animator = OrbAnimator()
        self._base_fps = max(1, int(fps))
        self._frame_interval_ms = max(16, int(1000 / IDLE_FPS))
        self._supersample = IDLE_SUPERSAMPLE
        self._running = False
        self._window_focused = True
        self._last_tick: float | None = None
        self._photo_image: ImageTk.PhotoImage | None = None
        self._image_item = self.create_image(size / 2, size / 2, image=None)

    # --- public API -----------------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._last_tick = time.perf_counter()
        self._schedule_next()

    def stop(self) -> None:
        self._running = False

    def set_state(self, state: OrbState) -> None:
        self.animator.set_state(state)
        self._adapt_quality(state)

    def set_window_focused(self, focused: bool) -> None:
        """When the main window loses focus, drop quality to save CPU."""
        self._window_focused = bool(focused)
        self._adapt_quality(self.animator.state)

    def _adapt_quality(self, state: OrbState) -> None:
        active_states = {OrbState.LISTENING, OrbState.THINKING, OrbState.SPEAKING, OrbState.TOOL_EXECUTION}
        busy = state in active_states and self._window_focused
        fps = ACTIVE_FPS if busy else IDLE_FPS
        if not self._window_focused:
            fps = min(fps, 10)
        self._frame_interval_ms = max(16, int(1000 / fps))
        self._supersample = ACTIVE_SUPERSAMPLE if busy else IDLE_SUPERSAMPLE

    def set_amplitude(self, amplitude: float) -> None:
        self.animator.set_amplitude(amplitude)

    def render_once(self) -> OrbFrame:
        """Advances and draws exactly one frame — used by the render loop, and directly
        by tests that don't want to depend on the after()-driven timer."""
        now = time.perf_counter()
        dt = now - (self._last_tick if self._last_tick is not None else now)
        self._last_tick = now
        frame = self.animator.tick(dt)
        self._draw(frame)
        return frame

    # --- internals --------------------------------------------------------------
    def _schedule_next(self) -> None:
        if not self._running:
            return
        self.render_once()
        try:
            self.after(self._frame_interval_ms, self._schedule_next)
        except tk.TclError:  # pragma: no cover - widget destroyed mid-loop
            self._running = False

    def _draw(self, frame: OrbFrame) -> None:
        # Adaptive supersample (2x idle / 4x active) + Lanczos — smooth ChatGPT-like edges on Windows HiDPI
        array = self._render_array(frame, render_size=self.size * self._supersample)
        image = Image.fromarray(array, mode="RGB").resize(
            (self.size, self.size), Image.Resampling.LANCZOS
        )
        self._photo_image = ImageTk.PhotoImage(image)
        self.itemconfig(self._image_item, image=self._photo_image)

    def _render_array(self, frame: OrbFrame, render_size: int | None = None) -> np.ndarray:
        """Fluid cyan blob (ChatGPT Voice vibe) — soft cloud interior, no plastic rings."""
        size = int(render_size or self.size)
        half = size / 2.0
        y, x = np.mgrid[0:size, 0:size].astype(np.float64)
        dx = x - half
        dy = y - half
        dist = np.sqrt(dx * dx + dy * dy)
        angle = np.arctan2(dy, dx)

        n = len(frame.wobble)
        wobble_arr = np.array(frame.wobble, dtype=np.float64) if n else np.zeros(1)
        idx_f = (angle + np.pi) / (2 * np.pi) * max(n, 1)
        idx0 = np.floor(idx_f).astype(int) % max(n, 1)
        idx1 = (idx0 + 1) % max(n, 1)
        frac = idx_f - np.floor(idx_f)
        w = wobble_arr[idx0] * (1 - frac) + wobble_arr[idx1] * frac
        liquid = 0.04 * np.sin(angle * 2.0 + w * 8.0)
        liquid += 0.025 * np.sin(angle * 5.0 - w * 12.0)

        base_radius = size * 0.30 * max(frame.scale, 0.05)
        edge_radius = base_radius * (1.0 + w * 1.35 + liquid)

        edge_soft = max(3.0, size * 0.11)
        t_edge = np.clip((edge_radius + edge_soft - dist) / (2.0 * edge_soft), 0.0, 1.0)
        blob = t_edge * t_edge * (3.0 - 2.0 * t_edge)

        glow_r = base_radius * 2.4
        glow = np.clip(1.0 - (dist - edge_radius * 0.85) / np.maximum(glow_r - edge_radius * 0.85, 1.0), 0.0, 1.0)
        glow = np.where(dist > edge_radius * 0.5, glow * glow * 0.55, 0.35)
        glow *= blob * 0.35 + 0.65 * np.clip(1.0 - dist / glow_r, 0.0, 1.0) ** 2

        if frame.ripple > 0.02:
            pulse = 0.15 * frame.ripple * (
                0.5 + 0.5 * np.sin(dist / max(base_radius, 1) * 3.0 - frame.ripple * 6.0)
            )
            glow = np.clip(glow + pulse * blob, 0.0, 1.0)

        think = np.zeros_like(dist)
        if frame.state is OrbState.THINKING or abs(frame.orbit_phase) > 1e-6:
            hx = half + math.cos(frame.orbit_phase) * base_radius * 0.35
            hy = half + math.sin(frame.orbit_phase) * base_radius * 0.35
            think = np.exp(-0.5 * (((x - hx) ** 2 + (y - hy) ** 2) / (size * 0.12) ** 2)) * 0.35

        nx = dx / max(base_radius, 1.0)
        ny = dy / max(base_radius, 1.0)
        cloud = (
            0.55
            + 0.22 * np.sin(nx * 3.1 + ny * 2.4 + w * 6.0)
            + 0.15 * np.sin(nx * 7.0 - ny * 5.0 + frame.scale * 4.0)
            + 0.10 * np.sin((nx + ny) * 11.0)
        )
        cloud = np.clip(cloud, 0.0, 1.0)

        inner = np.array(_hex_to_rgb(frame.inner_color), dtype=np.float64)
        outer = np.array(_hex_to_rgb(frame.outer_color), dtype=np.float64)
        violet = np.array([180.0, 160.0, 255.0], dtype=np.float64)
        mix = cloud[..., None]
        mid = inner * (0.55 + 0.45 * mix) + outer * (0.45 - 0.20 * mix)
        mid = mid * (1.0 - 0.12 * mix) + violet * (0.12 * mix)
        center = np.clip(1.0 - (dist / np.maximum(edge_radius, 1e-6)) ** 1.4, 0.0, 1.0)
        color = mid * (0.75 + 0.35 * center[..., None])
        color = color + think[..., None] * (outer * 0.5)

        alpha = np.clip(blob * 0.92 + glow * 0.55, 0.0, 1.0)
        bg = np.array(_hex_to_rgb(self.palette.orb_bg), dtype=np.float64)
        rgb = color * alpha[..., None] + bg[None, None, :] * (1.0 - alpha[..., None])
        return np.clip(rgb, 0, 255).astype(np.uint8)
