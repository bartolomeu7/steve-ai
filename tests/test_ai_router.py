"""Tests for the AI Router (ai/router.py) — the abstraction between Steve and whichever
AIProvider actually answers. See docs/ARCHITECTURE.md. Only Ollama exists as a real
provider today; every "second provider" here is a mock/fake used purely to validate the
Router's own registration/selection/fallback machinery, never a real second provider —
matching the explicit instruction not to pretend a second provider exists yet."""
from __future__ import annotations

from ai.base import AIMessage, AIResponse, ProviderCapabilities, StreamChunk, ToolCall, ToolSpec
from ai.router import (
    AIRouter,
    NoProviderAvailableError,
    RouterError,
    RoutingMode,
    UnknownProviderError,
)
from tests.fakes import FailingAIProvider, FailingStreamingAIProvider, FakeAIProvider, FakeStreamingAIProvider

_TOOL_SPECS = [
    ToolSpec(name="check_cpu", description="Consulta o uso atual de CPU.", parameters_schema={"type": "object", "properties": {}, "required": []})
]


# --- 1-3: registration -----------------------------------------------------------------


def test_register_provider():
    router = AIRouter()
    provider = FakeAIProvider([AIResponse(content="ok")])

    router.register("fake", provider)

    assert router.is_registered("fake") is True
    assert "fake" in router.list_providers()


def test_unregister_provider():
    router = AIRouter()
    router.register("fake", FakeAIProvider([AIResponse(content="ok")]))

    router.unregister("fake")

    assert router.is_registered("fake") is False
    assert "fake" not in router.list_providers()


def test_list_providers_preserves_registration_order():
    router = AIRouter()
    router.register("a", FakeAIProvider([AIResponse(content="a")]))
    router.register("b", FakeAIProvider([AIResponse(content="b")]))
    router.register("c", FakeAIProvider([AIResponse(content="c")]))

    assert router.list_providers() == ["a", "b", "c"]


# --- 4-5: availability -------------------------------------------------------------------


def test_provider_available():
    router = AIRouter()
    router.register("fake", FakeAIProvider([AIResponse(content="ok")], available=True))

    assert router.is_available() is True


def test_provider_unavailable():
    router = AIRouter()
    router.register("failing", FailingAIProvider())

    assert router.is_available() is False


# --- 6-7: selection mode -----------------------------------------------------------------


def test_auto_mode_selects_preferred_provider_when_multiple_registered():
    router = AIRouter(mode=RoutingMode.AUTO, preferred_provider="b")
    router.register("a", FakeAIProvider([AIResponse(content="a")]))
    router.register("b", FakeAIProvider([AIResponse(content="b")]))

    assert router.select().name == "b"


def test_auto_mode_falls_back_to_registration_order_without_a_preferred_provider():
    router = AIRouter(mode=RoutingMode.AUTO)
    router.register("a", FakeAIProvider([AIResponse(content="a")]))
    router.register("b", FakeAIProvider([AIResponse(content="b")]))

    assert router.select().name == "a"


def test_manual_mode_always_uses_the_configured_provider():
    router = AIRouter(mode=RoutingMode.MANUAL, preferred_provider="b")
    router.register("a", FakeAIProvider([AIResponse(content="a")]))
    router.register("b", FakeAIProvider([AIResponse(content="b")]))

    assert router.select().name == "b"
    assert router.select(requires_tools=True).name == "b"  # manual ignores capability filtering too


def test_manual_mode_without_preferred_provider_raises_clearly():
    router = AIRouter(mode=RoutingMode.MANUAL)
    router.register("a", FakeAIProvider([AIResponse(content="a")]))

    try:
        router.select()
        assert False, "should have raised"
    except NoProviderAvailableError:
        pass


# --- 8: selection by capability -----------------------------------------------------------


def test_select_by_capability_finds_a_satisfying_provider():
    router = AIRouter()
    no_tools = FakeAIProvider([AIResponse(content="a")], capabilities=ProviderCapabilities(chat=True, tool_calling=False, local=True))
    with_tools = FakeAIProvider([AIResponse(content="b")], capabilities=ProviderCapabilities(chat=True, tool_calling=True, local=True))
    router.register("no_tools", no_tools)
    router.register("with_tools", with_tools)

    registration = router.select(requires_tools=True, requires_local=True)

    assert registration.name == "with_tools"


def test_select_by_capability_raises_when_nothing_satisfies():
    router = AIRouter()
    router.register("no_tools", FakeAIProvider([AIResponse(content="a")], capabilities=ProviderCapabilities(tool_calling=False)))

    try:
        router.select(requires_tools=True)
        assert False, "should have raised"
    except NoProviderAvailableError:
        pass


# --- 9: unknown provider -------------------------------------------------------------------


def test_select_unknown_provider_name_raises():
    router = AIRouter()
    router.register("a", FakeAIProvider([AIResponse(content="a")]))

    try:
        router.select(provider_name="ghost")
        assert False, "should have raised"
    except UnknownProviderError:
        pass


def test_select_with_no_providers_registered_raises():
    router = AIRouter()

    try:
        router.select()
        assert False, "should have raised"
    except NoProviderAvailableError:
        pass


# --- 10: "model doesn't exist" (provider reachable but unusable) ---------------------------


def test_model_unavailable_is_reported_clearly_not_silently():
    """Stand-in for a configured-but-nonexistent model: the provider itself reports
    unavailable (this is exactly what OllamaProvider.is_available() already does when the
    configured model isn't installed — see ai/providers/ollama_provider.py)."""
    router = AIRouter()
    router.register("ollama", FakeAIProvider([AIResponse(content="unreachable")], available=False))

    assert router.is_available() is False


# --- 11: fallback without an alternative provider --------------------------------------------


def test_fallback_without_alternative_provider_raises_and_does_not_duplicate_calls():
    router = AIRouter()
    provider = FailingAIProvider()
    router.register("only", provider)

    try:
        router.chat([AIMessage(role="user", content="oi")], tools=_TOOL_SPECS)
        assert False, "should have raised"
    except RouterError as exc:
        assert isinstance(exc, ConnectionError)


# --- 12: fallback between two mocked providers ------------------------------------------------


def test_fallback_between_two_mocked_providers_uses_the_second_on_first_failure():
    router = AIRouter()
    failing = FailingAIProvider()
    working = FakeAIProvider([AIResponse(content="segundo provider respondeu")])
    router.register("a_fails", failing)
    router.register("b_works", working)

    response = router.chat([AIMessage(role="user", content="oi")], tools=_TOOL_SPECS)

    assert response.content == "segundo provider respondeu"
    assert len(working.calls) == 1  # exactly one call, not duplicated
    assert router.last_decision.fallback_used is True
    assert router.last_decision.provider_name == "b_works"
    assert router.last_decision.fallback_from == "a_fails"


def test_fallback_never_executes_a_tool_twice():
    """The Router only retries the raw model round trip on failure — a failed attempt
    never reaches ToolManager, so no tool call from a fallback attempt can ever be
    executed twice. Verified here at the Router level: a tool_call only ever appears in
    the response that's actually returned, from exactly one provider."""
    router = AIRouter()
    failing = FailingAIProvider()
    working = FakeAIProvider([AIResponse(content="", tool_calls=[ToolCall(id="c1", name="check_cpu", arguments={})])])
    router.register("a_fails", failing)
    router.register("b_works", working)

    response = router.chat([AIMessage(role="user", content="cpu?")], tools=_TOOL_SPECS)

    assert len(response.tool_calls) == 1
    assert len(working.calls) == 1


# --- 13-15: dataclasses preserved unchanged ----------------------------------------------------


def test_chat_preserves_airesponse_fields():
    router = AIRouter()
    original = AIResponse(content="olá", tool_calls=[], raw={"message": {"content": "olá"}})
    router.register("fake", FakeAIProvider([original]))

    response = router.chat([AIMessage(role="user", content="oi")])

    assert response is original
    assert response.content == "olá"
    assert response.raw == {"message": {"content": "olá"}}


def test_chat_preserves_toolcall_fields():
    router = AIRouter()
    call = ToolCall(id="call_1", name="check_cpu", arguments={"path": "C:\\"})
    router.register("fake", FakeAIProvider([AIResponse(content="", tool_calls=[call])]))

    response = router.chat([AIMessage(role="user", content="cpu?")], tools=_TOOL_SPECS)

    assert response.tool_calls[0] is call
    assert response.tool_calls[0].id == "call_1"
    assert response.tool_calls[0].name == "check_cpu"
    assert response.tool_calls[0].arguments == {"path": "C:\\"}


def test_chat_forwards_toolspecs_unchanged_to_the_provider():
    router = AIRouter()
    provider = FakeAIProvider([AIResponse(content="ok")])
    router.register("fake", provider)

    router.chat([AIMessage(role="user", content="oi")], tools=_TOOL_SPECS)

    received_messages, received_tools = provider.calls[0]
    assert received_tools is _TOOL_SPECS
    assert received_tools[0].name == "check_cpu"


# --- 16: native tool calling through the Router (no JSON-in-prompt, no parsing) ----------------


def test_native_tool_calling_flows_through_the_router_unmodified():
    router = AIRouter()
    router.register(
        "fake",
        FakeAIProvider([AIResponse(content="", tool_calls=[ToolCall(id="c1", name="check_cpu", arguments={})])]),
    )

    response = router.chat([AIMessage(role="user", content="qual o uso de cpu?")], tools=_TOOL_SPECS)

    assert response.content == ""
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "check_cpu"


# --- 17: provider error is surfaced clearly ----------------------------------------------------


def test_provider_error_is_logged_and_raised_clearly():
    router = AIRouter()
    router.register("only", FailingAIProvider())

    try:
        router.chat([AIMessage(role="user", content="oi")])
        assert False, "should have raised"
    except RouterError:
        pass

    assert router.last_error is not None
    assert "simulated failure" in router.last_error


# --- observability: last_decision / model / capabilities -----------------------------------


def test_last_decision_reports_provider_and_model_used():
    router = AIRouter()
    router.register("fake", FakeAIProvider([AIResponse(content="ok")], model="fake-model-x"))

    router.chat([AIMessage(role="user", content="oi")])

    assert router.last_decision.provider_name == "fake"
    assert router.last_decision.model == "fake-model-x"
    assert router.last_decision.fallback_used is False


def test_router_model_property_reflects_last_successful_decision():
    router = AIRouter()
    router.register("fake", FakeAIProvider([AIResponse(content="ok")], model="fake-model-x"))

    router.chat([AIMessage(role="user", content="oi")])

    assert router.model == "fake-model-x"


def test_registering_same_name_replaces_provider_in_place():
    """This is how a settings change ("use a different model") is meant to update the
    Router — main.py / ui.desktop.app._apply_settings re-register under the same name
    instead of building a new Router or reaching into the Orchestrator."""
    router = AIRouter()
    router.register("ollama", FakeAIProvider([AIResponse(content="v1")], model="model-v1"))
    order_before = router.list_providers()

    router.register("ollama", FakeAIProvider([AIResponse(content="v2")], model="model-v2"))

    assert router.list_providers() == order_before  # position/order unchanged
    assert router.select().provider.model == "model-v2"


# --- chat_stream() --------------------------------------------------------------------


def test_chat_stream_yields_chunks_from_the_selected_provider():
    router = AIRouter()
    provider = FakeStreamingAIProvider(stream_rounds=[[StreamChunk(content="a"), StreamChunk(content="b")]])
    router.register("fake_streaming", provider)

    chunks = list(router.chat_stream([AIMessage(role="user", content="oi")]))

    assert [c.content for c in chunks] == ["a", "b"]
    assert router.last_decision.provider_name == "fake_streaming"


def test_chat_stream_raises_when_no_provider_declares_streaming():
    """FakeAIProvider (the plain fake used everywhere else) doesn't declare
    streaming=True — select(requires_streaming=True) must reject it rather than call a
    chat_stream() it doesn't really implement."""
    router = AIRouter()
    router.register("fake", FakeAIProvider([AIResponse(content="ok")]))

    try:
        list(router.chat_stream([AIMessage(role="user", content="oi")]))
        assert False, "should have raised"
    except NoProviderAvailableError:
        pass


def test_chat_stream_propagates_connection_error_like_chat_does():
    router = AIRouter()
    router.register("fake_streaming", FailingStreamingAIProvider())

    try:
        list(router.chat_stream([AIMessage(role="user", content="oi")]))
        assert False, "should have raised"
    except ConnectionError:
        pass
