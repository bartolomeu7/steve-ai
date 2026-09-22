from __future__ import annotations

import requests

from ai.base import AIMessage, ToolCall, ToolSpec
from ai.providers.ollama_provider import OllamaProvider


def test_ollama_provider_declares_name_and_model():
    """Real, non-mocked: these are plain attributes, no network involved."""
    provider = OllamaProvider(model="llama3.2")
    assert provider.name == "ollama"
    assert provider.model == "llama3.2"


def test_ollama_provider_declares_only_actually_implemented_capabilities():
    """Real, non-mocked: chat, native tool-calling and streaming (chat_stream) are all
    implemented and tested; vision/reasoning aren't implemented. Must not claim more
    than the implementation actually does."""
    capabilities = OllamaProvider(model="llama3.2").capabilities
    assert capabilities.chat is True
    assert capabilities.tool_calling is True
    assert capabilities.local is True
    assert capabilities.streaming is True
    assert capabilities.vision is False
    assert capabilities.reasoning is False
    assert capabilities.online is False


def test_is_available_false_when_unreachable(monkeypatch):
    def fake_get(*args, **kwargs):
        raise requests.ConnectionError("no server")

    monkeypatch.setattr("ai.providers.ollama_provider.requests.get", fake_get)

    provider = OllamaProvider(model="llama3.2", host="http://localhost:11434")
    assert provider.is_available() is False


def test_is_available_true_on_200_when_model_is_installed(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {"models": [{"name": "llama3.2:latest"}]}

    monkeypatch.setattr("ai.providers.ollama_provider.requests.get", lambda *a, **k: FakeResponse())

    provider = OllamaProvider(model="llama3.2")
    assert provider.is_available() is True


def test_is_available_false_when_model_is_not_installed(monkeypatch):
    """Regression test: Ollama can be perfectly reachable while the configured model
    doesn't exist (e.g. a typo saved in settings) — is_available() must catch that
    instead of reporting the provider as usable and only failing later, on chat()."""

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"models": [{"name": "llama3.2:latest"}, {"name": "qwen2.5:latest"}]}

    monkeypatch.setattr("ai.providers.ollama_provider.requests.get", lambda *a, **k: FakeResponse())

    provider = OllamaProvider(model="SIM")
    assert provider.is_available() is False


def test_chat_raises_connection_error_when_ollama_unreachable(monkeypatch):
    def fake_post(*args, **kwargs):
        raise requests.ConnectionError("no server")

    monkeypatch.setattr("ai.providers.ollama_provider.requests.post", fake_post)

    provider = OllamaProvider(model="llama3.2")
    try:
        provider.chat([AIMessage(role="user", content="oi")])
        assert False, "should have raised"
    except ConnectionError:
        pass


def test_chat_error_message_includes_ollama_detail_for_unknown_model(monkeypatch):
    """Regression test for the reported bug: OllamaProvider.chat() got a 404 while a
    manual `requests.post(...)` with model='llama3.2' returned 200. Root cause was a
    bad model name in settings (saved as 'SIM'), not a URL/payload defect — Ollama
    itself replies 404 with a JSON body naming the missing model. This must show up in
    the raised ConnectionError's message, not just a bare HTTP status.

    Fails on the old implementation (message was just "404 Client Error: Not Found for
    url: ...", no mention of which model or why) and passes on the fixed one.
    """

    class FakeErrorResponse:
        status_code = 404

        def json(self):
            return {"error": "model 'SIM' not found"}

        def raise_for_status(self):
            error = requests.HTTPError("404 Client Error: Not Found for url: http://localhost:11434/api/chat")
            error.response = self
            raise error

    monkeypatch.setattr("ai.providers.ollama_provider.requests.post", lambda *a, **k: FakeErrorResponse())

    provider = OllamaProvider(model="SIM")
    try:
        provider.chat([AIMessage(role="user", content="oi")])
        assert False, "should have raised ConnectionError"
    except ConnectionError as exc:
        message = str(exc)
        assert "not found" in message
        assert "SIM" in message


def test_chat_returns_plain_message_content(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"role": "assistant", "content": "Olá! Sou o Steve."}}

    monkeypatch.setattr("ai.providers.ollama_provider.requests.post", lambda *a, **k: FakeResponse())

    provider = OllamaProvider(model="llama3.2")
    response = provider.chat([AIMessage(role="user", content="oi")])

    assert response.content == "Olá! Sou o Steve."
    assert response.tool_calls == []


def test_chat_falls_back_to_thinking_field_when_content_is_empty(monkeypatch):
    """PACK 1.1: some Ollama-served reasoning models (not llama3.2, the current
    default, but a real behavior confirmed in Ollama's own issue tracker for models
    like deepseek-r1) put their real output in a separate 'thinking' field and leave
    'content' empty. Swapping to such a model later must not silently produce blank
    replies."""

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"role": "assistant", "content": "", "thinking": "A resposta real está aqui."}}

    monkeypatch.setattr("ai.providers.ollama_provider.requests.post", lambda *a, **k: FakeResponse())

    provider = OllamaProvider(model="a-reasoning-model")
    response = provider.chat([AIMessage(role="user", content="oi")])

    assert response.content == "A resposta real está aqui."


def test_chat_content_takes_priority_over_thinking_when_both_present(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"role": "assistant", "content": "Resposta normal.", "thinking": "raciocínio interno"}}

    monkeypatch.setattr("ai.providers.ollama_provider.requests.post", lambda *a, **k: FakeResponse())

    provider = OllamaProvider(model="llama3.2")
    response = provider.chat([AIMessage(role="user", content="oi")])

    assert response.content == "Resposta normal."


def test_chat_parses_native_tool_calls(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_abc123",
                            "function": {"name": "check_cpu", "arguments": {}},
                        }
                    ],
                }
            }

    monkeypatch.setattr("ai.providers.ollama_provider.requests.post", lambda *a, **k: FakeResponse())

    provider = OllamaProvider(model="llama3.2")
    response = provider.chat([AIMessage(role="user", content="uso de cpu?")])

    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert call.id == "call_abc123"
    assert call.name == "check_cpu"
    assert call.arguments == {}


def test_chat_sends_tools_in_native_format(monkeypatch):
    captured_payload = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"role": "assistant", "content": "ok"}}

    def fake_post(url, json, timeout):
        captured_payload.update(json)
        return FakeResponse()

    monkeypatch.setattr("ai.providers.ollama_provider.requests.post", fake_post)

    tool_specs = [
        ToolSpec(
            name="check_cpu",
            description="Consulta o uso atual de CPU.",
            parameters_schema={"type": "object", "properties": {}, "required": []},
        )
    ]
    provider = OllamaProvider(model="llama3.2")
    provider.chat([AIMessage(role="user", content="oi")], tools=tool_specs)

    assert captured_payload["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "check_cpu",
                "description": "Consulta o uso atual de CPU.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }
    ]


def test_chat_omits_tools_field_when_none_given(monkeypatch):
    captured_payload = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"role": "assistant", "content": "ok"}}

    def fake_post(url, json, timeout):
        captured_payload.update(json)
        return FakeResponse()

    monkeypatch.setattr("ai.providers.ollama_provider.requests.post", fake_post)

    provider = OllamaProvider(model="llama3.2")
    provider.chat([AIMessage(role="user", content="oi")])

    assert "tools" not in captured_payload


def test_chat_sends_keep_alive_so_ollama_keeps_the_model_loaded(monkeypatch):
    captured_payload = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"role": "assistant", "content": "ok"}}

    def fake_post(url, json, timeout):
        captured_payload.update(json)
        return FakeResponse()

    monkeypatch.setattr("ai.providers.ollama_provider.requests.post", fake_post)

    provider = OllamaProvider(model="llama3.2", keep_alive="1h")
    provider.chat([AIMessage(role="user", content="oi")])

    assert captured_payload["keep_alive"] == "1h"


class _FakeStreamingResponse:
    """Minimal stand-in for requests.Response(stream=True): iter_lines() over NDJSON."""

    status_code = 200

    def __init__(self, lines: list[bytes]):
        self._lines = lines

    def raise_for_status(self):
        return None

    def iter_lines(self):
        return iter(self._lines)

    def close(self):
        pass


def test_chat_stream_yields_content_deltas_as_they_arrive(monkeypatch):
    lines = [
        b'{"message": {"role": "assistant", "content": "Ol"}, "done": false}',
        b'{"message": {"role": "assistant", "content": "\xc3\xa1!"}, "done": false}',
        b'{"message": {"role": "assistant", "content": ""}, "done": true}',
    ]
    monkeypatch.setattr(
        "ai.providers.ollama_provider.requests.post",
        lambda *a, **k: _FakeStreamingResponse(lines),
    )

    provider = OllamaProvider(model="llama3.2")
    chunks = list(provider.chat_stream([AIMessage(role="user", content="oi")]))

    assert "".join(c.content for c in chunks) == "Olá!"
    assert chunks[-1].done is True


def test_chat_stream_yields_tool_calls_on_the_terminal_chunk(monkeypatch):
    lines = [
        b'{"message": {"role": "assistant", "content": "", "tool_calls": '
        b'[{"id": "call_1", "function": {"name": "check_cpu", "arguments": {}}}]}, "done": true}',
    ]
    monkeypatch.setattr(
        "ai.providers.ollama_provider.requests.post",
        lambda *a, **k: _FakeStreamingResponse(lines),
    )

    provider = OllamaProvider(model="llama3.2")
    chunks = list(provider.chat_stream([AIMessage(role="user", content="uso de cpu?")]))

    assert len(chunks) == 1
    assert chunks[0].tool_calls[0].name == "check_cpu"


def test_chat_stream_requests_streaming_and_keep_alive_in_the_payload(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout, stream):
        captured.update(json)
        captured["stream_kwarg"] = stream
        return _FakeStreamingResponse([b'{"message": {"content": ""}, "done": true}'])

    monkeypatch.setattr("ai.providers.ollama_provider.requests.post", fake_post)

    provider = OllamaProvider(model="llama3.2", keep_alive="30m")
    list(provider.chat_stream([AIMessage(role="user", content="oi")]))

    assert captured["stream"] is True
    assert captured["stream_kwarg"] is True
    assert captured["keep_alive"] == "30m"


def test_chat_stream_raises_connection_error_when_ollama_unreachable(monkeypatch):
    def fake_post(*args, **kwargs):
        raise requests.ConnectionError("no server")

    monkeypatch.setattr("ai.providers.ollama_provider.requests.post", fake_post)

    provider = OllamaProvider(model="llama3.2")
    try:
        list(provider.chat_stream([AIMessage(role="user", content="oi")]))
        assert False, "should have raised"
    except ConnectionError:
        pass


def test_chat_serializes_tool_result_message_with_name(monkeypatch):
    captured_payload = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"role": "assistant", "content": "ok"}}

    def fake_post(url, json, timeout):
        captured_payload.update(json)
        return FakeResponse()

    monkeypatch.setattr("ai.providers.ollama_provider.requests.post", fake_post)

    provider = OllamaProvider(model="llama3.2")
    messages = [
        AIMessage(role="user", content="qual o uso de cpu?"),
        AIMessage(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="call_1", name="check_cpu", arguments={})],
        ),
        AIMessage(role="tool", content='{"success": true, "data": {"cpu_percent": 42.0}}', tool_name="check_cpu"),
    ]
    provider.chat(messages)

    wire_messages = captured_payload["messages"]
    assert wire_messages[1]["tool_calls"] == [{"id": "call_1", "function": {"name": "check_cpu", "arguments": {}}}]
    assert wire_messages[2]["role"] == "tool"
    assert wire_messages[2]["name"] == "check_cpu"
