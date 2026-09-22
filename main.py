"""Entry point. `python main.py` opens the desktop GUI by default; `--cli` opens the
terminal chat instead (kept for debugging/diagnostics/headless environments)."""
from __future__ import annotations

from pathlib import Path

import argparse
import logging
from dataclasses import dataclass

from ai.providers.ollama_provider import OllamaProvider
from ai.router import AIRouter, RoutingMode
from core.context import ContextManager
from core.events import EventBus
from core.greeting import GreetingService
from core.lifecycle import LifecycleManager, LifecycleState
from core.logging_setup import configure_logging
from core.orchestrator import Orchestrator
from core.session import SessionManager
from core.tool_manager import ToolManager
from memory.database import Database
from memory.service import MemoryService
from security.audit import AuditLogger
from security.confirmations import CLIConfirmationService, ConfirmationService
from security.engine import SecurityEngine
from security.permissions import PermissionLevel, PermissionManager
from settings.config import (
    APP_LOG_FILE,
    AUDIT_LOG_FILE,
    DATABASE_FILE,
    ConfigManager,
    Settings,
)
from settings.single_instance import SingleInstanceLock, bring_existing_instance_to_front
from system_monitor.service import SystemMonitorService
from tools.active_window import ActiveWindowTool
from tools.applications import ListProcessesTool, OpenApplicationTool
from tools.browser import OpenURLTool, PlayMusicTool, StopMusicTool
from tools.clipboard import ClipboardTool
from tools.filesystem import (
    CreateDirectoryTool,
    DeletePathTool,
    ListDirectoryTool,
    OpenFileTool,
    WriteTextFileTool,
)
from tools.notify import NotifyTool
from tools.steve_status import SteveStatusTool
from tools.memory import ForgetMemoryTool, RecallMemoriesTool, RememberFactTool
from tools.screenshot import ScreenshotTool
from tools.security import CheckSecurityStatusTool, ListSecurityFindingsTool, ScanFileTool
from tools.system import CheckCPUTool, CheckDiskTool, CheckRAMTool
from tools.volume import VolumeTool
from tools.web_search import WebSearchTool
from ui.cli import run_chat_loop
from voice.audio import AudioCapture
from voice.service import VoiceService
from voice.stt import FasterWhisperSTT
from voice.tts import NullTTS, TextToSpeech, create_tts


def register_default_tools(
    tool_manager: ToolManager,
    memory_service: MemoryService,
    security_engine: SecurityEngine,
    event_bus: EventBus | None = None,
    *,
    preset: str = "completo",
    status_fn=None,
) -> None:
    """Register tools. preset='essencial' keeps a lean set for small models."""
    essencial_names = {
        "open_application",
        "open_url",
        "play_music",
        "stop_music",
        "web_search",
        "list_directory",
        "open_file",
        "clipboard",
        "volume",
        "remember_fact",
        "recall_memories",
        "steve_status",
        "notify",
    }

    for tool_cls in (
        OpenApplicationTool,
        ListProcessesTool,
        CheckCPUTool,
        CheckRAMTool,
        CheckDiskTool,
        ListDirectoryTool,
        CreateDirectoryTool,
        OpenFileTool,
        WriteTextFileTool,
        DeletePathTool,
        OpenURLTool,
        ActiveWindowTool,
        ClipboardTool,
        VolumeTool,
        ScreenshotTool,
        WebSearchTool,
        NotifyTool,
    ):
        tool = tool_cls()
        if preset == "essencial" and tool.name not in essencial_names:
            continue
        tool_manager.register(tool)

    play = PlayMusicTool(event_bus=event_bus)
    if preset != "essencial" or play.name in essencial_names:
        tool_manager.register(play)
    stop = StopMusicTool(event_bus=event_bus)
    if preset != "essencial" or stop.name in essencial_names:
        tool_manager.register(stop)

    for memory_tool_cls in (RememberFactTool, ForgetMemoryTool, RecallMemoriesTool):
        tool = memory_tool_cls(memory_service=memory_service)
        if preset == "essencial" and tool.name not in essencial_names:
            continue
        tool_manager.register(tool)

    if preset != "essencial":
        for security_tool_cls in (CheckSecurityStatusTool, ListSecurityFindingsTool, ScanFileTool):
            tool_manager.register(security_tool_cls(security_engine=security_engine))

    status = SteveStatusTool(status_fn=status_fn)
    if preset != "essencial" or status.name in essencial_names:
        tool_manager.register(status)



def build_tts(settings: Settings) -> TextToSpeech:
    """Builds the best TTS available. `settings.tts_engine` picks the strategy:
    "auto"/"edge" tries EdgeTTS's neural voice first (falls back to pyttsx3 on its own
    if edge-tts/pygame aren't installed or there's no internet); "pyttsx3" forces the
    offline engine outright. Either way this never raises — worst case is NullTTS."""
    if not (settings.voice_output_enabled or settings.voice_input_enabled):
        return NullTTS()
    return create_tts(
        preferred=settings.tts_engine,
        edge_voice=settings.tts_voice or "pt-BR-AntonioNeural",
        pyttsx3_voice_id=settings.tts_voice,
        rate=settings.speaking_rate,
        volume=settings.speaking_volume,
    )


def build_voice_service(
    settings: Settings,
    orchestrator: Orchestrator,
    tts: TextToSpeech,
    logger: logging.Logger,
    event_bus: EventBus | None = None,
) -> VoiceService | None:
    if not settings.voice_input_enabled:
        return None

    audio_capture = AudioCapture(input_device=settings.voice_input_device)
    if not audio_capture.is_microphone_available():
        logger.warning("voice_input_enabled=True, mas nenhum microfone foi encontrado; voz por voz desativada.")
        return None

    stt_language = settings.language.split("-")[0] if settings.language else "pt"
    stt = FasterWhisperSTT(model_size=settings.stt_model_size, language=stt_language)
    if not stt.is_available():
        logger.warning("Motor de STT (faster-whisper) indisponível; entrada por voz desativada.")
        return None

    logger.info("Entrada por voz pronta (modelo STT: %s, dispositivo: %s).", settings.stt_model_size, settings.voice_input_device or "padrão")
    return VoiceService(audio_capture=audio_capture, stt=stt, orchestrator=orchestrator, tts=tts, event_bus=event_bus)


def build_ai_router(settings: Settings, audit_logger: AuditLogger) -> AIRouter:
    """The only place that constructs concrete AIProviders and registers them into the
    Router. Ollama is the only provider today; adding a second one later means
    registering it here — nothing that calls build_ai_router() (Orchestrator, GUI) needs
    to change."""
    router = AIRouter(
        mode=RoutingMode(settings.ai_routing_mode.value),
        preferred_provider=settings.ai_provider,
        audit_logger=audit_logger,
    )
    ollama_provider = OllamaProvider(
        model=settings.ai_model, host=settings.ollama_host, timeout=settings.ai_timeout_seconds
    )
    router.register(settings.ai_provider, ollama_provider)
    return router


@dataclass
class AppServices:
    """Everything a UI (CLI or GUI) needs to talk to Steve's Core. Built once per
    process by build_app_services() so no interface duplicates this wiring."""

    settings: Settings
    database: Database
    memory_service: MemoryService
    ai_router: AIRouter
    ai_available: bool
    tool_manager: ToolManager
    orchestrator: Orchestrator
    tts: TextToSpeech
    voice_service: VoiceService | None
    event_bus: EventBus
    system_monitor: SystemMonitorService
    security_engine: SecurityEngine


def build_app_services(
    settings: Settings,
    logger: logging.Logger,
    confirmation_service: ConfirmationService | None = None,
    lifecycle: LifecycleManager | None = None,
) -> AppServices:
    """Boot sequence steps 4-9 (memory -> AI Router -> tools -> Orchestrator -> voice) —
    see docs/ARCHITECTURE.md for the full ordered sequence, config/logging (steps 1-3)
    happen before this is called, GUI/restore-state/greeting (steps 10-13) happen after.
    `lifecycle`, when given, gets each component reported as it's built — optional and
    backward compatible (every pre-existing caller that omits it behaves exactly as
    before, just without the observability). `lifecycle.event_bus` (V1.3's EventBus,
    previously only used by LifecycleManager itself) is reused as THE single event bus
    for the whole process — Orchestrator/VoiceService/SystemMonitorService all publish
    on it; no second event system is created."""
    event_bus = lifecycle.event_bus if lifecycle is not None else EventBus()

    database = Database(DATABASE_FILE)
    memory_service = MemoryService(database)
    logger.info("Banco de dados de memória pronto em %s.", DATABASE_FILE)
    if lifecycle:
        lifecycle.report_component("memory", ok=True, essential=True)

    audit_logger = AuditLogger(AUDIT_LOG_FILE)
    ai_router = build_ai_router(settings, audit_logger)
    ai_available = ai_router.is_available()
    if ai_available:
        logger.info(
            "AI Router pronto — provider '%s', modelo '%s'.", settings.ai_provider, settings.ai_model
        )
        if lifecycle:
            lifecycle.report_component("ai_router", ok=True)
    else:
        logger.warning(
            "Nenhum provider de IA disponível agora (provider '%s', modelo '%s' em %s). "
            "Se for o Ollama, confira se está em execução e se rodou 'ollama pull %s'.",
            settings.ai_provider,
            settings.ai_model,
            settings.ollama_host,
            settings.ai_model,
        )
        if lifecycle:
            lifecycle.report_component(
                "ai_router", ok=False, detail=f"provider '{settings.ai_provider}' indisponível"
            )

    memory_service.purge_expired()

    # Constructed before tool registration (unlike V1.4, where this was built near the
    # end) because security/tools.py's tools need a live SecurityEngine to register
    # against — see AppServices.security_engine and docs/ARCHITECTURE.md's Security
    # Center section for why this starts immediately (unlike SystemMonitorService,
    # which only starts when the Dashboard is opened): "real-time protection" that only
    # runs while a dashboard window is open would not be real-time.
    system_monitor = SystemMonitorService(
        event_bus=event_bus,
        metrics_interval=settings.system_monitor_interval_seconds,
    )
    if lifecycle:
        lifecycle.report_component("system_monitor", ok=True)

    security_engine = SecurityEngine(event_bus=event_bus, system_monitor=system_monitor, audit_logger=audit_logger)
    security_engine.start()
    if lifecycle:
        lifecycle.report_component("security_engine", ok=True)

    tool_manager = ToolManager()
    preset = str(getattr(settings, "tools_preset", "completo") or "completo").strip().lower()
    if preset not in ("essencial", "completo"):
        preset = "completo"

    def _steve_status():
        return {
            "ui_mode": getattr(settings, "ui_mode", "hud"),
            "ai_model": settings.ai_model,
            "ai_provider": settings.ai_provider,
            "tools_preset": preset,
            "tools_count": len(tool_manager.list_tools()),
            "learning_root": r"G:\Grok\SteveLearning",
            "voice_input": bool(settings.voice_input_enabled),
            "project_root": str(Path(__file__).resolve().parent),
        }

    register_default_tools(
        tool_manager,
        memory_service,
        security_engine,
        event_bus=event_bus,
        preset=preset,
        status_fn=_steve_status,
    )
    logger.info("%d ferramentas registradas.", len(tool_manager.list_tools()))
    if lifecycle:
        lifecycle.report_component("tools", ok=True, essential=True)

    context_manager = ContextManager(memory_service=memory_service)
    session = SessionManager()
    # Map settings enum/str onto PermissionLevel
    _auto = getattr(settings, "permission_auto_approve_level", PermissionLevel.LOW)
    try:
        auto_level = (
            _auto
            if isinstance(_auto, PermissionLevel)
            else PermissionLevel.from_str(getattr(_auto, "value", _auto))
        )
    except Exception:
        auto_level = PermissionLevel.LOW
    permission_manager = PermissionManager(auto_approve_up_to=auto_level)

    orchestrator = Orchestrator(
        ai_router=ai_router,
        tool_manager=tool_manager,
        context_manager=context_manager,
        session=session,
        memory_service=memory_service,
        settings=settings,
        permission_manager=permission_manager,
        confirmation_service=confirmation_service or CLIConfirmationService(),
        audit_logger=audit_logger,
        event_bus=event_bus,
    )

    tts = build_tts(settings)
    voice_service = build_voice_service(settings, orchestrator, tts, logger, event_bus=event_bus)
    if lifecycle:
        if settings.voice_input_enabled and voice_service is None:
            lifecycle.report_component("voice", ok=False, detail="microfone/STT indisponível")
        else:
            lifecycle.report_component("voice", ok=True)

    return AppServices(
        settings=settings,
        database=database,
        memory_service=memory_service,
        ai_router=ai_router,
        ai_available=ai_available,
        tool_manager=tool_manager,
        orchestrator=orchestrator,
        tts=tts,
        voice_service=voice_service,
        event_bus=event_bus,
        system_monitor=system_monitor,
        security_engine=security_engine,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Steve Desktop Assistant")
    parser.add_argument(
        "--cli", action="store_true", help="Inicia a interface de linha de comando em vez da GUI."
    )
    return parser.parse_args(argv)


def run_cli(config_manager: ConfigManager, logger: logging.Logger, lifecycle: LifecycleManager, first_run: bool) -> None:
    settings = config_manager.load_or_run_wizard()
    logger.info("Configurações carregadas para o usuário '%s'.", settings.user_name)
    lifecycle.report_component("config", ok=True, essential=True)

    services = build_app_services(settings, logger, lifecycle=lifecycle)
    status = lifecycle.finish_startup()

    greeting = GreetingService().greet(settings.user_name, is_first_run=first_run)
    print(f"\nSteve: {greeting}\n")
    if status.lifecycle_state == LifecycleState.DEGRADED:
        print(f"Steve: Aviso — iniciei em modo reduzido ({'; '.join(status.degraded_reasons)}).\n")

    try:
        run_chat_loop(
            services.orchestrator,
            tts=services.tts,
            voice_output_enabled=settings.voice_output_enabled,
            voice_service=services.voice_service,
            push_to_talk_key=settings.push_to_talk_key,
            voice_streaming_enabled=settings.voice_streaming_enabled,
        )
    finally:
        lifecycle.begin_shutdown()
        services.system_monitor.stop()  # safe no-op if never started (CLI never opens a Dashboard)
        services.security_engine.stop()
        services.database.close()
        lifecycle.finish_shutdown()
        logger.info("Steve encerrado.")


def run_gui(config_manager: ConfigManager, logger: logging.Logger, lifecycle: LifecycleManager) -> None:
    from ui.desktop.app import SteveApp

    SteveApp(config_manager=config_manager, logger=logger, lifecycle=lifecycle).run()


def main() -> None:
    args = parse_args()
    logger = configure_logging(APP_LOG_FILE)
    logger.info("Iniciando Steve...")

    lock = SingleInstanceLock()
    if not lock.acquire():
        logger.warning("Já existe uma instância do Steve em execução — não abrindo uma segunda.")
        if not bring_existing_instance_to_front():
            logger.info("Não encontrei a janela da instância existente para trazer ao primeiro plano.")
        return

    try:
        config_manager = ConfigManager()
        first_run = config_manager.is_first_run()
        lifecycle = LifecycleManager()
        lifecycle.begin_startup(first_run=first_run, startup_mode="cli" if args.cli else "gui")

        if args.cli:
            run_cli(config_manager, logger, lifecycle, first_run)
        else:
            run_gui(config_manager, logger, lifecycle)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
