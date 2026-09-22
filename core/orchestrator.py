"""The Orchestrator: understand -> plan -> permission -> execute -> verify -> respond.

Tool-calling uses the AIProvider's native mechanism (AIResponse.tool_calls), not text
parsing: the model never executes anything itself, it only ever requests a ToolCall by
name; this module is the only place that turns a request into a real execution, always
through ToolManager + PermissionManager. See docs/ARCHITECTURE.md for the full loop.

The Orchestrator talks to `ai_router: AIRouter` (ai/router.py), never to a concrete
provider like OllamaProvider directly — the Router picks/falls back between registered
providers and exposes the exact same chat()/is_available() shape AIProvider always had,
so nothing here needs to know or care which model actually answered.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Iterator

from ai.base import AIMessage, ToolCall
from ai.router import AIRouter
from core.context import ContextManager
from core.events import EventBus
from core.session import SessionManager
from core.tool_manager import ToolManager, UnknownToolError
from memory.service import MemoryService
from security.audit import AuditLogger
from security.confirmations import ConfirmationService
from security.permissions import PermissionManager
from settings.config import Settings
from learning.router import LearningRouter
from learning.store import LearningStore
from learning.auto_learn import auto_learn_from_turn

logger = logging.getLogger("steve.orchestrator")

UNAVAILABLE_MESSAGE = (
    "Não consegui acessar o modelo de IA agora. Verifique se o Ollama está em execução."
)

TOOL_LIMIT_MESSAGE = (
    "Não consegui concluir isso com segurança depois de várias tentativas com "
    "ferramentas. Pode tentar reformular o pedido?"
)

#: PACK 1.1: previously, an AI response with no tool_calls and blank/whitespace-only
#: content flowed straight through to the caller — voice/service.py already guarded
#: against this (EMPTY_REPLY_MESSAGE) before ever speaking it, but the text/GUI path
#: (ui/desktop/window.py::MainWindow._on_reply) had no equivalent check, so a blank
#: model reply rendered as an empty, unexplained chat bubble. Centralizing the guard
#: here means both callers (ChatController.send_text and VoiceService) get a real
#: message instead of silently propagating an empty string — voice's own check still
#: runs too (defense in depth), it just never has anything to catch anymore for this
#: specific case.
EMPTY_RESPONSE_MESSAGE = "Não consegui gerar uma resposta agora. Pode tentar reformular o pedido?"

DEFAULT_MAX_TOOL_ITERATIONS = 5

# Steve-activity events for Live Activity / a future System Dashboard (V1.4) — published
# on the same EventBus core/lifecycle.py already uses, never a second event system.
# Payloads are deliberately minimal: tool names and booleans only, never message
# content, tool results, or file paths — see docs/ARCHITECTURE.md's Live Activity
# sanitization rules.
AI_REQUEST_STARTED = "orchestrator.ai_request_started"
AI_RESPONSE_RECEIVED = "orchestrator.ai_response_received"
TOOL_STARTED = "orchestrator.tool_started"
TOOL_COMPLETED = "orchestrator.tool_completed"


class Orchestrator:
    def __init__(
        self,
        ai_router: AIRouter,
        tool_manager: ToolManager,
        context_manager: ContextManager,
        session: SessionManager,
        memory_service: MemoryService,
        settings: Settings,
        permission_manager: PermissionManager,
        confirmation_service: ConfirmationService,
        audit_logger: AuditLogger,
        max_tool_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS,
        event_bus: EventBus | None = None,
    ):
        self.ai_router = ai_router
        self.tool_manager = tool_manager
        self.context_manager = context_manager
        self.session = session
        self.memory_service = memory_service
        self.settings = settings
        self.permission_manager = permission_manager
        self.confirmation_service = confirmation_service
        self.audit_logger = audit_logger
        self.max_tool_iterations = max_tool_iterations
        self.event_bus = event_bus
        self.learning = LearningRouter(LearningStore())

    def _publish(self, event_name: str, data: dict | None = None) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(event_name, data or {})

    def handle_message(
        self, user_text: str, on_tool_call: Callable[[str], None] | None = None
    ) -> str:
        """`on_tool_call`, if given, is called with a tool's name right before it runs —
        purely an observability hook for UIs (e.g. showing "consultando sistema..."); it
        never influences what runs or whether it's allowed. Optional and backward
        compatible: omitting it changes nothing about the conversation flow."""
        self._remember_turn("user", user_text)

        routed = self.learning.route(user_text)
        if not routed.ready and routed.ask:
            reply = routed.ask
            self._remember_turn("assistant", reply)
            try:
                auto_learn_from_turn(self.learning.store, user_text, reply)
            except Exception:
                logger.debug("auto-learn falhou", exc_info=True)
            return reply

        effective = routed.rewritten or routed.normalized or user_text
        messages = self.context_manager.build(self.session, self.settings, user_text=effective)
        messages.append(AIMessage(role="user", content=effective))
        tool_specs = self.tool_manager.to_tool_specs()

        reply = self._run_tool_loop(messages, tool_specs, on_tool_call)

        self._remember_turn("assistant", reply)
        try:
            auto_learn_from_turn(self.learning.store, user_text, reply)
        except Exception:
            logger.debug("auto-learn falhou", exc_info=True)
        return reply

    def _run_tool_loop(
        self,
        messages: list[AIMessage],
        tool_specs: list,
        on_tool_call: Callable[[str], None] | None = None,
    ) -> str:
        for _ in range(self.max_tool_iterations):
            self._publish(AI_REQUEST_STARTED)
            try:
                response = self.ai_router.chat(messages, tools=tool_specs)
            except ConnectionError:
                logger.exception("Falha ao consultar o AI provider.")
                return UNAVAILABLE_MESSAGE
            self._publish(AI_RESPONSE_RECEIVED, {"had_tool_calls": bool(response.tool_calls)})

            if not response.tool_calls:
                return response.content if response.content and response.content.strip() else EMPTY_RESPONSE_MESSAGE

            messages.append(
                AIMessage(role="assistant", content=response.content, tool_calls=response.tool_calls)
            )
            for call in response.tool_calls:
                if on_tool_call:
                    on_tool_call(call.name)
                messages.append(self._execute_tool_call(call))

        logger.warning("Limite de %d iterações de ferramentas atingido.", self.max_tool_iterations)
        return TOOL_LIMIT_MESSAGE

    def handle_message_stream(
        self, user_text: str, on_tool_call: Callable[[str], None] | None = None
    ) -> Iterator[str]:
        """Streaming counterpart to handle_message(): yields the reply as text deltas
        instead of returning it all at once, so a caller (voice/streaming_speaker.py)
        can start speaking the first sentence before the model finishes generating the
        rest — see docs/ARCHITECTURE.md's streaming section for the full design and its
        known trade-off.

        Reuses everything handle_message()/_run_tool_loop() already do for tool rounds
        — _execute_tool_call() (permissions, confirmation, audit logging, ToolManager),
        context_manager.build(), _remember_turn(), the same EventBus events, the same
        max_tool_iterations cap — nothing about tool-calling safety changes here. The
        only difference is HOW the final (no-tool-calls) round is fetched: via
        ai_router.chat_stream() instead of one blocking ai_router.chat() call, yielding
        content deltas as they arrive instead of returning the complete string.

        Correctness note: content is only safe to yield/speak once a round is confirmed
        to have ended WITHOUT tool_calls. Ollama's tool-calling contract (mirroring
        OpenAI's) is that a turn never mixes meaningful prose with a tool_calls request
        — the same assumption _run_tool_loop() already relies on when it discards
        response.content on a tool round. Chunks are still yielded live as they stream
        in (not buffered until the round is known-final) for the latency win to mean
        anything; if that assumption is ever violated by a given model, the caller may
        hear/see a stray fragment before the real tool result and answer follow — a
        known, low-probability edge case, not a crash or wrong data. See PACK 1.1's
        streaming report for why this is opt-in (Settings.voice_streaming_enabled).
        """
        self._remember_turn("user", user_text)

        messages = self.context_manager.build(self.session, self.settings, user_text=user_text)
        messages.append(AIMessage(role="user", content=user_text))
        tool_specs = self.tool_manager.to_tool_specs()

        for _ in range(self.max_tool_iterations):
            self._publish(AI_REQUEST_STARTED)
            accumulated: list[str] = []
            final_tool_calls: list[ToolCall] = []
            try:
                for chunk in self.ai_router.chat_stream(messages, tools=tool_specs):
                    if chunk.content:
                        accumulated.append(chunk.content)
                        yield chunk.content
                    if chunk.tool_calls:
                        final_tool_calls = chunk.tool_calls
            except ConnectionError:
                logger.exception("Falha ao consultar o AI provider (stream).")
                yield UNAVAILABLE_MESSAGE
                self._remember_turn("assistant", UNAVAILABLE_MESSAGE)
                return

            self._publish(AI_RESPONSE_RECEIVED, {"had_tool_calls": bool(final_tool_calls)})
            full_content = "".join(accumulated)

            if not final_tool_calls:
                reply = full_content if full_content.strip() else EMPTY_RESPONSE_MESSAGE
                if not full_content.strip():
                    yield EMPTY_RESPONSE_MESSAGE
                self._remember_turn("assistant", reply)
                return

            messages.append(AIMessage(role="assistant", content=full_content, tool_calls=final_tool_calls))
            for call in final_tool_calls:
                if on_tool_call:
                    on_tool_call(call.name)
                messages.append(self._execute_tool_call(call))

        logger.warning("Limite de %d iterações de ferramentas atingido (stream).", self.max_tool_iterations)
        yield TOOL_LIMIT_MESSAGE
        self._remember_turn("assistant", TOOL_LIMIT_MESSAGE)

    def _execute_tool_call(self, call: ToolCall) -> AIMessage:
        self._publish(TOOL_STARTED, {"tool": call.name})
        message = self._do_execute_tool_call(call)
        payload = json.loads(message.content)
        self._publish(TOOL_COMPLETED, {"tool": call.name, "success": payload.get("success", False)})
        return message

    def _do_execute_tool_call(self, call: ToolCall) -> AIMessage:
        if not self.tool_manager.has(call.name):
            logger.warning("IA solicitou ferramenta desconhecida: %s", call.name)
            return self._tool_result_message(
                call.name, {"success": False, "verified": False, "error": f"Ferramenta desconhecida: {call.name}"}
            )

        tool = self.tool_manager.get(call.name)

        if self.permission_manager.requires_confirmation(tool.permission_level):
            question = f"Steve quer executar '{tool.name}' ({tool.description}). Permitir?"
            approved = self.confirmation_service.confirm(question)
            self.audit_logger.log_confirmation(question, approved)
            if not approved:
                return self._tool_result_message(
                    call.name, {"success": False, "verified": False, "error": "Ação negada pelo usuário."}
                )

        try:
            result = self.tool_manager.execute(call.name, call.arguments)
        except UnknownToolError as exc:
            return self._tool_result_message(
                call.name, {"success": False, "verified": False, "error": str(exc)}
            )

        self.audit_logger.log_tool_execution(
            call.name, call.arguments, result.success, result.verified, result.message
        )
        return self._tool_result_message(call.name, result.to_dict())

    @staticmethod
    def _tool_result_message(tool_name: str, payload: dict) -> AIMessage:
        return AIMessage(role="tool", content=json.dumps(payload, ensure_ascii=False), tool_name=tool_name)

    def _remember_turn(self, role: str, content: str) -> None:
        self.session.add_turn(role, content)
        self.memory_service.add_history(self.session.session_id, role, content)
