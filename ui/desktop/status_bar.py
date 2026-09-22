"""StatusBar: shows current activity plus compact system indicators."""
from __future__ import annotations

import customtkinter as ctk

from ui.desktop.styles import FONT_FAMILY, FONT_SIZE_TINY, Palette

READY_LABEL = "● Pronto"


class StatusBar(ctk.CTkFrame):
    def __init__(self, master, palette: Palette, **kwargs):
        super().__init__(master, fg_color=palette.bg, corner_radius=0, height=36, **kwargs)
        self.pack_propagate(False)
        self.palette = palette
        self._labels: dict[str, ctk.CTkLabel] = {}

        # Activity chip (left) — mirrors Orb state when set via set("activity", ...)
        self._activity_chip = ctk.CTkFrame(
            self, fg_color=palette.tag_bg, corner_radius=12, border_width=0
        )
        self._activity_chip.pack(side="left", padx=12, pady=6)
        self.activity_label = ctk.CTkLabel(
            self._activity_chip,
            text=READY_LABEL,
            text_color=palette.tag_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_TINY, weight="bold"),
        )
        self.activity_label.pack(side="left", padx=10, pady=3)

        chips = ctk.CTkFrame(self, fg_color="transparent")
        chips.pack(side="right", padx=10, pady=4)

        for key, initial in (
            ("model", "—"),
            ("ollama", "○"),
            ("mic", "○"),
            ("voice", "○"),
            ("tools", "0"),
        ):
            chip = ctk.CTkFrame(chips, fg_color=palette.surface_alt, corner_radius=10)
            chip.pack(side="left", padx=3)
            label = ctk.CTkLabel(
                chip,
                text=initial,
                text_color=palette.text_muted,
                font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_TINY),
            )
            label.pack(side="left", padx=8, pady=3)
            self._labels[key] = label

    def set(self, key: str, text: str, color: str | None = None) -> None:
        if key == "activity":
            self.activity_label.configure(text=text, text_color=color or self.palette.tag_text)
            return
        label = self._labels.get(key)
        if label is None:
            return
        label.configure(text=text, text_color=color or self.palette.text_muted)

    def set_ready(self) -> None:
        self.set("activity", READY_LABEL, self.palette.tag_text)
