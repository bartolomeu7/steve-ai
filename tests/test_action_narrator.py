"""Unit tests for voice/action_narrator.py, independent of VoiceService's EventBus
wiring (see tests/test_voice_service.py for the end-to-end version)."""
from __future__ import annotations

from ui.desktop.orb.states import OrbState
from voice.action_narrator import ActionNarrator, NARRATIVES


class _FakeTTS:
    def __init__(self):
        self.spoken: list[str] = []

    def speak(self, text: str) -> bool:
        self.spoken.append(text)
        return True


def test_action_speaks_a_start_phrase_for_a_known_tool():
    tts = _FakeTTS()
    narrator = ActionNarrator(tts=tts)

    with narrator.action("screenshot"):
        pass

    assert tts.spoken[0] in NARRATIVES["screenshot"]["start"]


def test_action_falls_back_to_generic_for_an_unknown_tool():
    tts = _FakeTTS()
    narrator = ActionNarrator(tts=tts)

    with narrator.action("some_tool_narrator_has_never_heard_of"):
        pass

    assert tts.spoken[0] in NARRATIVES["generic_tool"]["start"]


def test_action_updates_orb_states_in_order():
    states_seen = []
    narrator = ActionNarrator(orb_callback=states_seen.append)

    with narrator.action("volume"):
        pass

    assert states_seen[0] == OrbState.TOOL_EXECUTION
    assert states_seen[-1] == OrbState.THINKING


def test_action_updates_orb_even_when_the_wrapped_code_raises():
    """The orb must return to THINKING even if the tool call inside raises — narration
    bracket must not leave the orb stuck on TOOL_EXECUTION forever."""
    states_seen = []
    narrator = ActionNarrator(orb_callback=states_seen.append)

    try:
        with narrator.action("volume"):
            raise RuntimeError("tool blew up")
    except RuntimeError:
        pass

    assert states_seen[-1] == OrbState.THINKING


def test_web_search_narration_mentions_the_active_browser():
    narrator = ActionNarrator(tts=_FakeTTS(), get_active_window=lambda: "RAG - Google Chrome")
    tts = narrator.tts

    with narrator.action("web_search"):
        pass

    assert "Chrome" in tts.spoken[0]


def test_web_search_narration_without_active_window_hook_still_works():
    tts = _FakeTTS()
    narrator = ActionNarrator(tts=tts, get_active_window=None)

    with narrator.action("web_search"):
        pass

    assert tts.spoken[0] in NARRATIVES["web_search"]["start"]


def test_get_active_window_exception_does_not_crash_narration():
    def _boom():
        raise RuntimeError("winapi failed")

    tts = _FakeTTS()
    narrator = ActionNarrator(tts=tts, get_active_window=_boom)

    with narrator.action("web_search"):
        pass

    assert tts.spoken  # still narrated something, didn't crash


def test_speak_enabled_false_still_updates_orb_but_never_calls_tts():
    tts = _FakeTTS()
    states_seen = []
    narrator = ActionNarrator(tts=tts, orb_callback=states_seen.append, speak_enabled=False)

    with narrator.action("volume"):
        pass

    assert tts.spoken == []
    assert states_seen  # orb still moved


def test_conclude_speaks_done_and_returns_orb_to_idle():
    tts = _FakeTTS()
    states_seen = []
    narrator = ActionNarrator(tts=tts, orb_callback=states_seen.append)

    narrator.conclude()

    assert tts.spoken[0] in NARRATIVES["done"]
    assert states_seen[-1] == OrbState.IDLE


def test_narrator_works_without_tts_or_orb_callback():
    """Both are optional — nothing should require them."""
    narrator = ActionNarrator()
    with narrator.action("clipboard"):
        pass  # must not raise


def test_tts_exception_does_not_propagate():
    class _BoomTTS:
        def speak(self, text):
            raise RuntimeError("engine crashed")

    narrator = ActionNarrator(tts=_BoomTTS())
    with narrator.action("volume"):
        pass  # must not raise
