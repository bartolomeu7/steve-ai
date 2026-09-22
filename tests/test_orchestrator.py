from __future__ import annotations

import json

from ai.base import AIMessage, AIProvider, AIResponse, StreamChunk, ToolCall
from ai.router import AIRouter
from core.orchestrator import EMPTY_RESPONSE_MESSAGE, TOOL_LIMIT_MESSAGE, UNAVAILABLE_MESSAGE, Orchestrator
from security.permissions import PermissionLevel
from tests.conftest import FakeConfirmationService
from tests.fakes import FailingAIProvider, FailingStreamingAIProvider, FakeAIProvider, FakeStreamingAIProvider
from tools.base import Tool, ToolResult


class _FakeMediumTool(Tool):
    name = "fake_medium_tool"
    description = "Ferramenta de teste de nível MEDIUM."
    permission_level = PermissionLevel.MEDIUM
    parameters_schema = {"type": "object", "properties": {}, "required": []}

    def validate(self, params: dict) -> tuple[bool, str]:
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        return ToolResult(success=True, verified=True, message="Executado.")


class _FakeFailingTool(Tool):
    name = "fake_failing_tool"
    description = "Ferramenta de teste que sempre falha."
    permission_level = PermissionLevel.LOW
    parameters_schema = {"type": "object", "properties": {}, "required": []}

    def validate(self, params: dict) -> tuple[bool, str]:
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        return ToolResult(success=False, verified=False, message="Falhou.", error="dispositivo indisponível")


def _plain(text: str) -> AIResponse:
    return AIResponse(content=text)


def _tool_call(name: str, arguments: dict | None = None, call_id: str = "call_1") -> AIResponse:
    return AIResponse(content="", tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments or {})])


def _wrap_in_router(ai_provider: AIProvider) -> AIRouter:
    """Test helper only: the real wiring lives in main.build_ai_router(). Wraps a fake
    provider in a single-provider Router so every existing orchestrator test keeps
    exercising the exact same fake-provider behavior it always has, now routed through
    the same abstraction the Orchestrator actually depends on in production."""
    router = AIRouter()
    router.register("fake", ai_provider)
    return router


def _build_orchestrator(
    ai_provider: AIProvider,
    tool_manager,
    context_manager,
    session,
    memory_service,
    settings,
    permission_manager,
    audit_logger,
    confirmation_service=None,
    max_tool_iterations: int = 5,
    event_bus=None,
):
    return Orchestrator(
        ai_router=_wrap_in_router(ai_provider),
        tool_manager=tool_manager,
        context_manager=context_manager,
        session=session,
        memory_service=memory_service,
        settings=settings,
        permission_manager=permission_manager,
        confirmation_service=confirmation_service or FakeConfirmationService(approve=True),
        audit_logger=audit_logger,
        max_tool_iterations=max_tool_iterations,
        event_bus=event_bus,
    )


def _build_streaming_orchestrator(
    streaming_provider,
    tool_manager,
    context_manager,
    session,
    memory_service,
    settings,
    permission_manager,
    audit_logger,
    confirmation_service=None,
    max_tool_iterations: int = 5,
):
    router = AIRouter()
    router.register("fake_streaming", streaming_provider)
    return Orchestrator(
        ai_router=router,
        tool_manager=tool_manager,
        context_manager=context_manager,
        session=session,
        memory_service=memory_service,
        settings=settings,
        permission_manager=permission_manager,
        confirmation_service=confirmation_service or FakeConfirmationService(approve=True),
        audit_logger=audit_logger,
        max_tool_iterations=max_tool_iterations,
    )


def test_handle_message_stream_yields_content_deltas_and_joins_to_the_full_reply(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    provider = FakeStreamingAIProvider(
        stream_rounds=[
            [StreamChunk(content="Olá"), StreamChunk(content=", tudo bem?"), StreamChunk(done=True)]
        ]
    )
    orchestrator = _build_streaming_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
    )

    chunks = list(orchestrator.handle_message_stream("Oi, Steve."))

    assert "".join(chunks) == "Olá, tudo bem?"
    assert [t.role for t in session.turns] == ["user", "assistant"]
    assert session.turns[-1].content == "Olá, tudo bem?"


def test_handle_message_stream_executes_a_real_tool_before_streaming_the_final_answer(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    """Mirrors test_ai_solicits_check_cpu_and_tool_manager_executes_it but for the
    streaming path: a tool-calling round must still go through the exact same
    ToolManager/PermissionManager/AuditLogger path as the non-streaming loop — nothing
    about tool safety changes just because the final answer is streamed."""
    provider = FakeStreamingAIProvider(
        stream_rounds=[
            [StreamChunk(tool_calls=[ToolCall(id="call_1", name="check_cpu", arguments={})]), StreamChunk(done=True)],
            [StreamChunk(content="A CPU está "), StreamChunk(content="tranquila."), StreamChunk(done=True)],
        ]
    )
    confirmations = FakeConfirmationService(approve=True)
    orchestrator = _build_streaming_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger,
        confirmation_service=confirmations,
    )

    reply = "".join(orchestrator.handle_message_stream("Como está o desempenho do PC?"))

    assert reply == "A CPU está tranquila."
    assert confirmations.asked == []  # LOW tool: no confirmation needed, same as non-streaming
    assert len(provider.stream_calls) == 2  # one round per tool_calls, one for the final answer


def test_handle_message_stream_reports_unavailable_message_on_connection_error(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    provider = FailingStreamingAIProvider()
    orchestrator = _build_streaming_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
    )

    reply = "".join(orchestrator.handle_message_stream("Oi?"))

    assert reply == UNAVAILABLE_MESSAGE
    assert session.turns[-1].content == UNAVAILABLE_MESSAGE


def test_handle_message_stream_falls_back_to_empty_response_message(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    provider = FakeStreamingAIProvider(stream_rounds=[[StreamChunk(content=""), StreamChunk(done=True)]])
    orchestrator = _build_streaming_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
    )

    reply = "".join(orchestrator.handle_message_stream("..."))

    assert reply == EMPTY_RESPONSE_MESSAGE


def test_handle_message_stream_stops_at_max_tool_iterations(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    tool_round = [StreamChunk(tool_calls=[ToolCall(id="c", name="check_cpu", arguments={})]), StreamChunk(done=True)]
    provider = FakeStreamingAIProvider(stream_rounds=[tool_round, tool_round])
    orchestrator = _build_streaming_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger,
        max_tool_iterations=2,
    )

    reply = "".join(orchestrator.handle_message_stream("Fica repetindo a ferramenta."))

    assert reply == TOOL_LIMIT_MESSAGE


def test_plain_conversation_needs_no_tools(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    provider = FakeAIProvider([_plain("Olá, tudo bem?")])
    orchestrator = _build_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
    )

    reply = orchestrator.handle_message("Oi, Steve.")

    assert reply == "Olá, tudo bem?"
    assert [t.role for t in session.turns] == ["user", "assistant"]
    assert len(memory_service.get_recent_history(session.session_id)) == 2
    assert len(provider.calls) == 1  # no tool requested -> single round trip


def test_arbitrary_plain_text_is_returned_verbatim(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    """Content that happens to look JSON-ish must NOT be parsed/mangled — it's just text now."""
    weird_text = '{"not": "a tool protocol"} still plain text'
    provider = FakeAIProvider([_plain(weird_text)])
    orchestrator = _build_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
    )

    reply = orchestrator.handle_message("Oi.")

    assert reply == weird_text


def test_blank_ai_reply_becomes_a_friendly_message(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    """PACK 1.1: an AI response with no tool_calls and empty/whitespace-only content
    used to flow straight through to the caller — voice/service.py already guarded
    against this before speaking it, but the text/GUI path had no equivalent, so a
    blank reply rendered as an empty chat bubble. Centralized in the Orchestrator so
    both entry points (text and voice) get a real message."""
    provider = FakeAIProvider([_plain("   ")])
    orchestrator = _build_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
    )

    reply = orchestrator.handle_message("Oi.")

    assert reply == EMPTY_RESPONSE_MESSAGE


def test_ai_solicits_check_cpu_and_tool_manager_executes_it(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    provider = FakeAIProvider(
        [_tool_call("check_cpu"), _plain("A CPU está tranquila.")]
    )
    confirmations = FakeConfirmationService(approve=True)
    orchestrator = _build_orchestrator(
        provider,
        tool_manager,
        context_manager,
        session,
        memory_service,
        settings,
        permission_manager,
        audit_logger,
        confirmation_service=confirmations,
    )

    reply = orchestrator.handle_message("Como está o desempenho do PC?")

    assert reply == "A CPU está tranquila."
    assert confirmations.asked == []  # LOW tool: no confirmation needed


def test_real_tool_result_is_fed_back_to_the_model(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    """The exact ToolResult produced by ToolManager.execute() must reach the follow-up call."""
    provider = FakeAIProvider([_tool_call("check_cpu"), _plain("Ok.")])
    orchestrator = _build_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
    )

    orchestrator.handle_message("Qual o uso de CPU?")

    second_call_messages, _tools = provider.calls[1]
    tool_messages = [m for m in second_call_messages if m.role == "tool"]
    assert len(tool_messages) == 1
    payload = json.loads(tool_messages[0].content)
    assert payload["success"] is True
    assert payload["verified"] is True
    assert "cpu_percent" in payload["data"]
    assert tool_messages[0].tool_name == "check_cpu"


def test_medium_permission_tool_requires_confirmation(
    context_manager, session, memory_service, settings, permission_manager, audit_logger, tool_manager
):
    tool_manager.register(_FakeMediumTool())
    provider = FakeAIProvider([_tool_call("fake_medium_tool"), _plain("Feito.")])
    confirmations = FakeConfirmationService(approve=True)
    orchestrator = _build_orchestrator(
        provider,
        tool_manager,
        context_manager,
        session,
        memory_service,
        settings,
        permission_manager,
        audit_logger,
        confirmation_service=confirmations,
    )

    reply = orchestrator.handle_message("Executa a ferramenta de teste.")

    assert reply == "Feito."
    assert len(confirmations.asked) == 1


def test_permission_blocks_execution_when_confirmation_denied(
    context_manager, session, memory_service, settings, permission_manager, audit_logger, tool_manager
):
    tool_manager.register(_FakeMediumTool())
    provider = FakeAIProvider(
        [_tool_call("fake_medium_tool"), _plain("Não fiz a ação porque você não permitiu.")]
    )
    confirmations = FakeConfirmationService(approve=False)
    orchestrator = _build_orchestrator(
        provider,
        tool_manager,
        context_manager,
        session,
        memory_service,
        settings,
        permission_manager,
        audit_logger,
        confirmation_service=confirmations,
    )

    orchestrator.handle_message("Executa a ferramenta de teste.")

    second_call_messages, _tools = provider.calls[1]
    tool_messages = [m for m in second_call_messages if m.role == "tool"]
    payload = json.loads(tool_messages[0].content)
    assert payload["success"] is False
    assert "negada" in payload["error"].lower()


def test_unknown_tool_is_rejected_without_crashing(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    provider = FakeAIProvider([_tool_call("does_not_exist"), _plain("Não consegui fazer isso.")])
    orchestrator = _build_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
    )

    orchestrator.handle_message("Faz algo estranho.")

    second_call_messages, _tools = provider.calls[1]
    tool_messages = [m for m in second_call_messages if m.role == "tool"]
    payload = json.loads(tool_messages[0].content)
    assert payload["success"] is False
    assert "desconhecida" in payload["error"].lower()


def test_invalid_parameters_are_rejected(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    from tools.filesystem import ListDirectoryTool

    tool_manager.register(ListDirectoryTool())
    provider = FakeAIProvider(
        [_tool_call("list_directory", arguments={}), _plain("Preciso do caminho para listar.")]
    )
    orchestrator = _build_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
    )

    orchestrator.handle_message("Lista meus arquivos.")

    second_call_messages, _tools = provider.calls[1]
    tool_messages = [m for m in second_call_messages if m.role == "tool"]
    payload = json.loads(tool_messages[0].content)
    assert payload["success"] is False
    assert "obrigatório" in payload["error"].lower()


def test_tool_failure_is_communicated_not_swallowed(
    context_manager, session, memory_service, settings, permission_manager, audit_logger, tool_manager
):
    tool_manager.register(_FakeFailingTool())
    provider = FakeAIProvider(
        [_tool_call("fake_failing_tool"), _plain("Não consegui obter esse dado agora.")]
    )
    orchestrator = _build_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
    )

    reply = orchestrator.handle_message("Faz a coisa que sempre falha.")

    second_call_messages, _tools = provider.calls[1]
    tool_messages = [m for m in second_call_messages if m.role == "tool"]
    payload = json.loads(tool_messages[0].content)
    assert payload["success"] is False
    assert payload["error"] == "dispositivo indisponível"
    assert reply == "Não consegui obter esse dado agora."


def test_tool_loop_stops_at_max_iterations(
    context_manager, session, memory_service, settings, permission_manager, audit_logger, tool_manager
):
    provider = FakeAIProvider([_tool_call("check_cpu") for _ in range(3)])
    orchestrator = _build_orchestrator(
        provider,
        tool_manager,
        context_manager,
        session,
        memory_service,
        settings,
        permission_manager,
        audit_logger,
        max_tool_iterations=3,
    )

    reply = orchestrator.handle_message("Fica pedindo a mesma ferramenta.")

    assert reply == TOOL_LIMIT_MESSAGE
    assert len(provider.calls) == 3


def test_ai_unavailable_returns_friendly_message(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    orchestrator = _build_orchestrator(
        FailingAIProvider(),
        tool_manager,
        context_manager,
        session,
        memory_service,
        settings,
        permission_manager,
        audit_logger,
    )

    reply = orchestrator.handle_message("Oi.")

    assert reply == UNAVAILABLE_MESSAGE


def test_ai_failure_mid_tool_loop_returns_friendly_message(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    class _FlakyProvider(AIProvider):
        def __init__(self):
            self.call_count = 0

        def is_available(self) -> bool:
            return True

        @property
        def capabilities(self):
            from ai.base import ProviderCapabilities

            return ProviderCapabilities(chat=True, tool_calling=True, local=True)

        def chat(self, messages, tools=None, **kwargs) -> AIResponse:
            self.call_count += 1
            if self.call_count == 1:
                return _tool_call("check_cpu")
            raise ConnectionError("simulated failure on follow-up")

    orchestrator = _build_orchestrator(
        _FlakyProvider(), tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
    )

    reply = orchestrator.handle_message("Como está a CPU?")

    assert reply == UNAVAILABLE_MESSAGE


def test_on_tool_call_hook_reports_tool_name_without_changing_behavior(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    """Optional observability hook for UIs (e.g. desktop app showing 'consultando
    sistema...'). Must not change what runs or the final reply — purely an event."""
    provider = FakeAIProvider([_tool_call("check_cpu"), _plain("Tudo certo.")])
    orchestrator = _build_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
    )
    seen: list[str] = []

    reply = orchestrator.handle_message("Como está a CPU?", on_tool_call=seen.append)

    assert reply == "Tudo certo."
    assert seen == ["check_cpu"]


def test_handle_message_passes_user_text_to_context_for_memory_relevance(
    context_manager, session, memory_service, settings, permission_manager, audit_logger, tool_manager
):
    """V1.2: the current user message must drive which memories get selected into the
    system prompt, not just recency/importance."""
    memory_service.remember("Prefere café pela manhã.", key="habito_cafe")
    memory_service.remember("O projeto principal é o Prime Ges.", key="projeto_atual")
    provider = FakeAIProvider([_plain("Ok.")])
    orchestrator = _build_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
    )

    orchestrator.handle_message("Qual é o meu projeto principal?")

    first_call_messages, _tools = provider.calls[0]
    system_content = first_call_messages[0].content
    assert system_content.index("Prime Ges") < system_content.index("café")


def test_remember_fact_tool_works_through_the_native_tool_calling_loop(
    context_manager, session, memory_service, settings, permission_manager, audit_logger, tool_manager
):
    """Memory tools must go through the exact same native tool-calling loop as every
    other tool — no separate/fragile text-command parser."""
    from tools.memory import RememberFactTool

    tool_manager.register(RememberFactTool(memory_service=memory_service))
    provider = FakeAIProvider(
        [
            _tool_call("remember_fact", arguments={"content": "O projeto principal é o Prime Ges.", "key": "projeto_atual"}),
            _plain("Combinado, vou lembrar disso."),
        ]
    )
    orchestrator = _build_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
    )

    reply = orchestrator.handle_message("Lembre que meu projeto principal é o Prime Ges.")

    assert reply == "Combinado, vou lembrar disso."
    stored = memory_service.get_active_by_key("projeto_atual")
    assert stored is not None
    assert stored.content == "O projeto principal é o Prime Ges."


def test_handle_message_without_on_tool_call_still_works(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    """Backward compatibility: omitting on_tool_call (as every pre-existing caller does)
    must behave exactly as before."""
    provider = FakeAIProvider([_plain("Oi!")])
    orchestrator = _build_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
    )

    assert orchestrator.handle_message("Oi") == "Oi!"


def test_orchestrator_runs_the_full_native_tool_calling_loop_through_the_ai_router(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    """V1.2.1 scenario 20: Orchestrator must depend on AIRouter, not a raw AIProvider —
    constructed directly here (not via the _wrap_in_router test helper) to make that
    explicit, exercising the same check_cpu native tool-calling flow end to end through
    the Router: AIRouter -> FakeAIProvider -> AIResponse -> ToolCall -> ToolManager."""
    router = AIRouter()
    provider = FakeAIProvider([_tool_call("check_cpu"), _plain("CPU sob controle, via o Router.")])
    router.register("ollama", provider)

    orchestrator = Orchestrator(
        ai_router=router,
        tool_manager=tool_manager,
        context_manager=context_manager,
        session=session,
        memory_service=memory_service,
        settings=settings,
        permission_manager=permission_manager,
        confirmation_service=FakeConfirmationService(approve=True),
        audit_logger=audit_logger,
    )

    reply = orchestrator.handle_message("Como está a CPU?")

    assert reply == "CPU sob controle, via o Router."
    assert orchestrator.ai_router is router
    assert router.last_decision.provider_name == "ollama"
    assert router.last_decision.fallback_used is False


# --- V1.4: Steve-activity events on the shared EventBus ---------------------------------


def test_orchestrator_without_event_bus_still_works(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    """Backward compatible: event_bus defaults to None, every pre-V1.4 caller (and every
    other test in this file) omits it and must behave exactly as before."""
    provider = FakeAIProvider([_plain("Oi!")])
    orchestrator = _build_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
    )
    assert orchestrator.handle_message("Oi") == "Oi!"


def test_orchestrator_publishes_ai_request_and_response_events(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    from core.events import EventBus
    from core.orchestrator import AI_REQUEST_STARTED, AI_RESPONSE_RECEIVED

    bus = EventBus()
    requests_seen = []
    responses_seen = []
    bus.subscribe(AI_REQUEST_STARTED, requests_seen.append)
    bus.subscribe(AI_RESPONSE_RECEIVED, responses_seen.append)

    provider = FakeAIProvider([_plain("Olá!")])
    orchestrator = _build_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger,
        event_bus=bus,
    )

    orchestrator.handle_message("Oi")

    assert len(requests_seen) == 1
    assert len(responses_seen) == 1
    assert responses_seen[0]["had_tool_calls"] is False


def test_orchestrator_publishes_tool_started_and_completed_events(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    from core.events import EventBus
    from core.orchestrator import TOOL_COMPLETED, TOOL_STARTED

    bus = EventBus()
    started_seen = []
    completed_seen = []
    bus.subscribe(TOOL_STARTED, started_seen.append)
    bus.subscribe(TOOL_COMPLETED, completed_seen.append)

    provider = FakeAIProvider([_tool_call("check_cpu"), _plain("Tudo certo.")])
    orchestrator = _build_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger,
        event_bus=bus,
    )

    orchestrator.handle_message("Como está a CPU?")

    assert started_seen == [{"tool": "check_cpu"}]
    assert completed_seen == [{"tool": "check_cpu", "success": True}]


def test_tool_completed_event_reports_failure(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    from core.events import EventBus
    from core.orchestrator import TOOL_COMPLETED

    bus = EventBus()
    completed_seen = []
    bus.subscribe(TOOL_COMPLETED, completed_seen.append)

    provider = FakeAIProvider([_tool_call("does_not_exist"), _plain("Não consegui.")])
    orchestrator = _build_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger,
        event_bus=bus,
    )

    orchestrator.handle_message("Faz algo estranho.")

    assert completed_seen == [{"tool": "does_not_exist", "success": False}]


def test_tool_events_never_carry_tool_result_content(
    tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    """Live Activity must never see raw tool output (paths, memory content, ...) — the
    event payload is deliberately limited to the tool name and a success flag."""
    from core.events import EventBus
    from core.orchestrator import TOOL_COMPLETED

    bus = EventBus()
    completed_seen = []
    bus.subscribe(TOOL_COMPLETED, completed_seen.append)

    provider = FakeAIProvider([_tool_call("check_cpu"), _plain("Tudo certo.")])
    orchestrator = _build_orchestrator(
        provider, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger,
        event_bus=bus,
    )

    orchestrator.handle_message("Como está a CPU?")

    assert set(completed_seen[0].keys()) == {"tool", "success"}
