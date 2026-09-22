"""MainWindow tests — real window creation (this machine has a display) driven
programmatically: direct method/widget calls exercise exactly what a mouse click or
keypress would trigger, without needing actual mouse/keyboard input."""
from __future__ import annotations

import pytest

from tests.test_desktop_controller import _FakeOrchestrator, _FakeRunner
from ui.desktop.controller import ChatController
from ui.desktop.window import MainWindow
from voice.service import VoiceTurnResult


def _status(**overrides) -> dict:
    base = {
        "model": "llama3.2",
        "ollama_online": True,
        "mic_available": True,
        "voice_available": True,
        "tools_count": 9,
    }
    base.update(overrides)
    return base


@pytest.fixture
def window():
    w = MainWindow(app_status=_status(), voice_available=True, orb_enabled=False)
    w.withdraw()
    yield w
    try:
        w.destroy()
    except Exception:
        pass


def test_window_creates_with_expected_title(window):
    assert window.title() == "Steve"


def test_window_starts_with_no_messages(window):
    assert window.chat_view.message_count == 0


def test_apply_status_updates_labels(window):
    window.apply_status(_status(model="qwen2.5", ollama_online=False, mic_available=False, voice_available=False, tools_count=3))

    assert "qwen2.5" in window.status_bar._labels["model"].cget("text")
    assert "Ollama" in window.status_bar._labels["ollama"].cget("text")
    assert "Offline" in window.connection_label.cget("text")
    assert "Microfone" in window.status_bar._labels["mic"].cget("text")


def test_on_reply_adds_steve_bubble_clears_busy_and_calls_hook(window):
    window.input_bar.set_busy(True)
    seen = []
    window._on_reply_cb = seen.append

    window._on_reply("Olá!")

    assert window.chat_view.message_count == 1
    assert seen == ["Olá!"]
    assert window.input_bar.entry.cget("state") == "normal"


def test_on_error_adds_a_single_system_bubble(window):
    window._on_error("Não consegui acessar o microfone.")
    assert window.chat_view.message_count == 1


def test_on_voice_result_success_adds_transcript_and_reply_bubbles(window):
    result = VoiceTurnResult(success=True, transcript="oi", reply="Tudo bem?")
    window._on_voice_result(result)
    assert window.chat_view.message_count == 2


def test_on_voice_result_failure_adds_one_system_bubble(window):
    result = VoiceTurnResult(success=False, error="Não consegui entender o áudio.")
    window._on_voice_result(result)
    assert window.chat_view.message_count == 1


def test_close_invokes_on_close_callback():
    closed = []
    w = MainWindow(app_status=_status(), voice_available=False, orb_enabled=False, on_close=lambda: closed.append(True))
    w.withdraw()

    w._handle_close()

    assert closed == [True]


def test_close_destroys_window_when_callback_returns_none_or_true():
    """Backward compatible: a callback with the old void contract (implicit None) must
    still result in a full close, same as before minimize-to-tray existed. Destroying the
    real root window makes winfo_exists() itself raise (the whole Tcl interpreter tears
    down), so a destroy spy is used instead of calling into the window afterwards."""
    destroyed = []
    w = MainWindow(app_status=_status(), voice_available=False, orb_enabled=False, on_close=lambda: None)
    w.withdraw()
    w.destroy = lambda: destroyed.append(True)

    w._handle_close()

    assert destroyed == [True]
    del w.destroy  # remove the spy so the real root window is actually torn down
    w.destroy()


def test_close_keeps_window_alive_when_callback_returns_false():
    destroyed = []
    w = MainWindow(app_status=_status(), voice_available=False, orb_enabled=False, on_close=lambda: False)
    w.withdraw()
    w.destroy = lambda: destroyed.append(True)

    w._handle_close()

    assert destroyed == []
    assert w.winfo_exists() == 1
    del w.destroy  # remove the spy override, restore the real bound method
    w.destroy()


def test_explicit_quit_always_destroys_the_window():
    quit_called = []
    destroyed = []
    w = MainWindow(app_status=_status(), voice_available=False, orb_enabled=False, on_quit=lambda: quit_called.append(True))
    w.withdraw()
    w.destroy = lambda: destroyed.append(True)

    w._handle_quit()

    assert quit_called == [True]
    assert destroyed == [True]
    del w.destroy
    w.destroy()


def test_hide_and_show_toggle_viewability():
    w = MainWindow(app_status=_status(), voice_available=False, orb_enabled=False)

    w.hide()
    assert w.state() == "withdrawn"

    w.show()
    assert w.state() != "withdrawn"
    w.destroy()


def test_mic_button_disabled_when_voice_unavailable():
    w = MainWindow(app_status=_status(mic_available=False, voice_available=False), voice_available=False, orb_enabled=False)
    w.withdraw()
    try:
        assert w.input_bar.mic_button.cget("state") == "disabled"
    finally:
        w.destroy()


def test_settings_button_triggers_callback():
    opened = []
    w = MainWindow(app_status=_status(), voice_available=True, orb_enabled=False, on_open_settings=lambda: opened.append(True))
    w.withdraw()
    try:
        w._handle_open_settings()
        assert opened == [True]
    finally:
        w.destroy()


def test_send_via_entry_and_button_end_to_end(window):
    """Programmatically drives the exact path a real click/Enter would: insert text
    into the entry, then invoke the same handler the button's command/Return binding
    calls. Uses a synchronous FakeRunner so no real thread/timer pumping is needed."""
    orchestrator = _FakeOrchestrator(reply="Oi para você também!")
    controller = ChatController(_FakeRunner(), orchestrator, voice_service=None)
    window.controller = controller

    window.input_bar.entry.insert(0, "Oi Steve")
    window.input_bar._handle_send()

    assert orchestrator.received == ["Oi Steve"]
    assert window.chat_view.message_count == 2  # user bubble + Steve's reply
    assert window.input_bar.entry.get() == ""  # cleared after send


def test_does_not_start_a_second_turn_while_busy(window):
    """Regression guard: two messages must never be processed concurrently."""
    orchestrator = _FakeOrchestrator(reply="Ok.")
    controller = ChatController(_FakeRunner(), orchestrator, voice_service=None)
    window.controller = controller
    controller._busy = True  # simulate a turn already in flight

    window.input_bar.entry.insert(0, "Segunda mensagem")
    window.input_bar._handle_send()

    assert orchestrator.received == []  # never reached the orchestrator


def test_on_orb_state_updates_orb_and_label():
    from ui.desktop.orb.states import STATE_LABELS, OrbState

    w = MainWindow(app_status=_status(), voice_available=True, orb_enabled=True)
    w.withdraw()
    try:
        w._on_orb_state(OrbState.LISTENING)
        assert w.orb.animator.state is OrbState.LISTENING
        assert w.orb_label.cget("text") == STATE_LABELS[OrbState.LISTENING]
    finally:
        w.orb.stop()
        w.destroy()


def test_on_mic_amplitude_forwards_a_normalized_value_to_the_orb():
    w = MainWindow(app_status=_status(), voice_available=True, orb_enabled=True)
    w.withdraw()
    try:
        w._on_mic_amplitude(3000.0)  # raw RMS, as AudioCapture reports it
        assert 0.0 < w.orb.animator._amplitude <= 1.0
    finally:
        w.orb.stop()
        w.destroy()


def test_orb_disabled_ignores_state_and_amplitude_updates():
    w = MainWindow(app_status=_status(), voice_available=True, orb_enabled=False)
    w.withdraw()
    try:
        from ui.desktop.orb.states import OrbState

        w._on_orb_state(OrbState.SPEAKING)  # must not raise even though nothing renders
        w._on_mic_amplitude(3000.0)
    finally:
        w.destroy()


def test_orb_click_triggers_a_voice_turn_when_available():
    from ui.desktop.orb.states import OrbState
    from voice.service import VoiceTurnResult

    class _FakeVoiceService:
        def __init__(self):
            self.calls = 0

        def listen_and_respond(self, on_state=None, on_tool_call=None, on_amplitude=None):
            self.calls += 1
            return VoiceTurnResult(success=True, transcript="oi", reply="Olá!")

    w = MainWindow(app_status=_status(), voice_available=True, orb_enabled=True)
    w.withdraw()
    try:
        voice_service = _FakeVoiceService()
        w.controller = ChatController(_FakeRunner(), _FakeOrchestrator(), voice_service=voice_service)

        w._handle_orb_click()

        assert voice_service.calls == 1
        assert w.chat_view.message_count == 2  # transcript bubble + reply bubble
    finally:
        w.orb.stop()
        w.destroy()
