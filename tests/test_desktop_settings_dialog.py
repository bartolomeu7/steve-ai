"""SettingsDialog tests: it only collects a dict and hands it to on_save — it must
never touch ConfigManager/services itself (that's the caller's job, see ui/desktop/app.py)."""
from __future__ import annotations

import pytest

from settings.config import ProactivityLevel, Settings
from ui.desktop.settings_dialog import SettingsDialog
from ui.desktop.styles import LIGHT
from ui.desktop.window import MainWindow


@pytest.fixture
def parent():
    w = MainWindow(
        app_status={"model": "llama3.2", "ollama_online": True, "mic_available": True, "voice_available": True, "tools_count": 9},
        voice_available=True,
        orb_enabled=False,
    )
    w.withdraw()
    yield w
    try:
        w.destroy()
    except Exception:
        pass


def test_dialog_prefills_from_existing_settings(parent):
    settings = Settings(user_name="Junior", ai_model="llama3.2", proactivity=ProactivityLevel.HIGH)
    dialog = SettingsDialog(parent, settings, LIGHT, on_save=lambda u: None)
    try:
        assert dialog.name_var.get() == "Junior"
        assert dialog.model_var.get() == "llama3.2"
        assert dialog.proactivity_var.get() == "HIGH"
    finally:
        dialog.destroy()


def test_save_collects_edited_values(parent):
    settings = Settings(user_name="Junior", ai_model="llama3.2")
    received = {}
    dialog = SettingsDialog(parent, settings, LIGHT, on_save=received.update)

    dialog.name_var.set("Novo Nome")
    dialog.model_var.set("qwen2.5")
    dialog.voice_output_var.set(True)
    dialog.rate_var.set(210)
    dialog._handle_save()

    assert received["user_name"] == "Novo Nome"
    assert received["ai_model"] == "qwen2.5"
    assert received["voice_output_enabled"] is True
    assert received["speaking_rate"] == 210


def test_save_falls_back_to_defaults_for_blank_required_fields(parent):
    settings = Settings(user_name="Junior", ai_model="llama3.2")
    received = {}
    dialog = SettingsDialog(parent, settings, LIGHT, on_save=received.update)

    dialog.name_var.set("   ")
    dialog.model_var.set("")
    dialog._handle_save()

    assert received["user_name"] == "Usuário"
    assert received["ai_model"] == "llama3.2"


def test_save_does_not_mutate_the_original_settings_object(parent):
    """The dialog only reports intent via on_save — applying it is main.SteveApp's job."""
    settings = Settings(user_name="Junior", ai_model="llama3.2")
    dialog = SettingsDialog(parent, settings, LIGHT, on_save=lambda u: None)

    dialog.name_var.set("Outro Nome")
    dialog._handle_save()

    assert settings.user_name == "Junior"  # unchanged


def test_dialog_prefills_theme_from_settings(parent):
    settings = Settings(user_name="Junior", ai_model="llama3.2", theme="dark")
    dialog = SettingsDialog(parent, settings, LIGHT, on_save=lambda u: None)
    try:
        assert dialog.theme_var.get() == "Escuro"
    finally:
        dialog.destroy()


def test_save_includes_theme_choice(parent):
    settings = Settings(user_name="Junior", ai_model="llama3.2", theme="light")
    received = {}
    dialog = SettingsDialog(parent, settings, LIGHT, on_save=received.update)

    dialog.theme_var.set("Escuro")
    dialog._handle_save()

    assert received["theme"] == "dark"


def test_dialog_prefills_cyber_theme_from_settings(parent):
    settings = Settings(user_name="Junior", ai_model="llama3.2", theme="cyber")
    dialog = SettingsDialog(parent, settings, LIGHT, on_save=lambda u: None)
    try:
        assert dialog.theme_var.get() == "Cyber"
    finally:
        dialog.destroy()


def test_save_includes_theme_choice_cyber(parent):
    settings = Settings(user_name="Junior", ai_model="llama3.2", theme="light")
    received = {}
    dialog = SettingsDialog(parent, settings, LIGHT, on_save=received.update)

    dialog.theme_var.set("Cyber")
    dialog._handle_save()

    assert received["theme"] == "cyber"


def test_dialog_prefills_voice_streaming_toggle_from_settings(parent):
    settings = Settings(user_name="Junior", ai_model="llama3.2", voice_streaming_enabled=True)
    dialog = SettingsDialog(parent, settings, LIGHT, on_save=lambda u: None)
    try:
        assert dialog.voice_streaming_var.get() is True
    finally:
        dialog.destroy()


def test_save_includes_voice_streaming_choice(parent):
    settings = Settings(user_name="Junior", ai_model="llama3.2")
    received = {}
    dialog = SettingsDialog(parent, settings, LIGHT, on_save=received.update)

    dialog.voice_streaming_var.set(True)
    dialog._handle_save()

    assert received["voice_streaming_enabled"] is True


def test_dialog_prefills_lifecycle_toggles_from_settings(parent):
    settings = Settings(
        user_name="Junior", ai_model="llama3.2",
        autostart_enabled=True, minimize_to_tray=True, start_minimized=True,
    )
    dialog = SettingsDialog(parent, settings, LIGHT, on_save=lambda u: None)
    try:
        assert dialog.autostart_var.get() is True
        assert dialog.minimize_to_tray_var.get() is True
        assert dialog.start_minimized_var.get() is True
    finally:
        dialog.destroy()


def test_save_includes_lifecycle_toggles(parent):
    settings = Settings(user_name="Junior", ai_model="llama3.2")
    received = {}
    dialog = SettingsDialog(parent, settings, LIGHT, on_save=received.update)

    dialog.autostart_var.set(True)
    dialog.minimize_to_tray_var.set(True)
    dialog.start_minimized_var.set(True)
    dialog._handle_save()

    assert received["autostart_enabled"] is True
    assert received["minimize_to_tray"] is True
    assert received["start_minimized"] is True
