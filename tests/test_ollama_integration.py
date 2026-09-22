"""Real end-to-end integration tests against a live Ollama server (no mocks).

These exercise the actual production wiring: UI -> Orchestrator -> AIProvider -> Ollama
-> native tool call -> ToolManager -> Tool -> real result -> Ollama -> final reply.
They are skipped automatically when Ollama isn't reachable, so the rest of the suite
stays green on machines without it (see tests/test_ai_provider.py for the mocked unit
tests that always run, and tests/test_orchestrator.py for the tool-calling loop logic).
"""
from __future__ import annotations

import pytest

from ai.providers.ollama_provider import OllamaProvider
from ai.router import AIRouter
from core.context import ContextManager
from core.events import EventBus
from core.orchestrator import UNAVAILABLE_MESSAGE, Orchestrator
from core.session import SessionManager
from core.tool_manager import ToolManager
import main as main_module
from main import register_default_tools
from memory.database import Database
from memory.service import MemoryService
from security.audit import AuditLogger
from security.confirmations import CLIConfirmationService
from security.engine import SecurityEngine
from security.permissions import PermissionLevel, PermissionManager
from settings.config import Settings
from system_monitor.service import SystemMonitorService
from tools.system import CheckCPUTool
from ui.cli import run_chat_loop


def _unstarted_security_engine() -> SecurityEngine:
    """These tests exercise the real Ollama tool-calling loop, not the Security
    Center — a never-started engine (no background thread, nothing to stop) is enough
    to satisfy register_default_tools()'s dependency without any of these tests taking
    on Security Center lifecycle management they don't otherwise care about."""
    event_bus = EventBus()
    system_monitor = SystemMonitorService(event_bus=event_bus, metrics_interval=30.0, process_interval=30.0)
    return SecurityEngine(event_bus=event_bus, system_monitor=system_monitor)

_LIVE_PROVIDER = OllamaProvider(model="llama3.2", host="http://localhost:11434")

requires_live_ollama = pytest.mark.skipif(
    not _LIVE_PROVIDER.is_available(),
    reason="Ollama não está rodando em http://localhost:11434 — pulando testes de integração real.",
)


def _build_real_orchestrator(tmp_path) -> Orchestrator:
    settings = Settings(user_name="Junior", ai_model="llama3.2", first_run_completed=True)
    database = Database(tmp_path / "steve.db")
    memory_service = MemoryService(database)

    tool_manager = ToolManager()
    register_default_tools(tool_manager, memory_service, _unstarted_security_engine())

    context_manager = ContextManager(memory_service=memory_service)
    session = SessionManager()
    permission_manager = PermissionManager(auto_approve_up_to=PermissionLevel.LOW)
    audit_logger = AuditLogger(tmp_path / "audit.log")

    return Orchestrator(
        ai_router=main_module.build_ai_router(settings, audit_logger),
        tool_manager=tool_manager,
        context_manager=context_manager,
        session=session,
        memory_service=memory_service,
        settings=settings,
        permission_manager=permission_manager,
        confirmation_service=CLIConfirmationService(),
        audit_logger=audit_logger,
    )


@requires_live_ollama
def test_real_ollama_provider_answers_a_simple_prompt():
    from ai.base import AIMessage

    response = _LIVE_PROVIDER.chat([AIMessage(role="user", content="Diga apenas 'ok'.")])
    assert response.content.strip() != ""


@requires_live_ollama
def test_orchestrator_real_flow_answers_identity_question(tmp_path):
    orchestrator = _build_real_orchestrator(tmp_path)

    reply = orchestrator.handle_message("Olá Steve, quem é você?")

    assert isinstance(reply, str)
    assert reply.strip() != ""
    assert reply != UNAVAILABLE_MESSAGE
    history = orchestrator.memory_service.get_recent_history(orchestrator.session.session_id)
    assert [h.role for h in history] == ["user", "assistant"]
    assert history[0].content == "Olá Steve, quem é você?"


@requires_live_ollama
def test_cli_end_to_end_real_flow_through_run_chat_loop(tmp_path, monkeypatch, capsys):
    """Drives the actual ui.cli.run_chat_loop function, exactly as main.py does."""
    orchestrator = _build_real_orchestrator(tmp_path)

    user_inputs = iter(["Olá Steve, quem é você?", "sair"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(user_inputs))

    run_chat_loop(orchestrator, tts=None, voice_output_enabled=False)

    output = capsys.readouterr().out
    assert "Steve:" in output
    assert "Até logo" in output


@requires_live_ollama
def test_real_flow_check_cpu_uses_the_real_windows_value(tmp_path, monkeypatch):
    """The literal scenario reported as broken: 'como está o uso de CPU?' must go through
    CLI -> Orchestrator -> AIProvider -> Ollama -> tool call -> ToolManager -> check_cpu
    -> real psutil result -> Ollama -> final reply, and the reply must use that real value
    instead of an invented one."""
    orchestrator = _build_real_orchestrator(tmp_path)

    real_cpu_values: list[float] = []
    original_execute = CheckCPUTool.execute

    def spying_execute(self, params):
        result = original_execute(self, params)
        real_cpu_values.append(result.data["cpu_percent"])
        return result

    monkeypatch.setattr(CheckCPUTool, "execute", spying_execute)

    user_inputs = iter(["Como está o uso de CPU neste computador agora?", "sair"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(user_inputs))

    run_chat_loop(orchestrator, tts=None, voice_output_enabled=False)

    assert real_cpu_values, "O modelo não solicitou a ferramenta check_cpu — regressão do bug original."
    real_value = real_cpu_values[0]
    assert 0.0 <= real_value <= 100.0

    reply = orchestrator.session.turns[-1].content
    possible_representations = {str(real_value), f"{real_value:.0f}", f"{round(real_value)}"}
    assert any(rep in reply for rep in possible_representations), (
        f"A resposta final não citou o valor real ({real_value}): {reply!r}"
    )


def test_orchestrator_handles_unreachable_ollama_for_real(tmp_path):
    """No mocking: points at a port nothing is listening on and expects the friendly fallback."""
    unreachable = OllamaProvider(model="llama3.2", host="http://localhost:19999", timeout=3)
    settings = Settings(user_name="Junior", ai_model="llama3.2", first_run_completed=True)
    database = Database(tmp_path / "steve.db")
    memory_service = MemoryService(database)
    tool_manager = ToolManager()
    register_default_tools(tool_manager, memory_service, _unstarted_security_engine())

    router = AIRouter()
    router.register("ollama", unreachable)

    orchestrator = Orchestrator(
        ai_router=router,
        tool_manager=tool_manager,
        context_manager=ContextManager(memory_service=memory_service),
        session=SessionManager(),
        memory_service=memory_service,
        settings=settings,
        permission_manager=PermissionManager(auto_approve_up_to=PermissionLevel.LOW),
        confirmation_service=CLIConfirmationService(),
        audit_logger=AuditLogger(tmp_path / "audit.log"),
    )

    reply = orchestrator.handle_message("Oi.")

    assert reply == UNAVAILABLE_MESSAGE
