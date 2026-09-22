"""FirstRunWizard: GUI equivalent of settings.config.ConfigManager.run_first_run_wizard.
Same Settings/ConfigManager underneath — just collected via widgets instead of input()."""
from __future__ import annotations

import logging
from typing import Callable

import customtkinter as ctk

from settings.config import ConfigManager, ProactivityLevel, Settings
from ui.desktop.styles import FONT_FAMILY, FONT_SIZE_BODY, FONT_SIZE_TITLE, Palette
from voice.audio import AudioCapture

logger = logging.getLogger("steve.ui.desktop.first_run_wizard")


class FirstRunWizard(ctk.CTk):
    def __init__(self, config_manager: ConfigManager, palette: Palette, on_done: Callable[[Settings], None]):
        super().__init__()
        self._config_manager = config_manager
        self._on_done = on_done
        self.palette = palette

        ctk.set_appearance_mode("light" if palette.bg == "#F4F5F7" else "dark")
        self.title("Bem-vindo ao Steve")
        self.geometry("460x620")
        self.resizable(False, False)
        self.configure(fg_color=palette.bg)

        ctk.CTkLabel(
            self, text="Bem-vindo ao Steve", text_color=palette.text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_TITLE, weight="bold"),
        ).pack(pady=(24, 4))
        ctk.CTkLabel(
            self, text="Vamos configurar seu assistente.", text_color=palette.text_muted,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_BODY),
        ).pack(pady=(0, 16))

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="both", expand=True, padx=24)

        self.name_var = ctk.StringVar(value="")
        self._entry(form, "1. Como você gostaria de ser chamado?", self.name_var)

        self.language_var = ctk.StringVar(value="pt-BR")
        self._entry(form, "2. Idioma preferido", self.language_var)

        self.model_var = ctk.StringVar(value="llama3.2")
        self._entry(form, "3. Modelo Ollama a utilizar", self.model_var)

        ctk.CTkLabel(form, text="4. Microfone", text_color=palette.text_muted, anchor="w",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_BODY)).pack(fill="x", pady=(12, 2))
        mic_row = ctk.CTkFrame(form, fg_color="transparent")
        mic_row.pack(fill="x")
        self.voice_input_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(mic_row, text="Habilitar entrada por voz", variable=self.voice_input_var,
                      text_color=palette.text).pack(side="left")
        ctk.CTkButton(mic_row, text="Testar microfone", width=140, fg_color=palette.surface_alt,
                      text_color=palette.text, hover_color=palette.border,
                      command=self._test_microphone).pack(side="right")
        self.mic_status_label = ctk.CTkLabel(form, text="", text_color=palette.text_muted,
                     font=ctk.CTkFont(family=FONT_FAMILY, size=11))
        self.mic_status_label.pack(fill="x", pady=(2, 0))

        ctk.CTkLabel(form, text="5. Voz", text_color=palette.text_muted, anchor="w",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_BODY)).pack(fill="x", pady=(12, 2))
        self.voice_output_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(form, text="Responder por voz (TTS)", variable=self.voice_output_var,
                      text_color=palette.text).pack(fill="x")

        self.proactivity_var = ctk.StringVar(value=ProactivityLevel.NORMAL.value)
        ctk.CTkLabel(form, text="6. Nível de proatividade", text_color=palette.text_muted, anchor="w",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_BODY)).pack(fill="x", pady=(12, 2))
        ctk.CTkOptionMenu(form, variable=self.proactivity_var,
                          values=[p.value for p in ProactivityLevel]).pack(fill="x")

        self.finish_button = ctk.CTkButton(
            self, text="Concluir", fg_color=palette.accent, text_color=palette.accent_text,
            command=self._handle_finish,
        )
        self.finish_button.pack(pady=20)

    def _entry(self, parent, label, var) -> None:
        ctk.CTkLabel(parent, text=label, text_color=self.palette.text_muted, anchor="w",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_BODY)).pack(fill="x", pady=(8, 2))
        ctk.CTkEntry(parent, textvariable=var).pack(fill="x")

    def _test_microphone(self) -> None:
        available = AudioCapture().is_microphone_available()
        if available:
            self.mic_status_label.configure(text="● Microfone detectado.", text_color=self.palette.success)
        else:
            self.mic_status_label.configure(text="● Nenhum microfone encontrado.", text_color=self.palette.danger)

    def _handle_finish(self) -> None:
        settings = Settings(
            user_name=self.name_var.get().strip() or "Usuário",
            language=self.language_var.get().strip() or "pt-BR",
            ai_model=self.model_var.get().strip() or "llama3.2",
            proactivity=ProactivityLevel(self.proactivity_var.get()),
            voice_input_enabled=bool(self.voice_input_var.get()),
            voice_output_enabled=bool(self.voice_output_var.get()),
            first_run_completed=True,
        )
        self._config_manager.save(settings)
        logger.info("Configuração inicial (GUI) concluída para '%s'.", settings.user_name)
        self.destroy()
        self._on_done(settings)
