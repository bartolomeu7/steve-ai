"""HUD chrome: clock ticker, corner labels, mic cluster (Jarvis aesthetic)."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

import customtkinter as ctk

from ui.desktop.styles import (
    FONT_FAMILY,
    FONT_MONO,
    FONT_SIZE_CLOCK,
    FONT_SIZE_SMALL,
    FONT_SIZE_TINY,
    Palette,
)

_WEEKDAYS_PT = ("SEGUNDA", "TERÇA", "QUARTA", "QUINTA", "SEXTA", "SÁBADO", "DOMINGO")
_MONTHS_PT = (
    "", "JANEIRO", "FEVEREIRO", "MARÇO", "ABRIL", "MAIO", "JUNHO",
    "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO",
)


class HudClock(ctk.CTkFrame):
    def __init__(self, master, palette: Palette, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._after_id: str | None = None
        self.time_label = ctk.CTkLabel(
            self,
            text="00:00:00",
            text_color=palette.accent,
            font=ctk.CTkFont(family=FONT_MONO, size=FONT_SIZE_CLOCK, weight="bold"),
        )
        self.time_label.pack(anchor="e")
        self.date_label = ctk.CTkLabel(
            self,
            text="",
            text_color=palette.text_muted,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_SMALL),
        )
        self.date_label.pack(anchor="e")
        self._tick()

    def _tick(self) -> None:
        now = datetime.now()
        self.time_label.configure(text=now.strftime("%H:%M:%S"))
        self.date_label.configure(
            text=f"{_WEEKDAYS_PT[now.weekday()]}, {now.day:02d} {_MONTHS_PT[now.month]}"
        )
        try:
            self._after_id = self.after(1000, self._tick)
        except Exception:
            self._after_id = None

    def stop(self) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None


class HudCornerLabel(ctk.CTkLabel):
    def __init__(self, master, palette: Palette, text: str = "", **kwargs):
        super().__init__(
            master,
            text=text,
            text_color=palette.text_muted,
            font=ctk.CTkFont(family=FONT_MONO, size=FONT_SIZE_TINY),
            **kwargs,
        )


class HudMicCluster(ctk.CTkFrame):
    def __init__(
        self,
        master,
        palette: Palette,
        on_mic: Callable[[], None],
        on_history: Callable[[], None] | None = None,
        on_settings: Callable[[], None] | None = None,
        voice_available: bool = True,
        **kwargs,
    ):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_mic = on_mic
        self._palette = palette
        self._voice_available = voice_available
        side, mic = 48, 72

        self.history_btn = ctk.CTkButton(
            self, text="↺", width=side, height=side, corner_radius=side // 2,
            fg_color=palette.surface, text_color=palette.accent, hover_color=palette.hover,
            border_width=1, border_color=palette.border,
            font=ctk.CTkFont(family=FONT_FAMILY, size=18),
            command=on_history or (lambda: None),
        )
        self.history_btn.pack(side="left", padx=12)

        self.mic_button = ctk.CTkButton(
            self, text="🎙️", width=mic, height=mic, corner_radius=mic // 2,
            fg_color=palette.accent, text_color=palette.accent_text, hover_color=palette.glow,
            font=ctk.CTkFont(family=FONT_FAMILY, size=28), command=self._handle_mic,
        )
        self.mic_button.pack(side="left", padx=8)
        if not voice_available:
            self.mic_button.configure(state="disabled", fg_color=palette.surface_alt)

        self.settings_btn = ctk.CTkButton(
            self, text="⚙", width=side, height=side, corner_radius=side // 2,
            fg_color=palette.surface, text_color=palette.accent, hover_color=palette.hover,
            border_width=1, border_color=palette.border,
            font=ctk.CTkFont(family=FONT_FAMILY, size=18),
            command=on_settings or (lambda: None),
        )
        self.settings_btn.pack(side="left", padx=12)

    def _handle_mic(self) -> None:
        self._on_mic()

    def set_listening(self, listening: bool) -> None:
        p = self._palette
        if listening:
            self.mic_button.configure(fg_color=p.glow, text_color=p.accent_text)
        elif self._voice_available:
            self.mic_button.configure(fg_color=p.accent, text_color=p.accent_text)

    def set_busy(self, busy: bool) -> None:
        if self._voice_available:
            self.mic_button.configure(state="disabled" if busy else "normal")

    def set_voice_available(self, available: bool) -> None:
        self._voice_available = available
        p = self._palette
        if available:
            self.mic_button.configure(state="normal", fg_color=p.accent)
        else:
            self.mic_button.configure(state="disabled", fg_color=p.surface_alt)
