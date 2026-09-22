"""SteveApp: desktop entry point. Boots the FIRST_RUN wizard or loads settings, builds
the exact same services main.py's CLI path uses (main.build_app_services — no second
brain), and shows MainWindow. Contains no AI/tool/permission logic of its own."""
from __future__ import annotations

import logging

from ai.providers.ollama_provider import OllamaProvider
from core.greeting import GreetingService
from core.lifecycle import LifecycleManager, LifecycleState
from security.confirmations import GuiConfirmationService
from settings import autostart
from settings.config import ConfigManager, Settings
from ui.desktop.async_bridge import BackgroundRunner
from ui.desktop.controller import ChatController
from ui.desktop.dashboard.window import DashboardWindow
from ui.desktop.first_run_wizard import FirstRunWizard
from ui.desktop.orb.reactive_tts import OrbReactiveTTS
from ui.desktop.orb.states import OrbState
from ui.desktop.settings_dialog import SettingsDialog
from ui.desktop.styles import palette_for
from ui.desktop.media_player import open_internal_player, stop_internal_player
from ui.desktop.window import MainWindow
from tools.browser import MEDIA_PLAY_EVENT
from voice.always_listen import AlwaysListenService
from voice.wakeword import WAKE_EVENT, build_wake_detector


class SteveApp:
    def __init__(self, config_manager: ConfigManager, logger: logging.Logger, lifecycle: LifecycleManager | None = None):
        self.config_manager = config_manager
        self.logger = logger
        self.lifecycle = lifecycle or LifecycleManager()
        self.services = None
        self.window: MainWindow | None = None
        self.runner: BackgroundRunner | None = None
        self.controller: ChatController | None = None
        self._settings_dialog: SettingsDialog | None = None
        self._dashboard_window: DashboardWindow | None = None
        self._always_listen: AlwaysListenService | None = None


    
    def _on_media_stop(self, _payload=None) -> None:
        try:
            if self.window is not None:
                self.window.after(0, stop_internal_player)
            else:
                stop_internal_player()
        except Exception:
            self.logger.debug("media.stop handler failed", exc_info=True)


    def _confirm_on_ui(self, message: str) -> bool:
        """Marshal yes/no to the Tk UI thread (messagebox)."""
        import threading
        from tkinter import messagebox

        if self.window is None:
            return False
        result = {"ok": False}
        done = threading.Event()

        def _ask() -> None:
            try:
                result["ok"] = bool(
                    messagebox.askyesno("Steve - confirmacao", message, parent=self.window)
                )
            except Exception:
                result["ok"] = False
            finally:
                done.set()

        try:
            self.window.after(0, _ask)
            done.wait(timeout=120)
        except Exception:
            return False
        return bool(result["ok"])

    def run(self) -> None:
        # `lifecycle.begin_startup()` may already have been called by main.py (the normal
        # path, which needs first_run before dispatching to CLI or GUI); calling it again
        # here is a no-op-equivalent fallback for direct SteveApp use (tests, or a future
        # embedder) so first_run is always known by the time _start_main_window runs.
        if self.lifecycle.state == LifecycleState.CREATED:
            self.lifecycle.begin_startup(first_run=self.config_manager.is_first_run(), startup_mode="gui")
        is_first_run = self.lifecycle.status.first_run

        if is_first_run:
            FirstRunWizard(
                self.config_manager, palette_for("light"),
                on_done=lambda settings: self._start_main_window(settings, is_first_run=True),
            ).mainloop()
        else:
            self._start_main_window(self.config_manager.load(), is_first_run=False)

    # --- boot -----------------------------------------------------------------------
    def _start_main_window(self, settings: Settings, is_first_run: bool) -> None:
        import main as main_module  # local import: main.py imports ui.desktop.app for run_gui()

        self._main_module = main_module
        self.lifecycle.report_component("config", ok=True, essential=True)
        self.services = main_module.build_app_services(
            settings, self.logger, confirmation_service=GuiConfirmationService(self._confirm_on_ui), lifecycle=self.lifecycle
        )
        self.lifecycle.report_component("gui", ok=True, essential=True)
        status = self.lifecycle.finish_startup()

        self.window = MainWindow(
            ui_mode=getattr(settings, "ui_mode", "hud"),
            on_ui_mode_change=self._on_ui_mode_change,
            
            app_status=self._status_snapshot(),
            voice_available=self.services.voice_service is not None,
            appearance_mode=settings.theme,
            on_close=self._on_window_close,
            on_open_settings=self._open_settings,
            on_reply=self._speak_if_enabled,
            on_quit=self._handle_quit,
            on_open_dashboard=self._open_dashboard,
        )
        self.runner = BackgroundRunner(self.window)
        self.controller = ChatController(
            self.runner,
            self.services.orchestrator,
            self.services.voice_service,
            voice_streaming_enabled=settings.voice_streaming_enabled,
        )
        self.window.controller = self.controller
        self._wrap_tts_for_orb()
        self.services.event_bus.subscribe(MEDIA_PLAY_EVENT, self._on_media_play)
        self.services.event_bus.subscribe("media.stop", self._on_media_stop)
        self._start_always_listen(settings)

        greeting = GreetingService().greet(settings.user_name, is_first_run=is_first_run)
        self.window.chat_view.add_system_message(greeting)
        if status.lifecycle_state == LifecycleState.DEGRADED:
            self.window.chat_view.add_system_message(
                f"Iniciei em modo reduzido ({'; '.join(status.degraded_reasons)}) — vou avisar se algo falhar."
            )

        if settings.start_minimized and settings.minimize_to_tray:
            self.window.hide()
        else:
            try:
                self.window.update_idletasks()
                self.window._boot_borderless_fullscreen()
            except Exception:
                self.logger.exception("boot fullscreen/splash failed")
        self.window.mainloop()


    def _on_media_play(self, data: dict) -> None:
        """EventBus media.play -> open internal WebView2 player on the UI thread."""
        if self.window is None:
            return
        embed = (data or {}).get("embed_url") or ""
        title = (data or {}).get("title") or "Steve · Musica"
        window_title = f"Steve · {title}" if title else "Steve · Musica"

        def _open() -> None:
            ok = open_internal_player(embed, window_title)
            if ok and self.window is not None:
                self.window.chat_view.add_system_message(f"Tocando: {title}")
            elif self.window is not None:
                self.window.chat_view.add_system_message(
                    "Nao consegui abrir o player interno. Veja os logs."
                )

        try:
            self.window.after(0, _open)
        except Exception:
            self.logger.exception("Falha ao agendar player interno")


    def _start_always_listen(self, settings: Settings) -> None:
        """Continuous mic + wake 'Steve' — no push-to-talk required."""
        self._stop_always_listen()
        if not getattr(settings, "wake_word_enabled", True):
            return
        if self.services is None or self.services.voice_service is None:
            self.logger.info("Wake word: voz indisponivel; always-listen nao iniciado.")
            return
        phrase = getattr(settings, "wake_word", "steve") or "steve"
        detector = build_wake_detector(phrase=phrase)
        self._always_listen = AlwaysListenService(
            detector=detector,
            on_wake=self._on_wake_word,
            input_device=settings.voice_input_device,
        )
        self._always_listen.start()
        if self.window is not None:
            self.window._on_before_mic_cb = (lambda: self._always_listen.pause() if self._always_listen else None)
            self.window._on_after_voice_cb = (lambda: self._always_listen.resume() if self._always_listen else None)
            self.window.chat_view.add_system_message(
                f'Estou de ouvido — diga "{phrase.capitalize()}" e fale o comando.'
            )

    def _stop_always_listen(self) -> None:
        if self._always_listen is not None:
            try:
                self._always_listen.stop()
            except Exception:
                self.logger.exception("Falha ao parar always-listen")
            self._always_listen = None

    def _on_wake_word(self) -> None:
        """Called from always-listen thread — hop to UI thread."""
        if self.window is None:
            if self._always_listen:
                self._always_listen.resume()
            return
        try:
            self.window.after(0, self._handle_wake_on_ui)
        except Exception:
            self.logger.exception("Falha ao agendar wake na UI")
            if self._always_listen:
                self._always_listen.resume()

    def _handle_wake_on_ui(self) -> None:
        if self.window is None or self.controller is None or self.services is None:
            if self._always_listen:
                self._always_listen.resume()
            return
        if self.controller.busy:
            if self._always_listen:
                self._always_listen.resume()
            return

        ack = getattr(self.services.settings, "wake_ack_phrase", None) or "Estou te ouvindo."
        self.services.event_bus.publish(WAKE_EVENT, {"phrase": self.services.settings.wake_word})
        self.window.chat_view.add_system_message(f"🎙️ {ack}")
        self.window._on_orb_state(OrbState.LISTENING)

        def speak_ack():
            if self.services.tts.is_available() and self.services.settings.voice_output_enabled:
                self.services.tts.speak(ack)

        def after_ack(_result=None):
            if self.window is None or self.controller is None:
                if self._always_listen:
                    self._always_listen.resume()
                return
            self.window.input_bar.set_busy(True)
            started = self.controller.send_voice(
                on_status=self.window._on_status,
                on_result=self._on_wake_voice_result,
                on_error=self._on_wake_voice_error,
                on_orb_state=self.window._on_orb_state,
                on_mic_amplitude=self.window._on_mic_amplitude,
            )
            if not started:
                self.window.input_bar.set_busy(False)
                if self._always_listen:
                    self._always_listen.resume()

        self.runner.run(speak_ack, on_done=after_ack, on_error=after_ack)

    def _on_wake_voice_result(self, result) -> None:
        self.window._on_voice_result(result)
        if self._always_listen:
            self._always_listen.resume()

    def _on_wake_voice_error(self, message: str) -> None:
        self.window._on_error(message)
        if self._always_listen:
            self._always_listen.resume()

    def _status_snapshot(self) -> dict:
        services = self.services
        return {
            "model": services.settings.ai_model,
            "ai_provider": services.settings.ai_provider,
            "ai_routing_mode": services.settings.ai_routing_mode.value,
            "ollama_online": services.ai_available,
            "mic_available": services.voice_service is not None,
            "voice_available": services.tts.is_available(),
            "tools_count": len(services.tool_manager.list_tools()),
            "lifecycle_state": self.lifecycle.state.value,
        }

    # --- orb --------------------------------------------------------------------
    def _wrap_tts_for_orb(self) -> None:
        """Makes Steve's own voice drive the orb: wraps the plain TTS with
        OrbReactiveTTS, whose speak() still behaves exactly like the original (same
        blocking contract), so VoiceService/CLI never need to know the wrapping
        happened — it's purely additive."""
        if not self.services.tts.is_available():
            return
        reactive_tts = OrbReactiveTTS(self.services.tts, on_amplitude=self._on_speaking_amplitude)
        self.services.tts = reactive_tts
        if self.services.voice_service is not None:
            self.services.voice_service.tts = reactive_tts

    def _on_speaking_amplitude(self, amplitude: float) -> None:
        # called from a worker thread (inside TTS.speak()) — must go through the queue
        self.runner.post(self.window.orb.set_amplitude, amplitude)

    # --- settings ------------------------------------------------------------------
    def _open_settings(self) -> None:
        if self._settings_dialog is not None and self._settings_dialog.winfo_exists():
            self._settings_dialog.lift()
            self._settings_dialog.focus()
            return
        self._settings_dialog = SettingsDialog(
            self.window, settings=self.services.settings, palette=self.window.palette, on_save=self._apply_settings
        )

    # --- dashboard -------------------------------------------------------------------
    def _open_dashboard(self) -> None:
        """Same existing-window guard as _open_settings() — clicking "Dashboard" again
        while it's already open just brings it to front instead of creating a second
        window (and, indirectly, a second System Monitor collector)."""
        if self._dashboard_window is not None and self._dashboard_window.winfo_exists():
            self._dashboard_window.lift()
            self._dashboard_window.focus()
            return
        self._dashboard_window = DashboardWindow(
            self.window,
            palette=self.window.palette,
            event_bus=self.services.event_bus,
            system_monitor=self.services.system_monitor,
            security_engine=self.services.security_engine,
            runner=self.runner,
        )

    def _apply_settings(self, updated: dict) -> None:
        settings = self.services.settings
        before = (
            settings.ai_model,
            settings.voice_output_enabled,
            settings.voice_input_enabled,
            settings.speaking_rate,
            settings.speaking_volume,
        )
        theme_before = settings.theme
        autostart_before = settings.autostart_enabled

        for key, value in updated.items():
            setattr(settings, key, value)
        self.config_manager.save(settings)

        # ChatController keeps its own copy of this flag (not a live read of `settings`
        # — see ui/desktop/controller.py), so a toggle here needs an explicit push.
        self.controller.voice_streaming_enabled = settings.voice_streaming_enabled

        after = (
            settings.ai_model,
            settings.voice_output_enabled,
            settings.voice_input_enabled,
            settings.speaking_rate,
            settings.speaking_volume,
        )

        if after[0] != before[0]:
            # Re-register under the same provider name instead of swapping out the
            # Router/Orchestrator objects — the Router already exists to absorb exactly
            # this kind of change ("use a different model") without anyone downstream
            # needing a new object handed to them.
            new_provider = OllamaProvider(model=settings.ai_model, host=settings.ollama_host)
            self.services.ai_router.register(settings.ai_provider, new_provider)
            self.services.ai_available = self.services.ai_router.is_available()

        if settings.autostart_enabled != autostart_before:
            self._apply_autostart_change(settings.autostart_enabled)

        voice_related_changed = after[1:] != before[1:]
        if voice_related_changed:
            self.window.status_bar.set("activity", "Aplicando configurações de voz...")
            self.runner.run(self._rebuild_voice_services, on_done=self._on_voice_services_rebuilt)
        else:
            self.window.apply_status(self._status_snapshot())

        if settings.theme != theme_before:
            self.window.chat_view.add_system_message(
                "Tema alterado — reinicie o Steve para aplicar o novo visual."
            )

    def _apply_autostart_change(self, enabled: bool) -> None:
        """Enabling/disabling is opt-in and explicit (the user just toggled it in
        Settings) — never triggered automatically. Idempotent by construction: the
        registry write is a single named value, so enabling twice in a row (e.g. two
        _apply_settings calls without a change in between never reach here, but a
        redundant explicit enable elsewhere would) never creates a duplicate entry."""
        try:
            if enabled:
                autostart.enable_autostart(autostart.default_launch_command())
                self.logger.info("Iniciar com o Windows ativado.")
            else:
                autostart.disable_autostart()
                self.logger.info("Iniciar com o Windows desativado.")
        except NotImplementedError:
            self.logger.warning("Iniciar com o Windows não é suportado nesta plataforma.")

    def _rebuild_voice_services(self):
        settings = self.services.settings
        tts = self._main_module.build_tts(settings)
        voice_service = self._main_module.build_voice_service(
            settings, self.services.orchestrator, tts, self.logger, event_bus=self.services.event_bus
        )
        return tts, voice_service

    def _on_voice_services_rebuilt(self, result) -> None:
        tts, voice_service = result
        self.services.tts = tts
        self.services.voice_service = voice_service
        self.controller.voice_service = voice_service
        self._wrap_tts_for_orb()
        self.window.set_voice_available(voice_service is not None)
        self.window.apply_status(self._status_snapshot())
        self.window.status_bar.set_ready()
        self._start_always_listen(self.services.settings)

    # --- misc ------------------------------------------------------------------------
    def _speak_if_enabled(self, reply: str) -> None:
        if not self.services.settings.voice_output_enabled:
            return
        if not self.services.tts.is_available():
            return

        def speak_and_report_done():
            self.services.tts.speak(reply)

        def back_to_idle(_result=None) -> None:
            self.window._on_orb_state(OrbState.IDLE)

        self.window._on_orb_state(OrbState.SPEAKING)
        self.runner.run(speak_and_report_done, on_done=back_to_idle, on_error=back_to_idle)

    def _on_window_close(self) -> bool:
        """Bound to MainWindow's on_close (window-manager close button / Alt+F4).
        Returning False tells MainWindow to keep the window alive, hidden, instead of
        destroying it — the minimize-to-tray path. Explicit "Sair" (_handle_quit) always
        fully shuts down regardless of this setting; there must always be one
        unambiguous way to actually exit."""
        if self.services and self.services.settings.minimize_to_tray:
            self.window.hide()
            self.logger.info("Janela ocultada (minimizar ao fechar); Steve continua em execução.")
            return False
        self._shutdown()
        return True

    def _handle_quit(self) -> None:
        self._shutdown()

    def _shutdown(self) -> None:
        self.lifecycle.begin_shutdown()
        if self.controller is not None and self.controller.busy:
            # A turn is still running on its own daemon thread. We don't block window
            # close waiting for it (Ollama calls can take several seconds) or try to
            # cancel it mid-flight — it simply won't finish saving its turn to memory
            # once the database closes below, and will log a caught (not crashing)
            # sqlite3.ProgrammingError when it gets there. That's expected here, not a
            # bug: log it plainly so it doesn't read as a real failure during triage.
            self.logger.info(
                "Janela fechada com uma resposta ainda em andamento; ela será "
                "interrompida (a thread em segundo plano termina sozinha)."
            )
        if self.runner:
            self.runner.close()
        if self.services:
            self.services.system_monitor.stop()  # Dashboard-window close never stops it — only full Steve shutdown does
            self.services.security_engine.stop()  # started at boot (build_app_services), stopped only here
            self.services.database.close()
            self.logger.info("Steve (GUI) encerrado.")
        self.lifecycle.finish_shutdown()

    def _on_ui_mode_change(self, mode: str) -> None:
        """Persist HUD/Workspace toggle into settings + data/config.json."""
        mode = "workspace" if str(mode).lower() == "workspace" else "hud"
        try:
            if getattr(self, "services", None) is not None and hasattr(self.services, "settings"):
                self.services.settings.ui_mode = mode
        except Exception:
            pass
        try:
            import json
            from pathlib import Path
            for cand in (
                Path(__file__).resolve().parents[2] / "data" / "config.json",
                Path(r"C:\Users\HAKARI\Downloads\Steve AI") / "data" / "config.json",
            ):
                if cand.exists():
                    data = json.loads(cand.read_text(encoding="utf-8"))
                    data["ui_mode"] = mode
                    cand.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    break
        except Exception:
            pass
