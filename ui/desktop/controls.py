"""InputBar: text entry + send + mic. voice_only=True hides text (HUD)."""
from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from ui.desktop.styles import FONT_FAMILY, FONT_SIZE_BODY, Palette


class InputBar(ctk.CTkFrame):
    def __init__(
        self,
        master,
        palette: Palette,
        on_send: Callable[[str], None],
        on_mic: Callable[[], None],
        voice_available: bool,
        voice_only: bool = False,
        **kwargs,
    ):
        super().__init__(master, fg_color="transparent", corner_radius=0, **kwargs)
        self._on_send = on_send
        self._on_mic = on_mic
        self._voice_available = voice_available
        self._voice_only = voice_only
        self._listening = False
        self._palette = palette
        p = palette

        self._shell = ctk.CTkFrame(
            self, fg_color=p.surface, corner_radius=18, border_width=1, border_color=p.surface_alt
        )
        self._shell.pack(fill="x", expand=True)
        inner = ctk.CTkFrame(self._shell, fg_color="transparent")
        inner.pack(fill="x", padx=10, pady=8)

        self.mic_button = ctk.CTkButton(
            inner, text="🎙️", width=42, height=42, fg_color=p.surface_alt, text_color=p.text,
            hover_color=p.hover, corner_radius=14, command=self._handle_mic,
        )
        self.mic_button.pack(side="left", padx=(0, 8))
        if not voice_available:
            self.mic_button.configure(state="disabled")

        self.entry = ctk.CTkEntry(
            inner, placeholder_text="Digite ou fale com o Steve...",
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_BODY),
            height=42, corner_radius=12, border_width=0, fg_color=p.surface, text_color=p.text,
        )
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.entry.bind("<Return>", self._handle_send)
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)

        self.send_button = ctk.CTkButton(
            inner, text="➤", width=42, height=42, fg_color=p.accent, text_color=p.accent_text,
            hover_color=p.accent, corner_radius=14,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_BODY, weight="bold"),
            command=self._handle_send,
        )
        self.send_button.pack(side="right")
        self.set_voice_only(voice_only)

    def set_voice_only(self, voice_only: bool) -> None:
        self._voice_only = voice_only
        if voice_only:
            self.entry.pack_forget()
            self.send_button.pack_forget()
            self.mic_button.configure(width=56, height=56, corner_radius=18)
        else:
            self.mic_button.configure(width=42, height=42, corner_radius=14)
            if not self.entry.winfo_ismapped():
                self.entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
            if not self.send_button.winfo_ismapped():
                self.send_button.pack(side="right")

    def _set_shell_border(self, color: str) -> None:
        self._shell.configure(border_color=color)

    def _on_focus_in(self, _e=None) -> None:
        if not self._listening:
            self._set_shell_border(self._palette.glow)

    def _on_focus_out(self, _e=None) -> None:
        if not self._listening:
            self._set_shell_border(self._palette.surface_alt)

    def set_listening(self, listening: bool) -> None:
        self._listening = listening
        p = self._palette
        if listening:
            self._set_shell_border(p.glow)
            if self._voice_available:
                self.mic_button.configure(fg_color=p.glow, text_color=p.accent_text)
        else:
            self._set_shell_border(p.surface_alt)
            if self._voice_available:
                self.mic_button.configure(fg_color=p.surface_alt, text_color=p.text)

    def _handle_send(self, _e=None) -> None:
        if self._voice_only:
            return
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self._on_send(text)

    def _handle_mic(self) -> None:
        self._on_mic()

    def set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        if not self._voice_only:
            self.entry.configure(state=state)
            self.send_button.configure(state=state)
        if self._voice_available:
            self.mic_button.configure(state=state)

    def set_voice_available(self, available: bool) -> None:
        self._voice_available = available
        self.mic_button.configure(state="normal" if available else "disabled")
