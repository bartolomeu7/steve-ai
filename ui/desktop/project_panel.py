"""Right 'Projeto Atual' panel for Workspace mode (placeholders OK)."""
from __future__ import annotations

import customtkinter as ctk

from ui.desktop.styles import (
    FONT_FAMILY,
    FONT_SIZE_BODY,
    FONT_SIZE_SMALL,
    FONT_SIZE_SUBTITLE,
    Palette,
)


class ProjectPanel(ctk.CTkFrame):
    def __init__(self, master, palette: Palette, **kwargs):
        super().__init__(
            master, fg_color=palette.surface, width=260, corner_radius=0,
            border_width=1, border_color=palette.border, **kwargs,
        )
        self.pack_propagate(False)
        p = palette

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(16, 8))
        ctk.CTkLabel(
            header, text="Projeto Atual", text_color=p.accent,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_SUBTITLE, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            header, text="STEVE AI", text_color=p.text_muted,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_SMALL),
        ).pack(anchor="w", pady=(2, 0))

        prog_box = ctk.CTkFrame(
            self, fg_color=p.surface_alt, corner_radius=12, border_width=1, border_color=p.border
        )
        prog_box.pack(fill="x", padx=16, pady=8)
        ctk.CTkLabel(
            prog_box, text="Progresso", text_color=p.text_muted,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_SMALL),
        ).pack(anchor="w", padx=12, pady=(10, 4))
        self.progress = ctk.CTkProgressBar(prog_box, height=8, progress_color=p.accent, fg_color=p.bg)
        self.progress.pack(fill="x", padx=12, pady=(0, 4))
        self.progress.set(0.42)
        ctk.CTkLabel(
            prog_box, text="42% — HUD dual-mode", text_color=p.text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_SMALL),
        ).pack(anchor="w", padx=12, pady=(0, 10))

        files_box = ctk.CTkFrame(self, fg_color="transparent")
        files_box.pack(fill="both", expand=True, padx=16, pady=8)
        ctk.CTkLabel(
            files_box, text="Arquivos recentes", text_color=p.text_muted,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_SMALL),
        ).pack(anchor="w", pady=(0, 6))
        for name in ("window.py", "styles.py", "hud_chrome.py", "config.json"):
            row = ctk.CTkFrame(files_box, fg_color=p.surface_alt, corner_radius=8, height=32)
            row.pack(fill="x", pady=3)
            row.pack_propagate(False)
            ctk.CTkLabel(
                row, text=f"  📄  {name}", text_color=p.text, anchor="w",
                font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_BODY),
            ).pack(fill="x", padx=4)
