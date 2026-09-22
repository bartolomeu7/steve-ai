"""Tests for main.py's shared service-wiring (build_app_services) and CLI/GUI dispatch
(parse_args) — the layer both ui/cli.py and ui/desktop/app.py are built on, so neither
interface duplicates Core wiring. Doesn't require Ollama to be running: is_available()
degrades to False gracefully rather than raising, so assertions only check wiring, not
connectivity."""
from __future__ import annotations

import logging

import main as main_module
from ai.router import AIRouter
from security.confirmations import AutoDenyConfirmationService, CLIConfirmationService
from settings.config import Settings

_LOGGER = logging.getLogger("steve.tests.main")


def test_parse_args_defaults_to_gui():
    args = main_module.parse_args([])
    assert args.cli is False


def test_parse_args_cli_flag():
    args = main_module.parse_args(["--cli"])
    assert args.cli is True


def test_build_app_services_wires_orchestrator_with_given_confirmation_service(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "DATABASE_FILE", tmp_path / "steve.db")
    monkeypatch.setattr(main_module, "AUDIT_LOG_FILE", tmp_path / "audit.log")

    settings = Settings(user_name="Junior", ai_model="llama3.2", first_run_completed=True)
    confirmation_service = AutoDenyConfirmationService()

    services = main_module.build_app_services(settings, _LOGGER, confirmation_service=confirmation_service)

    try:
        assert isinstance(services.ai_router, AIRouter)
        assert services.ai_router.list_providers() == ["ollama"]
        assert services.ai_router.select().provider.model == "llama3.2"
        assert services.orchestrator.ai_router is services.ai_router
        assert services.orchestrator.confirmation_service is confirmation_service
        assert len(services.tool_manager.list_tools()) >= 21  # +5 Pack 3 desktop tools (active_window, clipboard, volume, screenshot, web_search)
        assert isinstance(services.ai_available, bool)  # never raises even if Ollama is down
    finally:
        services.security_engine.stop()
        services.database.close()


def test_build_app_services_defaults_to_cli_confirmation_service(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "DATABASE_FILE", tmp_path / "steve.db")
    monkeypatch.setattr(main_module, "AUDIT_LOG_FILE", tmp_path / "audit.log")

    settings = Settings(user_name="Junior", ai_model="llama3.2", first_run_completed=True)
    services = main_module.build_app_services(settings, _LOGGER)

    try:
        assert isinstance(services.orchestrator.confirmation_service, CLIConfirmationService)
    finally:
        services.security_engine.stop()
        services.database.close()


def test_build_app_services_voice_disabled_gives_no_voice_service_and_null_tts(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "DATABASE_FILE", tmp_path / "steve.db")
    monkeypatch.setattr(main_module, "AUDIT_LOG_FILE", tmp_path / "audit.log")

    settings = Settings(
        user_name="Junior", ai_model="llama3.2", voice_input_enabled=False, voice_output_enabled=False,
        first_run_completed=True,
    )
    services = main_module.build_app_services(settings, _LOGGER)

    try:
        assert services.voice_service is None
        assert services.tts.is_available() is False
    finally:
        services.security_engine.stop()
        services.database.close()
