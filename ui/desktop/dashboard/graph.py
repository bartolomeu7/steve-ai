"""LineGraph: a small, deliberately lightweight history graph — a plain tkinter.Canvas
line plot, not a charting library. The Dashboard's own performance graphs are explicitly
supposed to be cheap (see system_monitor/service.py's bounded-history deques, capped at
120 samples/~2 minutes) — a stdlib Canvas redraw of ≤120 points is negligible, unlike
pulling in matplotlib for this.
"""
from __future__ import annotations

from collections.abc import Sequence

import customtkinter as ctk

from ui.desktop.dashboard.formatting import format_percent
from ui.desktop.styles import FONT_FAMILY, FONT_SIZE_SMALL, Palette

_PADDING = 6


class LineGraph(ctk.CTkFrame):
    """`value_max`, when given, fixes the y-axis (e.g. 100 for a percentage graph);
    `None` values in the series are skipped (gaps in the line) rather than drawn as 0 —
    an unavailable metric (e.g. no GPU) must never look like "0% usage"."""

    def __init__(self, master, palette: Palette, title: str, value_max: float | None = 100.0, **kwargs):
        super().__init__(master, fg_color=palette.surface, corner_radius=10, **kwargs)
        self.palette = palette
        self.value_max = value_max

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=(8, 0))
        ctk.CTkLabel(
            header, text=title, text_color=self.palette.text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_SMALL, weight="bold"),
        ).pack(side="left")
        self._latest_label = ctk.CTkLabel(
            header, text="—", text_color=self.palette.text_muted,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_SMALL),
        )
        self._latest_label.pack(side="right")

        self._canvas = ctk.CTkCanvas(
            self, height=90, bg=self.palette.surface, highlightthickness=0,
        )
        self._canvas.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self._canvas.bind("<Configure>", self._on_resize)
        self._last_values: Sequence[float | None] = ()

    def _on_resize(self, _event) -> None:
        self._redraw()

    def update_history(self, values: Sequence[float | None]) -> None:
        self._last_values = values
        latest = values[-1] if values else None
        self._latest_label.configure(text=format_percent(latest) if self.value_max == 100.0 else self._format_latest(latest))
        self._redraw()

    def _format_latest(self, latest: float | None) -> str:
        return "—" if latest is None else f"{latest:.1f}"

    def _redraw(self) -> None:
        canvas = self._canvas
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 2 * _PADDING or height <= 2 * _PADDING:
            return

        values = self._last_values
        present = [v for v in values if v is not None]
        if not present:
            canvas.create_text(
                width / 2, height / 2, text="Sem dados", fill=self.palette.text_muted,
                font=(FONT_FAMILY, FONT_SIZE_SMALL),
            )
            return

        value_max = self.value_max if self.value_max is not None else max(present) or 1.0
        value_max = max(value_max, max(present), 1e-6)

        usable_width = width - 2 * _PADDING
        usable_height = height - 2 * _PADDING
        count = len(values)
        if count < 2:
            return

        points: list[float] = []
        for index, value in enumerate(values):
            if value is None:
                continue
            x = _PADDING + (index / (count - 1)) * usable_width
            y = _PADDING + usable_height - (min(value, value_max) / value_max) * usable_height
            points.extend((x, y))

        if len(points) >= 4:
            canvas.create_line(*points, fill=self.palette.accent, width=2, smooth=True)
