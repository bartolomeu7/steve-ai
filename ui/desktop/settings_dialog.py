"""SettingsDialog: view/edit the existing Settings — no separate settings store, no
AI/tool logic. Collects a plain dict and hands it to the caller's on_save callback,
which is responsible for actually applying/persisting it via ConfigManager/services."""
from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from settings.config import ProactivityLevel, Settings
from ui.desktop.styles import FONT_FAMILY, FONT_SIZE_BODY, Palette


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, master, settings: Settings, palette: Palette, on_save: Callable[[dict], None]):
        super().__init__(master)
        self._on_save = on_save
        self.palette = palette

        self.title("Configurações — Steve")
        self.geometry("420x660")
        self.configure(fg_color=palette.bg)
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="both", expand=True, padx=20, pady=20)

        self.name_var = ctk.StringVar(value=settings.user_name)
        self._labeled_entry(form, "Nome do usuário", self.name_var)

        self.language_var = ctk.StringVar(value=settings.language)
        self._labeled_entry(form, "Idioma", self.language_var)

        self.model_var = ctk.StringVar(value=settings.ai_model)
        self._labeled_entry(form, "Modelo Ollama", self.model_var)

        self.proactivity_var = ctk.StringVar(value=settings.proactivity.value)
        self._labeled_option(form, "Proatividade", self.proactivity_var, [p.value for p in ProactivityLevel])

        self.theme_var = ctk.StringVar(value=self._theme_to_label(settings.theme))
        self._labeled_option(form, "Tema", self.theme_var, ["Claro", "Escuro", "Cyber"])

        self.voice_output_var = ctk.BooleanVar(value=settings.voice_output_enabled)
        self._labeled_switch(form, "Responder por voz (TTS)", self.voice_output_var)

        self.voice_input_var = ctk.BooleanVar(value=settings.voice_input_enabled)
        self._labeled_switch(form, "Entrada por voz (microfone)", self.voice_input_var)

        self.voice_streaming_var = ctk.BooleanVar(value=settings.voice_streaming_enabled)
        self._labeled_switch(form, "Falar frase a frase (streaming, experimental)", self.voice_streaming_var)

        self.rate_var = ctk.IntVar(value=settings.speaking_rate)
        self._labeled_slider(form, "Velocidade da voz", self.rate_var, 100, 260)

        self.volume_var = ctk.DoubleVar(value=settings.speaking_volume)
        self._labeled_slider(form, "Volume da voz", self.volume_var, 0.0, 1.0)

        self.autostart_var = ctk.BooleanVar(value=settings.autostart_enabled)
        self._labeled_switch(form, "Iniciar com o Windows", self.autostart_var)

        self.minimize_to_tray_var = ctk.BooleanVar(value=settings.minimize_to_tray)
        self._labeled_switch(form, "Minimizar em vez de fechar", self.minimize_to_tray_var)

        self.start_minimized_var = ctk.BooleanVar(value=settings.start_minimized)
        self._labeled_switch(form, "Iniciar minimizado", self.start_minimized_var)

        button_row = ctk.CTkFrame(self, fg_color="transparent")
        button_row.pack(fill="x", padx=20, pady=(0, 20))

        ctk.CTkButton(
            button_row, text="Cancelar", fg_color=palette.surface_alt, text_color=palette.text,
            hover_color=palette.border, command=self.destroy,
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            button_row, text="Salvar", fg_color=palette.accent, text_color=palette.accent_text,
            command=self._handle_save,
        ).pack(side="right")

    @staticmethod
    def _theme_to_label(theme: str) -> str:
        t = theme.lower().strip()
        if t in ("cyber", "thomas", "dark_cyber"):
            return "Cyber"
        return "Escuro" if t == "dark" else "Claro"

    @staticmethod
    def _label_to_theme(label: str) -> str:
        if label == "Cyber":
            return "cyber"
        return "dark" if label == "Escuro" else "light"

    def _labeled_entry(self, parent, label, var) -> None:
        ctk.CTkLabel(parent, text=label, text_color=self.palette.text_muted, anchor="w",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_BODY)).pack(fill="x", pady=(8, 2))
        ctk.CTkEntry(parent, textvariable=var).pack(fill="x")

    def _labeled_option(self, parent, label, var, values) -> None:
        ctk.CTkLabel(parent, text=label, text_color=self.palette.text_muted, anchor="w",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_BODY)).pack(fill="x", pady=(8, 2))
        ctk.CTkOptionMenu(parent, variable=var, values=values).pack(fill="x")

    def _labeled_switch(self, parent, label, var) -> None:
        ctk.CTkSwitch(parent, text=label, variable=var, text_color=self.palette.text).pack(fill="x", pady=8)

    def _labeled_slider(self, parent, label, var, from_, to) -> None:
        ctk.CTkLabel(parent, text=label, text_color=self.palette.text_muted, anchor="w",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_BODY)).pack(fill="x", pady=(8, 2))
        ctk.CTkSlider(parent, variable=var, from_=from_, to=to).pack(fill="x")

    def _handle_save(self) -> None:
        updated = {
            "user_name": self.name_var.get().strip() or "Usuário",
            "language": self.language_var.get().strip() or "pt-BR",
            "ai_model": self.model_var.get().strip() or "llama3.2",
            "proactivity": ProactivityLevel(self.proactivity_var.get()),
            "voice_output_enabled": bool(self.voice_output_var.get()),
            "voice_input_enabled": bool(self.voice_input_var.get()),
            "voice_streaming_enabled": bool(self.voice_streaming_var.get()),
            "speaking_rate": int(self.rate_var.get()),
            "speaking_volume": float(self.volume_var.get()),
            "theme": self._label_to_theme(self.theme_var.get()),
            "autostart_enabled": bool(self.autostart_var.get()),
            "minimize_to_tray": bool(self.minimize_to_tray_var.get()),
            "start_minimized": bool(self.start_minimized_var.get()),
        }
        self._on_save(updated)
        self.destroy()
