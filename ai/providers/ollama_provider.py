"""AIProvider implementation backed by a local Ollama server, using its native
tool-calling mechanism (POST /api/chat with a "tools" field; the model responds with
message.tool_calls instead of us parsing free text). See docs/ARCHITECTURE.md."""
from __future__ import annotations

import json
import logging
from typing import Iterator

import requests

from ai.base import AIMessage, AIProvider, AIResponse, ProviderCapabilities, StreamChunk, ToolCall, ToolSpec

logger = logging.getLogger("steve.ai.ollama")


class OllamaProvider(AIProvider):
    name = "ollama"

    def __init__(
        self,
        model: str,
        host: str = "http://localhost:11434",
        timeout: float = 60.0,
        keep_alive: str = "24h",
    ):
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout
        #: Sent as Ollama's own "keep_alive" request field so the model stays loaded in
        #: memory between turns — Ollama's default (5 minutes of inactivity) unloads it,
        #: costing several seconds of reload latency on the next request. A per-request
        #: field, not an environment variable: no server restart needed, safe to change
        #: any time. See docs/ARCHITECTURE.md's Ollama latency section.
        self.keep_alive = keep_alive

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Only what this implementation actually does today: native tool-calling and
        streaming are both implemented and tested (see docs/ARCHITECTURE.md);
        vision/reasoning aren't implemented; max_context isn't tracked per model, so
        it's left unknown rather than guessed."""
        return ProviderCapabilities(
            chat=True,
            tool_calling=True,
            streaming=True,
            vision=False,
            reasoning=False,
            max_context=None,
            local=True,
            online=False,
        )

    def is_available(self) -> bool:
        try:
            response = requests.get(f"{self.host}/api/tags", timeout=3)
        except requests.RequestException as exc:
            logger.warning("Ollama indisponível em %s: %s", self.host, exc)
            return False

        if response.status_code != 200:
            return False

        if not self._model_is_installed(response):
            logger.warning(
                "Ollama está em execução em %s, mas o modelo configurado '%s' não está "
                "instalado (rode 'ollama pull %s').",
                self.host,
                self.model,
                self.model,
            )
            return False

        return True

    def _model_is_installed(self, tags_response: requests.Response) -> bool:
        try:
            installed_names = {m.get("name", "") for m in tags_response.json().get("models", [])}
        except ValueError:
            # /api/tags didn't return valid JSON; don't block availability on that alone.
            return True
        base_name = self.model.split(":")[0]
        return any(name == self.model or name.split(":")[0] == base_name for name in installed_names)

    def _base_payload(self, messages: list[AIMessage], tools: list[ToolSpec] | None, stream: bool, **kwargs) -> dict:
        payload = {
            "model": kwargs.get("model", self.model),
            "messages": [self._to_wire_message(m) for m in messages],
            "stream": stream,
            "keep_alive": kwargs.get("keep_alive", self.keep_alive),
            "options": {"temperature": kwargs.get("temperature", 0.7)},
        }
        if tools:
            payload["tools"] = [self._to_wire_tool(spec) for spec in tools]
        return payload

    def chat(self, messages: list[AIMessage], tools: list[ToolSpec] | None = None, **kwargs) -> AIResponse:
        payload = self._base_payload(messages, tools, stream=False, **kwargs)

        try:
            response = requests.post(f"{self.host}/api/chat", json=payload, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            detail = self._describe_error(exc)
            logger.error("Falha ao consultar Ollama: %s", detail)
            raise ConnectionError(f"Falha ao consultar Ollama em {self.host}: {detail}") from exc

        data = response.json()
        message = data.get("message", {})
        content = message.get("content") or ""
        if not content.strip():
            # PACK 1.1: reasoning/thinking-capable models (not llama3.2, the current
            # default, but a real behavior confirmed in Ollama's own issue tracker for
            # models like deepseek-r1) can put their real output in a separate
            # "thinking" field and leave "content" empty. Falling back to it here means
            # swapping to such a model later doesn't silently produce blank replies —
            # core/orchestrator.py's EMPTY_RESPONSE_MESSAGE is still the final safety
            # net if even this comes back empty.
            content = message.get("thinking") or ""
        tool_calls = [self._from_wire_tool_call(tc) for tc in message.get("tool_calls") or []]
        return AIResponse(content=content, tool_calls=tool_calls, raw=data)

    def chat_stream(
        self, messages: list[AIMessage], tools: list[ToolSpec] | None = None, **kwargs
    ) -> Iterator[StreamChunk]:
        """Streams Ollama's NDJSON response line by line. Each line is one JSON object
        shaped like chat()'s single response, but partial — `message.content` is a
        delta (one or a few tokens), not the accumulated text. Ollama's own tool-calling
        contract (mirroring OpenAI's) is that `tool_calls` only ever appears once, fully
        formed, without meaningful prose alongside it in the same turn — the same
        assumption core/orchestrator.py's non-streaming loop already relies on when it
        discards `response.content` on a tool-calling response. Callers must not treat a
        yielded chunk's `content` as safe to show/speak until they know the round didn't
        end in tool_calls (see Orchestrator.handle_message_stream)."""
        payload = self._base_payload(messages, tools, stream=True, **kwargs)

        try:
            response = requests.post(f"{self.host}/api/chat", json=payload, timeout=self.timeout, stream=True)
            response.raise_for_status()
        except requests.RequestException as exc:
            detail = self._describe_error(exc)
            logger.error("Falha ao consultar Ollama (stream): %s", detail)
            raise ConnectionError(f"Falha ao consultar Ollama em {self.host}: {detail}") from exc

        try:
            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except (ValueError, TypeError):
                    logger.warning("Linha não-JSON ignorada no stream do Ollama: %r", line[:200])
                    continue

                message = data.get("message", {})
                content = message.get("content") or message.get("thinking") or ""
                raw_tool_calls = message.get("tool_calls") or []
                tool_calls = [self._from_wire_tool_call(tc) for tc in raw_tool_calls]
                done = bool(data.get("done", False))
                if content or tool_calls or done:
                    yield StreamChunk(content=content, tool_calls=tool_calls, done=done)
        except requests.RequestException as exc:
            logger.error("Conexão com o Ollama caiu durante o streaming: %s", exc)
            raise ConnectionError(f"Conexão com o Ollama caiu durante o streaming: {exc}") from exc
        finally:
            response.close()

    @staticmethod
    def _describe_error(exc: requests.RequestException) -> str:
        """Pulls Ollama's own JSON error detail (e.g. "model 'x' not found") out of an
        HTTP error response, when there is one, so logs show the real reason instead of
        just a generic status code."""
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                server_error = response.json().get("error")
            except ValueError:
                server_error = None
            if server_error:
                return f"{exc} — {server_error}"
        return str(exc)

    @staticmethod
    def _to_wire_message(message: AIMessage) -> dict:
        wire: dict = {"role": message.role, "content": message.content}
        if message.tool_calls:
            wire["tool_calls"] = [
                {"id": call.id, "function": {"name": call.name, "arguments": call.arguments}}
                for call in message.tool_calls
            ]
        if message.role == "tool" and message.tool_name:
            wire["name"] = message.tool_name
        return wire

    @staticmethod
    def _to_wire_tool(spec: ToolSpec) -> dict:
        return {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters_schema,
            },
        }

    @staticmethod
    def _from_wire_tool_call(raw_call: dict) -> ToolCall:
        function = raw_call.get("function", {})
        return ToolCall(
            id=raw_call.get("id", ""),
            name=function.get("name", ""),
            arguments=function.get("arguments") or {},
        )
