"""AI Router — the seam between Steve and whichever AI provider actually answers.

Before this module, the flow was:

    Orchestrator -> OllamaProvider -> Ollama

Now it's:

    Orchestrator -> AIRouter -> AIProvider (OllamaProvider today) -> model

The Orchestrator only ever calls `AIRouter.chat(messages, tools=...)` — the exact same
signature `AIProvider.chat()` already had — so it never learns which concrete provider
served the call, and doesn't need to: no `if ollama`/`if claude` anywhere in Core. Adding
a second provider later (Claude, ...) means registering it here; nothing in Orchestrator,
ContextManager or the tool-calling loop changes. See docs/ARCHITECTURE.md.

Native tool-calling is untouched: the Router never inspects or rewrites an AIResponse —
it calls `provider.chat(messages, tools=tool_specs)` and returns exactly what came back
(same AIResponse/ToolCall/ToolSpec dataclasses from ai/base.py), so ToolCall keeps
flowing to the Orchestrator's existing tool loop unchanged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Iterator

from ai.base import AIMessage, AIProvider, AIResponse, ProviderCapabilities, StreamChunk, ToolSpec

logger = logging.getLogger("steve.ai.router")


class RoutingMode(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"


class RouterError(ConnectionError):
    """Base class for every AIRouter failure. Subclasses ConnectionError on purpose: the
    Orchestrator already catches ConnectionError around every provider.chat() call and
    replies with UNAVAILABLE_MESSAGE (core/orchestrator.py) — a Router-level failure (no
    provider registered, unknown provider name, every provider unreachable) should look
    exactly like "the AI is unavailable" to the rest of Steve, with no new error-handling
    path needed anywhere else."""


class NoProviderAvailableError(RouterError):
    """No registered provider could serve this call — either nothing is registered, or
    every candidate that satisfies the requested capabilities failed/is unreachable."""


class UnknownProviderError(RouterError):
    """A caller (settings, a direct provider_name override, tests) named a provider that
    isn't registered."""


@dataclass
class ProviderRegistration:
    name: str
    provider: AIProvider
    capabilities: ProviderCapabilities


@dataclass
class RouterDecision:
    """What the Router actually did for one chat() call — for logs/GUI/audit. Never
    carries conversation content, only provider/model identifiers and a short reason."""

    provider_name: str
    model: str
    reason: str
    fallback_used: bool = False
    fallback_from: str | None = None


class AIRouter(AIProvider):
    """Registers AIProviders and exposes a single AIProvider-shaped interface over all of
    them. Selection is intentionally simple for this stage — configuration-driven
    (AUTO: preferred provider, falling back through registration order by capability;
    MANUAL: exactly the configured provider) — not a second model deciding which model to
    use."""

    name = "ai_router"

    def __init__(
        self,
        mode: RoutingMode = RoutingMode.AUTO,
        preferred_provider: str | None = None,
        audit_logger=None,
    ):
        self.mode = mode
        self.preferred_provider = preferred_provider
        self._audit_logger = audit_logger
        self._providers: dict[str, ProviderRegistration] = {}
        self._order: list[str] = []  # registration order == fallback order
        self.last_decision: RouterDecision | None = None
        self.last_error: str | None = None

    # --- registration -----------------------------------------------------------------
    def register(self, name: str, provider: AIProvider, capabilities: ProviderCapabilities | None = None) -> None:
        """Registering a name that's already taken replaces that provider in place
        (fallback order/position preserved) — this is how a settings change ("use a
        different model") updates the Router without anyone needing to touch the
        Orchestrator or hand out a new Router instance."""
        is_new = name not in self._providers
        self._providers[name] = ProviderRegistration(
            name=name, provider=provider, capabilities=capabilities or provider.capabilities
        )
        if is_new:
            self._order.append(name)

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)
        if name in self._order:
            self._order.remove(name)

    def list_providers(self) -> list[str]:
        return list(self._order)

    def is_registered(self, name: str) -> bool:
        return name in self._providers

    def available_providers(self) -> list[str]:
        """Registered providers that report themselves reachable right now. Does a real
        availability check per provider (e.g. OllamaProvider hits /api/tags) — meant for
        startup/diagnostics/GUI, not the per-message hot path (chat() below never calls
        this, to avoid an extra network round trip on every turn)."""
        return [name for name in self._order if self._providers[name].provider.is_available()]

    # --- selection ----------------------------------------------------------------------
    def select(
        self,
        requires_tools: bool = False,
        requires_local: bool = False,
        requires_vision: bool = False,
        requires_streaming: bool = False,
        requires_reasoning: bool = False,
        requires_online: bool = False,
        provider_name: str | None = None,
    ) -> ProviderRegistration:
        """Pick a registered provider satisfying the given requirements — a pure,
        in-memory decision (no network calls; availability is only checked when chat()
        actually tries to talk to the chosen provider). Doesn't yet support a classifier
        or multi-factor scoring — that's explicitly out of scope for this stage."""
        if not self._providers:
            raise NoProviderAvailableError("Nenhum provider de IA está registrado no Router.")

        if provider_name is not None:
            return self._get_or_raise(provider_name)

        if self.mode == RoutingMode.MANUAL:
            if not self.preferred_provider:
                raise NoProviderAvailableError(
                    "Modo de roteamento MANUAL configurado, mas nenhum provider preferido foi definido."
                )
            return self._get_or_raise(self.preferred_provider)

        ordered_names = [self.preferred_provider] if self.preferred_provider in self._providers else []
        ordered_names += [n for n in self._order if n not in ordered_names]

        for name in ordered_names:
            registration = self._providers[name]
            if self._satisfies(
                registration.capabilities,
                requires_tools=requires_tools,
                requires_local=requires_local,
                requires_vision=requires_vision,
                requires_streaming=requires_streaming,
                requires_reasoning=requires_reasoning,
                requires_online=requires_online,
            ):
                return registration

        raise NoProviderAvailableError("Nenhum provider registrado satisfaz os requisitos pedidos.")

    def _get_or_raise(self, name: str) -> ProviderRegistration:
        if name not in self._providers:
            raise UnknownProviderError(f"Provider '{name}' não está registrado no Router.")
        return self._providers[name]

    @staticmethod
    def _satisfies(
        caps: ProviderCapabilities,
        requires_tools: bool,
        requires_local: bool,
        requires_vision: bool,
        requires_streaming: bool,
        requires_reasoning: bool,
        requires_online: bool,
    ) -> bool:
        if requires_tools and not caps.tool_calling:
            return False
        if requires_local and not caps.local:
            return False
        if requires_vision and not caps.vision:
            return False
        if requires_streaming and not caps.streaming:
            return False
        if requires_reasoning and not caps.reasoning:
            return False
        if requires_online and not caps.online:
            return False
        return True

    # --- AIProvider interface ---------------------------------------------------------
    def chat(self, messages: list[AIMessage], tools: list[ToolSpec] | None = None, **kwargs) -> AIResponse:
        """Selects a provider, then tries it and — only if there's more than one
        registered provider — falls through the rest in registration order on failure.
        With only Ollama registered (this version), the fallback list has exactly one
        entry, so this behaves identically to calling OllamaProvider.chat() directly:
        one HTTP call, no duplicated requests, no tool ever executed twice."""
        primary = self.select(requires_tools=bool(tools))
        fallback_order = [primary.name] + [n for n in self._order if n != primary.name]

        last_error: Exception | None = None
        for attempt_index, name in enumerate(fallback_order):
            registration = self._providers[name]
            try:
                response = registration.provider.chat(messages, tools=tools, **kwargs)
            except ConnectionError as exc:
                last_error = exc
                logger.warning("Provider '%s' falhou, tentando o próximo se houver: %s", name, exc)
                self._audit("ai_routing_error", provider=name, model=registration.provider.model, error=str(exc))
                continue

            fallback_used = attempt_index > 0
            self.last_decision = RouterDecision(
                provider_name=name,
                model=registration.provider.model,
                reason="fallback após falha do provider preferido" if fallback_used else "seleção normal",
                fallback_used=fallback_used,
                fallback_from=fallback_order[0] if fallback_used else None,
            )
            self.last_error = None
            self._audit(
                "ai_routing_decision",
                provider=name,
                model=registration.provider.model,
                reason=self.last_decision.reason,
                fallback_used=fallback_used,
            )
            return response

        self.last_error = str(last_error) if last_error else "nenhum provider disponível"
        raise NoProviderAvailableError(
            f"Nenhum provider de IA respondeu. Último erro: {self.last_error}"
        ) from last_error

    def chat_stream(
        self, messages: list[AIMessage], tools: list[ToolSpec] | None = None, **kwargs
    ) -> Iterator[StreamChunk]:
        """Streaming counterpart to chat() — deliberately simpler: picks one provider
        that declares `streaming=True` and streams straight from it, with no
        mid-stream fallback to a second provider on failure (unlike chat(), which can
        safely retry a whole non-streamed call on another provider). Retrying a
        streaming call is a different, harder problem — the caller may have already
        acted on (e.g. spoken) part of the first provider's partial output — and with
        only Ollama registered today there's nothing to fall back to anyway. A
        mid-stream ConnectionError propagates to the caller exactly like chat()'s
        would, so core/orchestrator.py's existing UNAVAILABLE_MESSAGE handling covers
        it without any new error-handling path."""
        registration = self.select(requires_tools=bool(tools), requires_streaming=True)
        self.last_decision = RouterDecision(
            provider_name=registration.name,
            model=registration.provider.model,
            reason="seleção normal (stream)",
        )
        yield from registration.provider.chat_stream(messages, tools=tools, **kwargs)

    def is_available(self) -> bool:
        try:
            registration = self.select()
        except RouterError:
            return False
        return registration.provider.is_available()

    @property
    def model(self) -> str:
        if self.last_decision is not None:
            return self.last_decision.model
        try:
            return self.select().provider.model
        except RouterError:
            return ""

    @property
    def capabilities(self) -> ProviderCapabilities:
        try:
            return self.select().capabilities
        except RouterError:
            return ProviderCapabilities()

    # --- observability ------------------------------------------------------------------
    def _audit(self, event_type: str, **details) -> None:
        if self._audit_logger is None:
            return
        self._audit_logger.log(event_type, details)
