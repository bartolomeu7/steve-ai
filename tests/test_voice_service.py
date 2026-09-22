from __future__ import annotations

import time
from pathlib import Path

from tests.conftest import FakeConfirmationService
from tests.fakes import FakeAIProvider, FakeStreamingAIProvider
from voice.audio import AudioCaptureError
from voice.service import (
    EMPTY_REPLY_MESSAGE,
    MIC_ERROR_MESSAGE,
    NO_SPEECH_MESSAGE,
    STT_UNAVAILABLE_MESSAGE,
    VoiceService,
)


class _FakeAudioCapture:
    def __init__(self, path: Path | None = None, error: Exception | None = None):
        self._path = path
        self._error = error

    def record_until_silence(self, **kwargs):
        if self._error is not None:
            raise self._error
        return self._path


class _FakeSTT:
    def __init__(self, text: str | None = "", available: bool = True, raise_exc: Exception | None = None):
        self._text = text
        self._available = available
        self._raise_exc = raise_exc

    def is_available(self) -> bool:
        return self._available

    def transcribe(self, audio_path: str) -> str:
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._text


class _FakeTTS:
    def __init__(self, available: bool = True, succeed: bool = True):
        self._available = available
        self._succeed = succeed
        self.spoken: list[str] = []

    def is_available(self) -> bool:
        return self._available

    def speak(self, text: str) -> bool:
        self.spoken.append(text)
        return self._succeed


class _FakeOrchestrator:
    def __init__(self, reply: str = "Ok."):
        self.reply = reply
        self.received: list[str] = []

    def handle_message(self, text: str, on_tool_call=None) -> str:
        self.received.append(text)
        return self.reply


class _FakeStreamingOrchestrator:
    """Stands in for Orchestrator.handle_message_stream() — yields the reply in
    pieces, like the real generator does."""

    def __init__(self, chunks: list[str] | None = None):
        self.chunks = chunks if chunks is not None else ["Ok."]
        self.received: list[str] = []

    def handle_message_stream(self, text: str, on_tool_call=None):
        self.received.append(text)
        yield from self.chunks


def test_stt_unavailable_returns_friendly_error(tmp_path):
    service = VoiceService(
        audio_capture=_FakeAudioCapture(path=tmp_path / "a.wav"),
        stt=_FakeSTT(available=False),
        orchestrator=_FakeOrchestrator(),
        tts=_FakeTTS(),
    )

    result = service.listen_and_respond()

    assert result.success is False
    assert result.error == STT_UNAVAILABLE_MESSAGE


def test_microphone_error_returns_friendly_error():
    service = VoiceService(
        audio_capture=_FakeAudioCapture(error=AudioCaptureError("sem microfone")),
        stt=_FakeSTT(),
        orchestrator=_FakeOrchestrator(),
        tts=_FakeTTS(),
    )

    result = service.listen_and_respond()

    assert result.success is False
    assert result.error == MIC_ERROR_MESSAGE


def test_empty_transcript_returns_friendly_error(tmp_path):
    service = VoiceService(
        audio_capture=_FakeAudioCapture(path=tmp_path / "a.wav"),
        stt=_FakeSTT(text="   "),
        orchestrator=_FakeOrchestrator(),
        tts=_FakeTTS(),
    )

    result = service.listen_and_respond()

    assert result.success is False
    assert result.error == NO_SPEECH_MESSAGE


def test_transcribe_exception_returns_friendly_error(tmp_path):
    service = VoiceService(
        audio_capture=_FakeAudioCapture(path=tmp_path / "a.wav"),
        stt=_FakeSTT(raise_exc=RuntimeError("modelo travou")),
        orchestrator=_FakeOrchestrator(),
        tts=_FakeTTS(),
    )

    result = service.listen_and_respond()

    assert result.success is False
    assert result.error == NO_SPEECH_MESSAGE


def test_empty_orchestrator_reply_returns_friendly_error(tmp_path):
    service = VoiceService(
        audio_capture=_FakeAudioCapture(path=tmp_path / "a.wav"),
        stt=_FakeSTT(text="oi"),
        orchestrator=_FakeOrchestrator(reply="   "),
        tts=_FakeTTS(),
    )

    result = service.listen_and_respond()

    assert result.success is False
    assert result.error == EMPTY_REPLY_MESSAGE


def test_happy_path_transcribes_asks_orchestrator_and_speaks(tmp_path):
    orchestrator = _FakeOrchestrator(reply="Tudo certo por aqui.")
    tts = _FakeTTS()
    service = VoiceService(
        audio_capture=_FakeAudioCapture(path=tmp_path / "a.wav"),
        stt=_FakeSTT(text="Steve, tudo bem?"),
        orchestrator=orchestrator,
        tts=tts,
    )

    states: list[str] = []
    result = service.listen_and_respond(on_state=states.append)

    assert result.success is True
    assert result.transcript == "Steve, tudo bem?"
    assert result.reply == "Tudo certo por aqui."
    assert orchestrator.received == ["Steve, tudo bem?"]
    assert tts.spoken == ["Tudo certo por aqui."]
    assert states == ["🎙️ Ouvindo...", "🧠 Processando...", "🔊 Respondendo..."]
    assert "capture_s" in result.timings
    assert "stt_s" in result.timings
    assert "orchestrator_s" in result.timings
    assert "tts_s" in result.timings
    assert "total_s" in result.timings


def test_tts_failure_does_not_fail_the_turn(tmp_path):
    service = VoiceService(
        audio_capture=_FakeAudioCapture(path=tmp_path / "a.wav"),
        stt=_FakeSTT(text="oi"),
        orchestrator=_FakeOrchestrator(reply="Olá!"),
        tts=_FakeTTS(succeed=False),
    )

    result = service.listen_and_respond()

    assert result.success is True
    assert result.reply == "Olá!"


def test_tts_unavailable_does_not_fail_the_turn(tmp_path):
    service = VoiceService(
        audio_capture=_FakeAudioCapture(path=tmp_path / "a.wav"),
        stt=_FakeSTT(text="oi"),
        orchestrator=_FakeOrchestrator(reply="Olá!"),
        tts=_FakeTTS(available=False),
    )

    result = service.listen_and_respond()

    assert result.success is True
    assert result.reply == "Olá!"


def test_voice_service_uses_the_real_orchestrator_no_second_brain(
    tmp_path, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    """VoiceService must not reimplement any conversation/tool logic — it should behave
    identically to a text turn through the exact same Orchestrator."""
    from ai.base import AIResponse, ToolCall
    from core.orchestrator import Orchestrator

    from ai.router import AIRouter

    provider = FakeAIProvider(
        [
            AIResponse(content="", tool_calls=[ToolCall(id="c1", name="check_cpu", arguments={})]),
            AIResponse(content="Uso de CPU sob controle."),
        ]
    )
    router = AIRouter()
    router.register("fake", provider)
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

    service = VoiceService(
        audio_capture=_FakeAudioCapture(path=tmp_path / "a.wav"),
        stt=_FakeSTT(text="Como está o uso de CPU?"),
        orchestrator=orchestrator,
        tts=_FakeTTS(),
    )

    tool_calls_seen: list[str] = []
    result = service.listen_and_respond(on_tool_call=tool_calls_seen.append)

    assert result.success is True
    assert result.reply == "Uso de CPU sob controle."
    # the tool call really went through ToolManager/PermissionManager, same as text mode
    assert [t.role for t in session.turns] == ["user", "assistant"]
    assert tool_calls_seen == ["check_cpu"]
    assert session.turns[0].content == "Como está o uso de CPU?"


# --- V1.4: voice.state_changed on the shared EventBus ------------------------------------


def test_voice_service_without_event_bus_still_works(tmp_path):
    """Backward compatible: event_bus defaults to None."""
    service = VoiceService(
        audio_capture=_FakeAudioCapture(path=tmp_path / "a.wav"),
        stt=_FakeSTT(text="oi"),
        orchestrator=_FakeOrchestrator(reply="Ok."),
        tts=_FakeTTS(),
    )
    result = service.listen_and_respond()
    assert result.success is True


def test_voice_service_publishes_state_events_matching_on_state(tmp_path):
    from core.events import EventBus
    from voice.service import VOICE_STATE_CHANGED

    bus = EventBus()
    published: list[str] = []
    bus.subscribe(VOICE_STATE_CHANGED, lambda data: published.append(data["state"]))

    service = VoiceService(
        audio_capture=_FakeAudioCapture(path=tmp_path / "a.wav"),
        stt=_FakeSTT(text="oi"),
        orchestrator=_FakeOrchestrator(reply="Ok."),
        tts=_FakeTTS(),
        event_bus=bus,
    )

    on_state_seen: list[str] = []
    service.listen_and_respond(on_state=on_state_seen.append)

    assert published == on_state_seen  # every on_state call also reached the EventBus
    assert len(published) == 3


# --- listen_and_respond_streaming() ---------------------------------------------------


def test_streaming_stt_unavailable_returns_friendly_error(tmp_path):
    """Same up-front guards as the non-streaming path — capture/STT failures are
    unrelated to how the reply gets spoken."""
    service = VoiceService(
        audio_capture=_FakeAudioCapture(path=tmp_path / "a.wav"),
        stt=_FakeSTT(available=False),
        orchestrator=_FakeStreamingOrchestrator(),
        tts=_FakeTTS(),
    )

    result = service.listen_and_respond_streaming()

    assert result.success is False
    assert result.error == STT_UNAVAILABLE_MESSAGE


def test_streaming_happy_path_speaks_each_chunk_and_joins_the_full_reply(tmp_path):
    orchestrator = _FakeStreamingOrchestrator(chunks=["Tudo certo ", "por aqui."])
    tts = _FakeTTS()
    service = VoiceService(
        audio_capture=_FakeAudioCapture(path=tmp_path / "a.wav"),
        stt=_FakeSTT(text="Steve, tudo bem?"),
        orchestrator=orchestrator,
        tts=tts,
    )

    result = service.listen_and_respond_streaming()

    assert result.success is True
    assert result.transcript == "Steve, tudo bem?"
    assert result.reply == "Tudo certo por aqui."
    assert orchestrator.received == ["Steve, tudo bem?"]
    assert "".join(tts.spoken) == "Tudo certo por aqui."  # spoken sentence-by-sentence, joins back up
    assert "orchestrator_and_tts_s" in result.timings
    assert "total_s" in result.timings


def test_streaming_uses_the_real_orchestrator_and_still_executes_tools(
    tmp_path, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    """End-to-end: VoiceService -> Orchestrator.handle_message_stream ->
    StreamingSpeaker -> TTS, through the REAL Orchestrator (not a fake) — proves a
    tool-calling voice turn still goes through ToolManager/PermissionManager/
    AuditLogger correctly when streaming is on, exactly like the non-streaming
    equivalent (test_voice_service_uses_the_real_orchestrator_no_second_brain)."""
    from ai.base import StreamChunk, ToolCall
    from ai.router import AIRouter
    from core.orchestrator import Orchestrator

    provider = FakeStreamingAIProvider(
        stream_rounds=[
            [StreamChunk(tool_calls=[ToolCall(id="c1", name="check_cpu", arguments={})]), StreamChunk(done=True)],
            [StreamChunk(content="Uso de CPU "), StreamChunk(content="sob controle."), StreamChunk(done=True)],
        ]
    )
    router = AIRouter()
    router.register("fake_streaming", provider)
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

    tts = _FakeTTS()
    service = VoiceService(
        audio_capture=_FakeAudioCapture(path=tmp_path / "a.wav"),
        stt=_FakeSTT(text="Como está o uso de CPU?"),
        orchestrator=orchestrator,
        tts=tts,
    )

    tool_calls_seen: list[str] = []
    result = service.listen_and_respond_streaming(on_tool_call=tool_calls_seen.append)

    assert result.success is True
    assert result.reply == "Uso de CPU sob controle."
    assert tool_calls_seen == ["check_cpu"]
    assert [t.role for t in session.turns] == ["user", "assistant"]
    assert "".join(tts.spoken) == "Uso de CPU sob controle."


# --- Action Narrator (Pack 3 — replaces Pack 2's ToolFiller as the active mechanism) --


def _make_tool(name: str, sleep_seconds: float = 0.0):
    """Real Tool subclass standing in for any tool the narrator might announce."""
    from security.permissions import PermissionLevel
    from tools.base import Tool, ToolResult

    class _RealTool(Tool):
        description = "Ferramenta de teste."
        permission_level = PermissionLevel.LOW
        parameters_schema = {"type": "object", "properties": {}, "required": []}

        def validate(self, params: dict) -> tuple[bool, str]:
            return True, ""

        def execute(self, params: dict) -> ToolResult:
            if sleep_seconds:
                time.sleep(sleep_seconds)
            return ToolResult(success=True, verified=True, message="Feito.")

    _RealTool.name = name
    return _RealTool()


def _real_orchestrator_with_tool(tool, provider, tool_manager, context_manager, session,
                                  memory_service, settings, permission_manager, audit_logger, event_bus):
    from ai.router import AIRouter
    from core.orchestrator import Orchestrator

    tool_manager.register(tool)
    router = AIRouter()
    router.register("fake", provider)
    return Orchestrator(
        ai_router=router,
        tool_manager=tool_manager,
        context_manager=context_manager,
        session=session,
        memory_service=memory_service,
        settings=settings,
        permission_manager=permission_manager,
        confirmation_service=FakeConfirmationService(approve=True),
        audit_logger=audit_logger,
        event_bus=event_bus,
    )


def test_narrator_speaks_for_any_real_tool_call_even_a_fast_one(
    tmp_path, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    """Unlike Pack 2's ToolFiller (delay-gated, silent for fast tools), ActionNarrator
    narrates intent regardless of tool speed — matching the Pack 3 spec's own example
    ("Steve executa OPEN_APPLICATION... Enquanto executa: 'Claro. Abrindo o Chrome.'"),
    which is narrated even though opening an app is fast."""
    from ai.base import AIResponse, ToolCall
    from core.events import EventBus

    bus = EventBus()
    provider = FakeAIProvider(
        [
            AIResponse(content="", tool_calls=[ToolCall(id="c1", name="open_application", arguments={})]),
            AIResponse(content="Pronto rápido."),
        ]
    )
    orchestrator = _real_orchestrator_with_tool(
        _make_tool("open_application", sleep_seconds=0.0), provider, tool_manager, context_manager, session,
        memory_service, settings, permission_manager, audit_logger, event_bus=bus,
    )

    tts = _FakeTTS()
    service = VoiceService(
        audio_capture=_FakeAudioCapture(path=tmp_path / "a.wav"),
        stt=_FakeSTT(text="Abre o Chrome."),
        orchestrator=orchestrator,
        tts=tts,
        event_bus=bus,
    )

    result = service.listen_and_respond()

    assert result.success is True
    assert len(tts.spoken) == 2  # narration phrase, then the real reply
    assert tts.spoken[1] == "Pronto rápido."


def test_narrator_stays_silent_when_no_tool_is_called(tmp_path):
    """A plain conversational reply (no tool_calls at all) must never trigger
    narration — scoped to TOOL_STARTED/TOOL_COMPLETED specifically, not the whole
    orchestrator call (which would narrate over every reply, tool or not)."""
    from core.events import EventBus

    bus = EventBus()
    tts = _FakeTTS()
    service = VoiceService(
        audio_capture=_FakeAudioCapture(path=tmp_path / "a.wav"),
        stt=_FakeSTT(text="Oi, Steve."),
        orchestrator=_FakeOrchestrator(reply="Oi! Tudo bem?"),
        tts=tts,
        event_bus=bus,
    )

    result = service.listen_and_respond()

    assert result.success is True
    assert tts.spoken == ["Oi! Tudo bem?"]


def test_narrator_works_without_an_event_bus():
    """No EventBus means no TOOL_STARTED/TOOL_COMPLETED signal to drive the narrator —
    _narrator_active() must no-op cleanly rather than error."""
    service = VoiceService(
        audio_capture=_FakeAudioCapture(path=Path("a.wav")),
        stt=_FakeSTT(text="oi"),
        orchestrator=_FakeOrchestrator(reply="Ok."),
        tts=_FakeTTS(),
        event_bus=None,
    )

    with service._narrator_active():
        pass  # must not raise


def test_narrator_uses_the_live_tts_not_a_stale_reference_from_construction(
    tmp_path, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    """Regression guard: the narrator must speak through self.tts as it is AT CALL
    TIME, not whatever `tts` VoiceService was constructed with — main.py's SteveApp
    swaps voice_service.tts for an OrbReactiveTTS wrapper after construction (see
    ui/desktop/app.py::_wrap_tts_for_orb)."""
    from ai.base import AIResponse, ToolCall
    from core.events import EventBus

    bus = EventBus()
    provider = FakeAIProvider(
        [
            AIResponse(content="", tool_calls=[ToolCall(id="c1", name="open_application", arguments={})]),
            AIResponse(content="Pronto."),
        ]
    )
    orchestrator = _real_orchestrator_with_tool(
        _make_tool("open_application"), provider, tool_manager, context_manager, session, memory_service,
        settings, permission_manager, audit_logger, event_bus=bus,
    )

    original_tts = _FakeTTS()
    replacement_tts = _FakeTTS()
    service = VoiceService(
        audio_capture=_FakeAudioCapture(path=tmp_path / "a.wav"),
        stt=_FakeSTT(text="Abre o Chrome."),
        orchestrator=orchestrator,
        tts=original_tts,
        event_bus=bus,
    )
    service.tts = replacement_tts  # simulates _wrap_tts_for_orb happening after construction

    service.listen_and_respond()

    assert original_tts.spoken == []
    assert len(replacement_tts.spoken) == 2


def test_narrator_uses_a_tool_specific_phrase_not_the_generic_filler(
    tmp_path, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger
):
    """web_search has its own NARRATIVES entry — confirms the real per-tool-type
    phrasing is actually reached through the EventBus wiring, not just a generic one.
    get_active_window is pinned to "" (deterministic) so this doesn't depend on
    whatever window happens to have focus on the machine running the test."""
    from ai.base import AIResponse, ToolCall
    from core.events import EventBus
    from voice.action_narrator import NARRATIVES, ActionNarrator

    bus = EventBus()
    provider = FakeAIProvider(
        [
            AIResponse(content="", tool_calls=[ToolCall(id="c1", name="web_search", arguments={})]),
            AIResponse(content="Aqui está o que encontrei."),
        ]
    )
    orchestrator = _real_orchestrator_with_tool(
        _make_tool("web_search"), provider, tool_manager, context_manager, session, memory_service,
        settings, permission_manager, audit_logger, event_bus=bus,
    )

    tts = _FakeTTS()
    service = VoiceService(
        audio_capture=_FakeAudioCapture(path=tmp_path / "a.wav"),
        stt=_FakeSTT(text="Pesquisa o que é RAG."),
        orchestrator=orchestrator,
        tts=tts,
        event_bus=bus,
        narrator=ActionNarrator(tts=tts, get_active_window=lambda: ""),
    )

    service.listen_and_respond()

    assert tts.spoken[0] in NARRATIVES["web_search"]["start"]
