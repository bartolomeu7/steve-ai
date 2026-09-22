"""ChatController tests — no real Tk window or thread needed: a synchronous FakeRunner
stands in for BackgroundRunner, so these run fast and deterministically everywhere."""
from __future__ import annotations

from ui.desktop.controller import (
    DEFAULT_TOOL_STATUS_LABEL,
    ChatController,
    tool_status_label,
)
from ui.desktop.orb.states import OrbState


class _FakeRunner:
    """Runs `fn` synchronously (no thread) and calls callbacks immediately — makes
    ChatController's async orchestration deterministic to test."""

    def __init__(self):
        self.posted = []

    def run(self, fn, *args, on_done=None, on_error=None, **kwargs):
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if on_error:
                on_error(exc)
        else:
            if on_done:
                on_done(result)

    def post(self, callback, *args):
        self.posted.append((callback, args))
        callback(*args)


class _FakeOrchestrator:
    def __init__(self, reply="Ok.", raise_exc=None, tool_name=None):
        self.reply = reply
        self.raise_exc = raise_exc
        self.tool_name = tool_name
        self.received = []

    def handle_message(self, text, on_tool_call=None):
        self.received.append(text)
        if self.raise_exc:
            raise self.raise_exc
        if self.tool_name and on_tool_call:
            on_tool_call(self.tool_name)
        return self.reply


class _FakeVoiceService:
    """Mirrors the real VoiceService's event order: listening -> processing -> (tool
    call, if any) -> speaking, so tests reflect a realistic sequence of callbacks."""

    def __init__(self, result, states=("🎙️ Ouvindo...", "🧠 Processando...", "🔊 Respondendo...")):
        self.result = result
        self.states = states
        self.calls = 0

    def listen_and_respond(self, on_state=None, on_tool_call=None, on_amplitude=None):
        self.calls += 1
        if on_amplitude:
            on_amplitude(0.42)
        for i, state in enumerate(self.states):
            if on_state:
                on_state(state)
            if i == 1 and on_tool_call and getattr(self.result, "reply", None):
                on_tool_call("check_cpu")
        return self.result


def test_tool_status_label_known_and_unknown():
    assert tool_status_label("check_cpu") == "🧠 Consultando CPU..."
    assert tool_status_label("totally_unknown_tool") == DEFAULT_TOOL_STATUS_LABEL


def test_send_text_happy_path_calls_reply_and_toggles_busy():
    runner = _FakeRunner()
    orchestrator = _FakeOrchestrator(reply="Olá!")
    controller = ChatController(runner, orchestrator, voice_service=None)

    statuses, replies, errors = [], [], []
    busy_during = {}

    def on_status(text):
        statuses.append(text)
        busy_during["busy"] = controller.busy

    started = controller.send_text("Oi", on_status=on_status, on_reply=replies.append, on_error=errors.append)

    assert started is True
    assert orchestrator.received == ["Oi"]
    assert replies == ["Olá!"]
    assert errors == []
    assert controller.busy is False  # reset after completion
    assert busy_during["busy"] is True  # was busy while in flight


def test_send_text_rejects_empty_text():
    controller = ChatController(_FakeRunner(), _FakeOrchestrator(), voice_service=None)
    started = controller.send_text("   ", on_status=lambda t: None, on_reply=lambda r: None, on_error=lambda e: None)
    assert started is False


def test_send_text_rejects_when_already_busy():
    controller = ChatController(_FakeRunner(), _FakeOrchestrator(), voice_service=None)
    controller._busy = True
    started = controller.send_text("Oi", on_status=lambda t: None, on_reply=lambda r: None, on_error=lambda e: None)
    assert started is False


def test_send_text_reports_tool_status_via_on_status():
    runner = _FakeRunner()
    orchestrator = _FakeOrchestrator(reply="Feito.", tool_name="check_cpu")
    controller = ChatController(runner, orchestrator, voice_service=None)

    statuses = []
    controller.send_text("CPU?", on_status=statuses.append, on_reply=lambda r: None, on_error=lambda e: None)

    assert "🧠 Consultando CPU..." in statuses


def test_send_text_error_path_calls_on_error_and_resets_busy():
    runner = _FakeRunner()
    orchestrator = _FakeOrchestrator(raise_exc=RuntimeError("boom"))
    controller = ChatController(runner, orchestrator, voice_service=None)

    errors = []
    controller.send_text("Oi", on_status=lambda t: None, on_reply=lambda r: None, on_error=errors.append)

    assert len(errors) == 1
    assert controller.busy is False


def test_send_voice_returns_false_when_no_voice_service():
    controller = ChatController(_FakeRunner(), _FakeOrchestrator(), voice_service=None)
    assert controller.voice_available is False
    started = controller.send_voice(on_status=lambda t: None, on_result=lambda r: None, on_error=lambda e: None)
    assert started is False


def test_send_voice_happy_path():
    from voice.service import VoiceTurnResult

    result = VoiceTurnResult(success=True, transcript="oi", reply="Tudo bem.")
    voice_service = _FakeVoiceService(result)
    controller = ChatController(_FakeRunner(), _FakeOrchestrator(), voice_service=voice_service)

    assert controller.voice_available is True
    results = []
    started = controller.send_voice(on_status=lambda t: None, on_result=results.append, on_error=lambda e: None)

    assert started is True
    assert voice_service.calls == 1
    assert results == [result]
    assert controller.busy is False


def test_send_voice_rejects_when_already_busy():
    from voice.service import VoiceTurnResult

    voice_service = _FakeVoiceService(VoiceTurnResult(success=True, reply="ok"))
    controller = ChatController(_FakeRunner(), _FakeOrchestrator(), voice_service=voice_service)
    controller._busy = True

    started = controller.send_voice(on_status=lambda t: None, on_result=lambda r: None, on_error=lambda e: None)

    assert started is False
    assert voice_service.calls == 0


def test_send_text_reports_thinking_then_idle_orb_states():
    orchestrator = _FakeOrchestrator(reply="Olá!")
    controller = ChatController(_FakeRunner(), orchestrator, voice_service=None)

    orb_states = []
    controller.send_text(
        "Oi", on_status=lambda t: None, on_reply=lambda r: None, on_error=lambda e: None,
        on_orb_state=orb_states.append,
    )

    assert orb_states == [OrbState.THINKING, OrbState.IDLE]


def test_send_text_reports_tool_execution_orb_state():
    orchestrator = _FakeOrchestrator(reply="Feito.", tool_name="check_cpu")
    controller = ChatController(_FakeRunner(), orchestrator, voice_service=None)

    orb_states = []
    controller.send_text(
        "CPU?", on_status=lambda t: None, on_reply=lambda r: None, on_error=lambda e: None,
        on_orb_state=orb_states.append,
    )

    assert orb_states == [OrbState.THINKING, OrbState.TOOL_EXECUTION, OrbState.IDLE]


def test_send_text_error_reports_error_orb_state():
    orchestrator = _FakeOrchestrator(raise_exc=RuntimeError("boom"))
    controller = ChatController(_FakeRunner(), orchestrator, voice_service=None)

    orb_states = []
    controller.send_text(
        "Oi", on_status=lambda t: None, on_reply=lambda r: None, on_error=lambda e: None,
        on_orb_state=orb_states.append,
    )

    assert orb_states == [OrbState.THINKING, OrbState.ERROR]


def test_send_voice_maps_state_messages_to_orb_states():
    from voice.service import VoiceTurnResult

    result = VoiceTurnResult(success=True, transcript="oi", reply="Tudo bem.")
    voice_service = _FakeVoiceService(result)
    controller = ChatController(_FakeRunner(), _FakeOrchestrator(), voice_service=voice_service)

    orb_states = []
    controller.send_voice(
        on_status=lambda t: None, on_result=lambda r: None, on_error=lambda e: None,
        on_orb_state=orb_states.append,
    )

    assert orb_states == [
        OrbState.LISTENING,
        OrbState.THINKING,
        OrbState.TOOL_EXECUTION,  # the fake voice service also triggers a tool call
        OrbState.SPEAKING,
        OrbState.IDLE,
    ]


def test_send_voice_failure_result_reports_error_orb_state():
    from voice.service import VoiceTurnResult

    result = VoiceTurnResult(success=False, error="Não consegui acessar o microfone.")
    voice_service = _FakeVoiceService(result, states=())
    controller = ChatController(_FakeRunner(), _FakeOrchestrator(), voice_service=voice_service)

    orb_states = []
    controller.send_voice(
        on_status=lambda t: None, on_result=lambda r: None, on_error=lambda e: None,
        on_orb_state=orb_states.append,
    )

    assert orb_states == [OrbState.ERROR]


def test_send_voice_forwards_mic_amplitude():
    from voice.service import VoiceTurnResult

    voice_service = _FakeVoiceService(VoiceTurnResult(success=True, reply="ok"), states=())
    controller = ChatController(_FakeRunner(), _FakeOrchestrator(), voice_service=voice_service)

    amplitudes = []
    controller.send_voice(
        on_status=lambda t: None, on_result=lambda r: None, on_error=lambda e: None,
        on_mic_amplitude=amplitudes.append,
    )

    assert amplitudes == [0.42]


def test_orb_state_hook_is_optional_and_backward_compatible():
    """Every pre-existing caller omits on_orb_state — nothing should require it."""
    orchestrator = _FakeOrchestrator(reply="Ok.")
    controller = ChatController(_FakeRunner(), orchestrator, voice_service=None)

    started = controller.send_text("Oi", on_status=lambda t: None, on_reply=lambda r: None, on_error=lambda e: None)

    assert started is True
