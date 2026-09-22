from __future__ import annotations

import json

import pytest

from settings.config import (
    AIRoutingMode,
    ConfigManager,
    ProactivityLevel,
    Settings,
    SettingsValidationError,
)


def test_first_run_when_no_config_file(tmp_path):
    manager = ConfigManager(data_dir=tmp_path, config_file=tmp_path / "config.json")
    assert manager.is_first_run() is True


def test_save_and_load_round_trip(tmp_path):
    manager = ConfigManager(data_dir=tmp_path, config_file=tmp_path / "config.json")
    original = Settings(user_name="Junior", proactivity=ProactivityLevel.HIGH, first_run_completed=True)

    manager.save(original)
    loaded = manager.load()

    assert loaded.user_name == "Junior"
    assert loaded.proactivity == ProactivityLevel.HIGH
    assert manager.is_first_run() is False


def test_first_run_wizard_persists_answers(tmp_path):
    manager = ConfigManager(data_dir=tmp_path, config_file=tmp_path / "config.json")
    answers = iter(["Junior", "pt-BR", "HIGH", "llama3.2"])
    outputs: list[str] = []

    settings = manager.run_first_run_wizard(input_fn=lambda _: next(answers), output_fn=outputs.append)

    assert settings.user_name == "Junior"
    assert settings.proactivity == ProactivityLevel.HIGH
    assert settings.first_run_completed is True
    assert manager.is_first_run() is False
    assert any("configuração inicial concluída" in line.lower() for line in outputs)


def test_load_or_run_wizard_skips_wizard_when_already_configured(tmp_path):
    manager = ConfigManager(data_dir=tmp_path, config_file=tmp_path / "config.json")
    manager.save(Settings(user_name="Junior", first_run_completed=True))

    def fail_input(_):
        raise AssertionError("wizard should not run again")

    settings = manager.load_or_run_wizard(input_fn=fail_input, output_fn=lambda _: None)
    assert settings.user_name == "Junior"


# --- V1.2.1: AI Router configuration validation ---------------------------------------


def test_settings_default_ai_provider_and_routing_mode():
    settings = Settings()
    assert settings.ai_provider == "ollama"
    assert settings.ai_routing_mode == AIRoutingMode.AUTO


def test_empty_ai_model_is_rejected():
    """Regression guard for the 'SIM' incident: an invalid/empty value must be rejected
    where it's set, not silently accepted and left to fail later inside OllamaProvider."""
    with pytest.raises(SettingsValidationError):
        Settings(ai_model="")


def test_empty_ai_provider_is_rejected():
    with pytest.raises(SettingsValidationError):
        Settings(ai_provider="   ")


def test_invalid_ai_routing_mode_is_rejected():
    with pytest.raises(SettingsValidationError):
        Settings(ai_routing_mode="turbo")


def test_valid_ai_routing_mode_string_is_coerced_to_enum():
    settings = Settings(ai_routing_mode="manual")
    assert settings.ai_routing_mode == AIRoutingMode.MANUAL


def test_save_and_load_round_trip_preserves_router_config(tmp_path):
    manager = ConfigManager(data_dir=tmp_path, config_file=tmp_path / "config.json")
    original = Settings(
        user_name="Junior", ai_provider="ollama", ai_routing_mode=AIRoutingMode.MANUAL, first_run_completed=True
    )

    manager.save(original)
    loaded = manager.load()

    assert loaded.ai_provider == "ollama"
    assert loaded.ai_routing_mode == AIRoutingMode.MANUAL


def test_old_config_json_without_router_fields_still_loads_with_defaults(tmp_path):
    """Backward compatibility: a config.json saved before V1.2.1 has no ai_provider/
    ai_routing_mode keys at all — loading it must fill in the new defaults instead of
    failing, exactly like every other field added in a previous version."""
    config_file = tmp_path / "config.json"
    old_style_config = {
        "user_name": "Junior",
        "language": "pt-BR",
        "proactivity": "NORMAL",
        "ai_model": "llama3.2",
        "ollama_host": "http://localhost:11434",
        "permission_auto_approve_level": "LOW",
        "first_run_completed": True,
    }
    config_file.write_text(json.dumps(old_style_config), encoding="utf-8")

    manager = ConfigManager(data_dir=tmp_path, config_file=config_file)
    settings = manager.load()

    assert settings.ai_model == "llama3.2"
    assert settings.ai_provider == "ollama"
    assert settings.ai_routing_mode == AIRoutingMode.AUTO


def test_corrupt_ai_model_in_config_json_triggers_first_run_instead_of_crashing(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"ai_model": "", "first_run_completed": True}), encoding="utf-8")

    manager = ConfigManager(data_dir=tmp_path, config_file=config_file)

    assert manager.is_first_run() is True


# --- V1.3: lifecycle/startup configuration ---------------------------------------------


def test_settings_default_lifecycle_fields():
    settings = Settings()
    assert settings.autostart_enabled is False
    assert settings.minimize_to_tray is False
    assert settings.start_minimized is False


def test_save_and_load_round_trip_preserves_lifecycle_fields(tmp_path):
    manager = ConfigManager(data_dir=tmp_path, config_file=tmp_path / "config.json")
    original = Settings(
        user_name="Junior", autostart_enabled=True, minimize_to_tray=True, start_minimized=True,
        first_run_completed=True,
    )

    manager.save(original)
    loaded = manager.load()

    assert loaded.autostart_enabled is True
    assert loaded.minimize_to_tray is True
    assert loaded.start_minimized is True


def test_old_config_json_without_lifecycle_fields_still_loads_with_defaults(tmp_path):
    """Backward compatibility: a config.json saved before V1.3 has no autostart_enabled/
    minimize_to_tray/start_minimized keys — loading it must fill in the new defaults."""
    config_file = tmp_path / "config.json"
    old_style_config = {
        "user_name": "Junior",
        "ai_model": "llama3.2",
        "ai_provider": "ollama",
        "ai_routing_mode": "auto",
        "first_run_completed": True,
    }
    config_file.write_text(json.dumps(old_style_config), encoding="utf-8")

    manager = ConfigManager(data_dir=tmp_path, config_file=config_file)
    settings = manager.load()

    assert settings.autostart_enabled is False
    assert settings.minimize_to_tray is False
    assert settings.start_minimized is False
