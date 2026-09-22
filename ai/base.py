"""Provider-agnostic contract for talking to a language model. Core never imports Ollama directly.

Tool-calling is part of this contract too: a provider that supports it natively (like
Ollama) populates AIResponse.tool_calls with structured ToolCall objects; a provider
that doesn't can simply always return an empty list and only ever hold plain
conversations. Either way, the Orchestrator only ever sees ToolCall — never a specific
wire format — so it stays provider-agnostic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class ToolCall:
    """A structured request from the model to run one tool, with parsed arguments."""

    id: str
    name: str
    arguments: dict


@dataclass
class ToolSpec:
    """Provider-agnostic description of a tool, ready to be advertised to a model."""

    name: str
    description: str
    parameters_schema: dict


@dataclass
class AIMessage:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str = ""
    tool_calls: list[ToolCall] | None = None  # set on an assistant message that requested tools
    tool_name: str | None = None  # set on a "tool" message: which tool produced this result


@dataclass
class AIResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict | None = None


@dataclass
class StreamChunk:
    """One incremental piece of a streamed chat() call. `content` is a text delta to
    append (never the full text so far) — most chunks carry only this. `tool_calls` is
    only ever populated on the terminal chunk of a round that decided to call a tool
    instead of speaking, mirroring the same assumption core/orchestrator.py's
    non-streaming _run_tool_loop already relies on (a turn's `content` is treated as
    irrelevant once `tool_calls` is present) — see
    Orchestrator.handle_message_stream in core/orchestrator.py."""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    done: bool = False


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider/model actually supports — used by AIRouter (ai/router.py) to pick
    a provider that satisfies what a call needs, without the caller (Orchestrator) ever
    knowing which concrete provider it got. A provider must only declare what its own
    implementation genuinely supports; declaring something unimplemented would let the
    Router silently route a request somewhere it can't actually be served."""

    chat: bool = True
    tool_calling: bool = False
    streaming: bool = False
    vision: bool = False
    reasoning: bool = False
    max_context: int | None = None
    local: bool = True
    online: bool = False


class AIProvider(ABC):
    #: Human-readable identifier (e.g. "ollama") — used by AIRouter for logs/selection.
    #: Not abstract: a provider that doesn't override this still works everywhere an
    #: AIProvider is expected, it just shows up as "unknown".
    name: str = "unknown"
    #: Which underlying model this instance talks to, if applicable.
    model: str = ""

    @abstractmethod
    def chat(self, messages: list[AIMessage], tools: list[ToolSpec] | None = None, **kwargs) -> AIResponse:
        """Send a conversation (optionally advertising tools) and return the model's reply."""

    def chat_stream(
        self, messages: list[AIMessage], tools: list[ToolSpec] | None = None, **kwargs
    ) -> Iterator[StreamChunk]:
        """Like chat(), but yields the reply incrementally. Not abstract: a provider
        that doesn't implement real streaming simply doesn't override this, and must
        declare `capabilities.streaming = False` so AIRouter.select(requires_streaming=
        True) never routes here — calling this default raises rather than silently
        returning nothing, so a routing mistake fails loudly instead of hanging."""
        raise NotImplementedError(f"{self.name} não implementa chat_stream().")

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the provider can currently be reached."""

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Conservative default (chat only, no tool-calling) so a provider that doesn't
        override this doesn't accidentally get selected for something it can't do."""
        return ProviderCapabilities()
