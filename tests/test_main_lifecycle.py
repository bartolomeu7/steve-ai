"""Tests for the boot sequence wired into main.py: build_app_services() reporting
components to a LifecycleManager, and run_cli()'s first-run/returning-user greeting and
shutdown wiring. Doesn't require Ollama to be running — build_app_services() already
degrades gracefully either way (see tests/test_main_app_services.py), so these assert on
whichever real outcome (READY or DEGRADED) actually happens rather than assuming one."""
from __future__ import annotations

import logging

import main as main_module
from ai.base import AIMessage, AIResponse, ToolSpec
from core.lifecycle import LifecycleManager, LifecycleState
from security.confirmations import AutoDenyConfirmationService
from settings.config import Settings

_LOGGER = logging.getLogger("steve.tests.main_lifecycle")


def _isolated_settings(tmp_path, monkeypatch, **overrides) -> Settings:
    monkeypatch.setattr(main_module, "DATABASE_FILE", tmp_path / "steve.db")
    monkeypatch.setattr(main_module, "AUDIT_LOG_FILE", tmp_path / "audit.log")
    return Settings(user_name="Junior", ai_model="llama3.2", first_run_completed=True, **overrides)


def test_build_app_services_reports_essential_components_when_lifecycle_given(tmp_path, monkeypatch):
    settings = _isolated_settings(tmp_path, monkeypatch)
    lifecycle = LifecycleManager()
    lifecycle.begin_startup(first_run=False)

    services = main_module.build_app_services(
        settings, _LOGGER, confirmation_service=AutoDenyConfirmationService(), lifecycle=lifecycle
    )
    try:
        assert lifecycle.status.is_ok("memory") is True
        assert lifecycle.status.component("memory").essential is True
        assert lifecycle.status.is_ok("tools") is True
        assert lifecycle.status.component("tools").essential is True
        # ai_router's ok-ness mirrors services.ai_available exactly, whichever way it went
        assert lifecycle.status.is_ok("ai_router") is services.ai_available
        assert lifecycle.status.is_ok("voice") is True  # voice input disabled by default -> not a failure
        assert lifecycle.status.is_ok("security_engine") is True
    finally:
        services.security_engine.stop()
        services.database.close()


def test_build_app_services_without_lifecycle_still_works(tmp_path, monkeypatch):
    """Backward compatible: every pre-V1.3 caller omits `lifecycle` — must behave exactly
    as before (no crash, no attempt to report anything)."""
    settings = _isolated_settings(tmp_path, monkeypatch)

    services = main_module.build_app_services(settings, _LOGGER, confirmation_service=AutoDenyConfirmationService())

    try:
        assert services is not None
    finally:
        services.security_engine.stop()
        services.database.close()


def test_finish_startup_after_build_app_services_reflects_ai_availability(tmp_path, monkeypatch):
    settings = _isolated_settings(tmp_path, monkeypatch)
    lifecycle = LifecycleManager()
    lifecycle.begin_startup(first_run=False)

    services = main_module.build_app_services(settings, _LOGGER, lifecycle=lifecycle)
    try:
        status = lifecycle.finish_startup()
        expected_state = LifecycleState.READY if services.ai_available else LifecycleState.DEGRADED
        assert status.lifecycle_state == expected_state
    finally:
        services.security_engine.stop()
        services.database.close()


def test_run_cli_greets_first_run_user_and_shuts_down_cleanly(tmp_path, monkeypatch, capsys):
    settings = _isolated_settings(tmp_path, monkeypatch)
    config_manager = _FakeConfigManager(settings)
    monkeypatch.setattr(main_module, "run_chat_loop", lambda *a, **k: None)

    lifecycle = LifecycleManager()
    lifecycle.begin_startup(first_run=True, startup_mode="cli")

    main_module.run_cli(config_manager, _LOGGER, lifecycle, first_run=True)

    output = capsys.readouterr().out
    assert "prazer em conhecê-lo" in output.lower() or "prazer conhecê-lo" in output.lower()
    assert lifecycle.state == LifecycleState.STOPPED


def test_run_cli_greets_returning_user_differently(tmp_path, monkeypatch, capsys):
    settings = _isolated_settings(tmp_path, monkeypatch)
    config_manager = _FakeConfigManager(settings)
    monkeypatch.setattr(main_module, "run_chat_loop", lambda *a, **k: None)

    lifecycle = LifecycleManager()
    lifecycle.begin_startup(first_run=False, startup_mode="cli")

    main_module.run_cli(config_manager, _LOGGER, lifecycle, first_run=False)

    output = capsys.readouterr().out
    assert "bem-vindo de volta" in output.lower()
    assert "prazer" not in output.lower()


def test_run_cli_reports_degraded_notice_when_ai_unavailable(tmp_path, monkeypatch, capsys):
    settings = _isolated_settings(tmp_path, monkeypatch)
    config_manager = _FakeConfigManager(settings)
    monkeypatch.setattr(main_module, "run_chat_loop", lambda *a, **k: None)

    monkeypatch.setattr(main_module, "build_ai_router", lambda settings, audit_logger: _UnavailableRouterStub())

    lifecycle = LifecycleManager()
    lifecycle.begin_startup(first_run=False, startup_mode="cli")

    main_module.run_cli(config_manager, _LOGGER, lifecycle, first_run=False)

    output = capsys.readouterr().out
    assert "modo reduzido" in output.lower()
    # by the time run_cli() returns, shutdown has already moved the state past DEGRADED
    # to STOPPED — degraded_reasons survives that transition and is the durable record.
    assert lifecycle.status.degraded_reasons != []
    assert lifecycle.state == LifecycleState.STOPPED


class _FakeConfigManager:
    """Stands in for ConfigManager in run_cli(): load_or_run_wizard() just returns the
    given settings, no real file I/O or input() prompts involved."""

    def __init__(self, settings: Settings):
        self._settings = settings

    def load_or_run_wizard(self, **kwargs) -> Settings:
        return self._settings


class _UnavailableRouterStub:
    """Minimal AIRouter-shaped stub used only to force build_app_services() down the
    ai_available=False path deterministically, regardless of whether a real Ollama
    happens to be running on the machine executing this test."""

    model = ""

    def is_available(self) -> bool:
        return False

    def chat(self, messages: list[AIMessage], tools: list[ToolSpec] | None = None, **kwargs) -> AIResponse:
        raise ConnectionError("stub: no provider available")
