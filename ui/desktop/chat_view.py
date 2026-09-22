"""ChatView: scrollable list of message bubbles (user / Steve / system)."""
from __future__ import annotations

import customtkinter as ctk

from ui.desktop.styles import FONT_FAMILY, FONT_SIZE_BODY, FONT_SIZE_SMALL, Palette


class ChatView(ctk.CTkScrollableFrame):
    def __init__(self, master, palette: Palette, **kwargs):
        super().__init__(master, **kwargs)
        self.palette = palette
        self.message_count = 0

    def add_user_message(self, text: str) -> None:
        self._add_bubble(text, sender="user")

    def add_steve_message(self, text: str) -> None:
        self._add_bubble(text, sender="steve")

    def add_system_message(self, text: str) -> None:
        self._add_bubble(text, sender="system")

    def clear(self) -> None:
        for child in self.winfo_children():
            child.destroy()
        self.message_count = 0

    def _add_bubble(self, text: str, sender: str) -> None:
        p = self.palette

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", pady=6, padx=16)

        if sender == "user":
            bg = p.bubble_user
            fg = p.bubble_user_text
            anchor = "e"
            label_text = "Você"
            show_label = True
        elif sender == "steve":
            bg = p.bubble_steve
            fg = p.bubble_steve_text
            anchor = "w"
            label_text = "Steve"
            show_label = True
        else:
            bg = p.bubble_system
            fg = p.bubble_system_text
            anchor = "center"
            label_text = ""
            show_label = False

        if show_label:
            ctk.CTkLabel(
                row,
                text=label_text,
                text_color=p.text_muted,
                font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_SMALL, weight="bold"),
            ).pack(anchor=anchor, padx=4, pady=(0, 3))

        bubble = ctk.CTkLabel(
            row,
            text=text,
            fg_color=bg,
            text_color=fg,
            corner_radius=16,
            justify="left",
            wraplength=560,
            anchor="w",
            padx=16,
            pady=12,
            font=ctk.CTkFont(
                family=FONT_FAMILY,
                size=FONT_SIZE_BODY,
                slant="italic" if sender == "system" else "roman",
            ),
        )
        bubble.pack(anchor=anchor, padx=4)
        self.message_count += 1
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        self.update_idletasks()
        canvas = getattr(self, "_parent_canvas", None)
        if canvas is not None:
            try:
                canvas.yview_moveto(1.0)
            except Exception:
                pass
