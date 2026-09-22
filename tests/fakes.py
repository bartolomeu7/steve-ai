"""Test doubles shared across the test suite."""
from __future__ import annotations

from typing import Iterator

from ai.base import AIMessage, AIProvider, AIResponse, ProviderCapabilities, StreamChunk, ToolSpec

#: Every existing test that uses FakeAIProvider is standing in for "a provider like
#: Ollama" — native tool-calling included, since Steve always advertises its registered
#: tools on every chat() call (whether or not the model ends up using one). Declaring
#: tool_calling=True here, not per-test, keeps every pre-existing FakeAIProvider(...)
#: call site working unchanged now that AIRouter.select() checks this capability.
_FAKE_PROVIDER_CAPABILITIES = ProviderCapabilities(chat=True, tool_calling=True, local=True)


class FakeAIProvider(AIProvider):
    """Returns a queued sequence of AIResponse objects, one per call to chat()."""

    def __init__(
        self,
        responses: list[AIResponse],
        available: bool = True,
        name: str = "fake",
        model: str = "fake-model",
        capabilities: ProviderCapabilities | None = None,
    ):
        self.responses = list(responses)
        self.available = available
        self.name = name
        self.model = model
        self._capabilities = capabilities or _FAKE_PROVIDER_CAPABILITIES
        self.calls: list[tuple[list[AIMessage], list[ToolSpec] | None]] = []

    def is_available(self) -> bool:
        return self.available

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def chat(self, messages: list[AIMessage], tools: list[ToolSpec] | None = None, **kwargs) -> AIResponse:
        self.calls.append((messages, tools))
        if not self.responses:
            raise AssertionError("FakeAIProvider ran out of queued responses.")
        return self.responses.pop(0)


_FAKE_STREAMING_CAPABILITIES = ProviderCapabilities(chat=True, tool_calling=True, streaming=True, local=True)


class FakeStreamingAIProvider(AIProvider):
    """Returns a queued sequence of "rounds" from chat_stream(), where each round is a
    list[StreamChunk] — standing in for a real Ollama streaming response's chunks for
    one turn. Also supports plain chat() (queued AIResponse) for tests that mix both."""

    name = "fake_streaming"
    model = "fake-streaming-model"

    def __init__(
        self,
        stream_rounds: list[list[StreamChunk]] | None = None,
        responses: list[AIResponse] | None = None,
        available: bool = True,
    ):
        self.stream_rounds = list(stream_rounds or [])
        self.responses = list(responses or [])
        self.available = available
        self.stream_calls: list[tuple[list[AIMessage], list[ToolSpec] | None]] = []

    def is_available(self) -> bool:
        return self.available

    @property
    def capabilities(self) -> ProviderCapabilities:
        return _FAKE_STREAMING_CAPABILITIES

    def chat(self, messages: list[AIMessage], tools: list[ToolSpec] | None = None, **kwargs) -> AIResponse:
        if not self.responses:
            raise AssertionError("FakeStreamingAIProvider ran out of queued chat() responses.")
        return self.responses.pop(0)

    def chat_stream(
        self, messages: list[AIMessage], tools: list[ToolSpec] | None = None, **kwargs
    ) -> Iterator[StreamChunk]:
        self.stream_calls.append((messages, tools))
        if not self.stream_rounds:
            raise AssertionError("FakeStreamingAIProvider ran out of queued stream rounds.")
        yield from self.stream_rounds.pop(0)


class FailingStreamingAIProvider(AIProvider):
    name = "failing_streaming"
    model = "failing-streaming-model"

    def is_available(self) -> bool:
        return False

    @property
    def capabilities(self) -> ProviderCapabilities:
        return _FAKE_STREAMING_CAPABILITIES

    def chat(self, messages: list[AIMessage], tools: list[ToolSpec] | None = None, **kwargs) -> AIResponse:
        raise ConnectionError("simulated failure")

    def chat_stream(
        self, messages: list[AIMessage], tools: list[ToolSpec] | None = None, **kwargs
    ) -> Iterator[StreamChunk]:
        raise ConnectionError("simulated streaming failure")
        yield  # pragma: no cover - makes this a generator function; never reached


class FailingAIProvider(AIProvider):
    name = "failing"
    model = "failing-model"

    def is_available(self) -> bool:
        return False

    @property
    def capabilities(self) -> ProviderCapabilities:
        # Declares the same capabilities as a real (working) provider on purpose: this
        # fake represents "an Ollama-like provider that happens to be unreachable right
        # now", not "a provider that doesn't support tool-calling" — its chat() raising
        # ConnectionError is what should be exercised, not a capability mismatch at
        # AIRouter.select() time.
        return _FAKE_PROVIDER_CAPABILITIES

    def chat(self, messages: list[AIMessage], tools: list[ToolSpec] | None = None, **kwargs) -> AIResponse:
        raise ConnectionError("simulated failure")
