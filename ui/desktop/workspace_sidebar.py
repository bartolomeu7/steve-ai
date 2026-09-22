"""Left icon sidebar for Workspace mode."""
from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from ui.desktop.styles import FONT_FAMILY, FONT_SIZE_BODY, Palette


class WorkspaceSidebar(ctk.CTkFrame):
    def __init__(
        self,
        master,
        palette: Palette,
        on_chat: Callable[[], None] | None = None,
        on_search: Callable[[], None] | None = None,
        on_settings: Callable[[], None] | None = None,
        on_toggle_mode: Callable[[], None] | None = None,
        **kwargs,
    ):
        super().__init__(master, fg_color=palette.surface, width=64, corner_radius=0, **kwargs)
        self.pack_propagate(False)
        p = palette

        def _icon(text: str, cmd, top: bool = True) -> ctk.CTkButton:
            btn = ctk.CTkButton(
                self, text=text, width=44, height=44, corner_radius=12,
                fg_color="transparent", text_color=p.accent, hover_color=p.hover,
                font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_BODY + 4),
                command=cmd or (lambda: None),
            )
            btn.pack(side="top" if top else "bottom", padx=10, pady=8)
            return btn

        self.chat_btn = _icon("💬", on_chat)
        self.search_btn = _icon("🔍", on_search)
        self.settings_btn = _icon("⚙", on_settings)
        self.mode_btn = _icon("◉", on_toggle_mode, top=False)
