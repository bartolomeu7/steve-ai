"""FirstRunWizard tests: same Settings/ConfigManager the CLI wizard uses, just filled
via widgets instead of input(). Never depends on a real microphone."""
from __future__ import annotations

from settings.config import ConfigManager, ProactivityLevel
from ui.desktop.first_run_wizard import FirstRunWizard
from ui.desktop.styles import LIGHT


def test_finish_saves_settings_via_the_real_config_manager(tmp_path, monkeypatch):
    config_manager = ConfigManager(data_dir=tmp_path, config_file=tmp_path / "config.json")
    monkeypatch.setattr(
        "ui.desktop.first_run_wizard.AudioCapture.is_microphone_available", lambda self: True
    )

    done = {}
    wizard = FirstRunWizard(config_manager, LIGHT, on_done=lambda s: done.update({"settings": s}))
    wizard.withdraw()

    wizard.name_var.set("Junior")
    wizard.language_var.set("pt-BR")
    wizard.model_var.set("llama3.2")
    wizard.proactivity_var.set(ProactivityLevel.HIGH.value)
    wizard.voice_output_var.set(True)

    wizard._handle_finish()

    assert config_manager.is_first_run() is False
    saved = config_manager.load()
    assert saved.user_name == "Junior"
    assert saved.proactivity == ProactivityLevel.HIGH
    assert saved.voice_output_enabled is True
    assert saved.first_run_completed is True
    assert done["settings"].user_name == "Junior"


def test_test_microphone_button_reports_availability(tmp_path, monkeypatch):
    config_manager = ConfigManager(data_dir=tmp_path, config_file=tmp_path / "config.json")
    monkeypatch.setattr(
        "ui.desktop.first_run_wizard.AudioCapture.is_microphone_available", lambda self: False
    )

    wizard = FirstRunWizard(config_manager, LIGHT, on_done=lambda s: None)
    wizard.withdraw()
    try:
        wizard._test_microphone()
        assert "Nenhum microfone" in wizard.mic_status_label.cget("text")
    finally:
        wizard.destroy()
