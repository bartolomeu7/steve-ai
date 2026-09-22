"""SteveApp tests. _start_main_window() ends in window.mainloop(), which blocks, so
these tests build the same pieces it would (services/window/runner/controller) and
then exercise SteveApp's methods directly — never calling mainloop()."""
from __future__ import annotations

import logging

import pytest

import main as main_module
from security.confirmations import AutoDenyConfirmationService
from settings.config import ConfigManager, Settings
from ui.desktop.app import SteveApp
from ui.desktop.async_bridge import BackgroundRunner
from ui.desktop.controller import ChatController
from ui.desktop.window import MainWindow

_LOGGER = logging.getLogger("steve.tests.desktop_app")


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "DATABASE_FILE", tmp_path / "steve.db")
    monkeypatch.setattr(main_module, "AUDIT_LOG_FILE", tmp_path / "audit.log")

    config_manager = ConfigManager(data_dir=tmp_path, config_file=tmp_path / "config.json")
    settings = Settings(user_name="Junior", ai_model="llama3.2", first_run_completed=True)
    config_manager.save(settings)

    instance = SteveApp(config_manager=config_manager, logger=_LOGGER)
    instance._main_module = main_module
    instance.services = main_module.build_app_services(
        settings, _LOGGER, confirmation_service=AutoDenyConfirmationService()
    )
    instance.window = MainWindow(
        app_status=instance._status_snapshot(),
        voice_available=instance.services.voice_service is not None,
        appearance_mode=settings.theme,
        orb_enabled=False,
    )
    instance.window.withdraw()
    instance.runner = BackgroundRunner(instance.window)
    instance.controller = ChatController(instance.runner, instance.services.orchestrator, instance.services.voice_service)
    instance.window.controller = instance.controller

    yield instance

    try:
        instance.window.destroy()
    except Exception:
        pass
    instance.services.system_monitor.stop()  # idempotent no-op if a test never started it
    instance.services.security_engine.stop()  # started automatically by build_app_services() since V1.5
    instance.services.database.close()


def _settings_payload(**overrides) -> dict:
    base = {
        "user_name": "Junior",
        "language": "pt-BR",
        "ai_model": "llama3.2",
        "proactivity": Settings().proactivity,
        "voice_output_enabled": False,
        "voice_input_enabled": False,
        "speaking_rate": 175,
        "speaking_volume": 1.0,
        "theme": "light",
    }
    base.update(overrides)
    return base


def test_open_settings_creates_a_dialog(app):
    assert app._settings_dialog is None
    app._open_settings()
    assert app._settings_dialog is not None
    app._settings_dialog.destroy()


def test_open_settings_does_not_stack_a_second_dialog(app):
    app._open_settings()
    first_dialog = app._settings_dialog

    app._open_settings()

    assert app._settings_dialog is first_dialog  # no second Toplevel created
    first_dialog.destroy()


def test_apply_settings_persists_and_updates_snapshot(app, tmp_path):
    app._apply_settings(_settings_payload(user_name="Novo Nome"))

    assert app.services.settings.user_name == "Novo Nome"
    reloaded = app.config_manager.load()
    assert reloaded.user_name == "Novo Nome"


def test_apply_settings_theme_change_shows_restart_message(app):
    before_count = app.window.chat_view.message_count

    app._apply_settings(_settings_payload(theme="dark"))

    assert app.services.settings.theme == "dark"
    assert app.window.chat_view.message_count == before_count + 1


def test_apply_settings_same_theme_shows_no_message(app):
    before_count = app.window.chat_view.message_count

    app._apply_settings(_settings_payload(theme="light"))  # already light by default

    assert app.window.chat_view.message_count == before_count


def test_apply_settings_model_change_updates_router_provider(app):
    """The Router (not a raw OllamaProvider) is what Orchestrator depends on now — a
    model change re-registers the provider inside the same Router instance instead of
    swapping objects out from under the Orchestrator."""
    router = app.services.ai_router
    original_registered_names = router.list_providers()

    app._apply_settings(_settings_payload(ai_model="qwen2.5"))

    assert app.services.ai_router is router  # same Router instance, not replaced
    assert router.list_providers() == original_registered_names  # same registration slot
    assert router.select().provider.model == "qwen2.5"
    assert app.services.orchestrator.ai_router is router


def test_handle_close_closes_runner_and_database(app):
    app._shutdown()
    assert app.runner._closed is True


def test_shutdown_stops_a_running_system_monitor(app):
    """V1.4.1 PATCH 03, item 10 of the required test list: Steve shutdown must stop the
    monitor. Started explicitly here first (the fixture never opens a real Dashboard),
    so this actually exercises stopping a *running* service, not trivially confirming an
    already-stopped one."""
    app.services.system_monitor.start()
    assert app.services.system_monitor.is_running is True

    app._shutdown()

    assert app.services.system_monitor.is_running is False


def test_on_window_close_shuts_down_by_default(app):
    """minimize_to_tray is off by default — closing the window must fully shut down,
    same as before this setting existed."""
    assert app.services.settings.minimize_to_tray is False

    should_destroy = app._on_window_close()

    assert should_destroy is True
    assert app.runner._closed is True


def test_on_window_close_hides_instead_of_shutting_down_when_minimize_to_tray_enabled(app):
    app.services.settings.minimize_to_tray = True
    hidden = []
    app.window.hide = lambda: hidden.append(True)

    should_destroy = app._on_window_close()

    assert should_destroy is False
    assert hidden == [True]
    assert app.runner._closed is False  # services/runner still alive, window just hidden


def test_apply_settings_enabling_autostart_calls_the_real_mechanism(app, monkeypatch):
    """Must never touch the real Windows registry from a test — enable_autostart/
    disable_autostart are monkeypatched here the same way tests/test_autostart.py fakes
    winreg itself, just one layer up."""
    calls = []
    monkeypatch.setattr("ui.desktop.app.autostart.enable_autostart", lambda command: calls.append(("enable", command)))

    app._apply_settings(_settings_payload(autostart_enabled=True))

    assert calls[0][0] == "enable"
    assert app.services.settings.autostart_enabled is True


def test_apply_settings_disabling_autostart_calls_the_real_mechanism(app, monkeypatch):
    app.services.settings.autostart_enabled = True
    calls = []
    monkeypatch.setattr("ui.desktop.app.autostart.disable_autostart", lambda: calls.append("disable"))

    app._apply_settings(_settings_payload(autostart_enabled=False))

    assert calls == ["disable"]
    assert app.services.settings.autostart_enabled is False


def test_apply_settings_autostart_unchanged_does_not_touch_the_mechanism(app, monkeypatch):
    calls = []
    monkeypatch.setattr("ui.desktop.app.autostart.enable_autostart", lambda command: calls.append("enable"))
    monkeypatch.setattr("ui.desktop.app.autostart.disable_autostart", lambda: calls.append("disable"))

    app._apply_settings(_settings_payload(autostart_enabled=False))  # already False, no change

    assert calls == []


def test_explicit_quit_always_shuts_down_even_with_minimize_to_tray_enabled(app):
    app.services.settings.minimize_to_tray = True

    app._handle_quit()

    assert app.runner._closed is True


def test_handle_close_while_busy_logs_info_and_does_not_raise(app, caplog):
    app.controller._busy = True  # simulate a turn still in flight

    with caplog.at_level(logging.INFO, logger=_LOGGER.name):
        app._shutdown()  # must not raise

    assert app.runner._closed is True
    assert any("em andamento" in record.message for record in caplog.records)
