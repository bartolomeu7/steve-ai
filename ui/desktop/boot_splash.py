"""Borderless-fullscreen boot splash (no Orb). Cyan HUD loading animation."""
from __future__ import annotations

import math
import time
from typing import Callable

import customtkinter as ctk

from ui.desktop.styles import FONT_FAMILY, FONT_MONO, FONT_SIZE_SMALL, FONT_SIZE_TITLE, Palette


class BootSplashOverlay(ctk.CTkFrame):
    """Full-window loading overlay. Call start(); invokes on_done when finished."""

    def __init__(
        self,
        master,
        palette: Palette,
        on_done: Callable[[], None] | None = None,
        duration_ms: int = 2200,
    ):
        super().__init__(master, fg_color=palette.bg, corner_radius=0)
        self._palette = palette
        self._on_done = on_done
        self._duration_ms = max(800, int(duration_ms))
        self._t0 = 0.0
        self._angle = 0.0
        self._running = False
        self._canvas = ctk.CTkCanvas(
            self, bg=palette.bg, highlightthickness=0, bd=0
        )
        self._canvas.pack(fill="both", expand=True)
        self._title = ctk.CTkLabel(
            self,
            text="STEVE  ·  BOOT",
            text_color=palette.accent,
            font=(FONT_MONO, FONT_SIZE_TITLE, "bold"),
            fg_color="transparent",
        )
        self._status = ctk.CTkLabel(
            self,
            text="INICIALIZANDO PROTOCOLOS…",
            text_color=palette.text_muted,
            font=(FONT_FAMILY, FONT_SIZE_SMALL),
            fg_color="transparent",
        )
        self._pct = ctk.CTkLabel(
            self,
            text="0%",
            text_color=palette.accent,
            font=(FONT_MONO, 18),
            fg_color="transparent",
        )
        self.bind("<Configure>", self._place_labels)

    def _place_labels(self, _event=None) -> None:
        w = max(self.winfo_width(), 1)
        h = max(self.winfo_height(), 1)
        self._title.place(relx=0.5, rely=0.62, anchor="center")
        self._status.place(relx=0.5, rely=0.68, anchor="center")
        self._pct.place(relx=0.5, rely=0.74, anchor="center")
        self._canvas.configure(width=w, height=h)

    def start(self) -> None:
        self.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.lift()
        self._t0 = time.perf_counter()
        self._running = True
        self.after(16, self._tick)

    def _tick(self) -> None:
        if not self._running:
            return
        elapsed = (time.perf_counter() - self._t0) * 1000.0
        progress = min(1.0, elapsed / self._duration_ms)
        self._angle = (self._angle + 8.0) % 360.0
        self._paint_loader(progress)
        self._pct.configure(text=f"{int(progress * 100)}%")
        if progress < 0.35:
            self._status.configure(text="CARREGANDO NÚCLEO…")
        elif progress < 0.7:
            self._status.configure(text="SINCRONIZANDO INTERFACE…")
        else:
            self._status.configure(text="PRONTO")
        if progress >= 1.0:
            self._finish()
            return
        self.after(16, self._tick)

    def _paint_loader(self, progress: float) -> None:
        c = self._canvas
        c.delete("all")
        w = max(c.winfo_width(), 2)
        h = max(c.winfo_height(), 2)
        cx, cy = w / 2, h * 0.42
        accent = self._palette.accent
        muted = self._palette.border
        # Outer faint ring
        for i, r in enumerate((90, 110, 130)):
            c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=muted, width=1)
        # Spinning arc (loading) — not an orb blob
        r = 100
        extent = 60 + 80 * progress
        start = -self._angle
        c.create_arc(
            cx - r, cy - r, cx + r, cy + r,
            start=start, extent=extent,
            style="arc", outline=accent, width=3,
        )
        # Progress bar
        bar_w = min(360, w * 0.45)
        bx0, by0 = cx - bar_w / 2, cy + 150
        bx1, by1 = cx + bar_w / 2, cy + 158
        c.create_rectangle(bx0, by0, bx1, by1, outline=muted, width=1)
        fill_x = bx0 + (bx1 - bx0) * progress
        c.create_rectangle(bx0, by0, fill_x, by1, outline="", fill=accent)

    def _finish(self) -> None:
        self._running = False
        cb = self._on_done
        try:
            self.place_forget()
            self.destroy()
        except Exception:
            pass
        if cb:
            cb()
