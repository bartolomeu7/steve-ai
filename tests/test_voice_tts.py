from __future__ import annotations

import sys
from pathlib import Path

import pytest

from voice.tts import EdgeTTS, NullTTS, Pyttsx3TTS, create_tts


class _FakeVoice:
    def __init__(self, id_, name, languages=None):
        self.id = id_
        self.name = name
        self.languages = languages or []


class _FakeEngine:
    def __init__(self, voices):
        self._voices = voices
        self.properties = {}
        self.said = []
        self.saved_to_file = []

    def setProperty(self, name, value):
        self.properties[name] = value

    def getProperty(self, name):
        if name == "voices":
            return self._voices
        return self.properties.get(name)

    def say(self, text):
        self.said.append(text)

    def save_to_file(self, text, path):
        self.saved_to_file.append((text, path))
        Path(path).write_bytes(b"fake wav bytes")

    def runAndWait(self):
        pass


def test_null_tts_is_never_available():
    tts = NullTTS()
    assert tts.is_available() is False
    assert tts.speak("oi") is False


def test_pyttsx3_unavailable_when_init_fails(monkeypatch):
    class _BoomPyttsx3:
        @staticmethod
        def init():
            raise RuntimeError("SAPI5 indisponível")

    monkeypatch.setattr("pyttsx3.init", _BoomPyttsx3.init, raising=False)

    tts = Pyttsx3TTS()

    assert tts.is_available() is False
    assert tts.speak("oi") is False


def test_pyttsx3_auto_selects_pt_br_voice_and_applies_it_when_speaking(monkeypatch):
    voices = [
        _FakeVoice("en-US-voice", "Microsoft Zira Desktop - English (United States)", ["en-US"]),
        _FakeVoice("pt-BR-voice", "Microsoft Maria Desktop - Portuguese(Brazil)", ["pt-BR"]),
    ]
    engine = _FakeEngine(voices)
    monkeypatch.setattr("pyttsx3.init", lambda: engine, raising=False)

    tts = Pyttsx3TTS(rate=180, volume=0.8)
    assert tts.speak("oi") is True

    assert engine.properties["voice"] == "pt-BR-voice"
    assert engine.properties["rate"] == 180
    assert engine.properties["volume"] == 0.8


def test_pyttsx3_respects_explicit_voice_id(monkeypatch):
    voices = [_FakeVoice("pt-BR-voice", "Maria", ["pt-BR"])]
    engine = _FakeEngine(voices)
    monkeypatch.setattr("pyttsx3.init", lambda: engine, raising=False)

    tts = Pyttsx3TTS(voice_id="some-other-voice")
    assert tts.speak("oi") is True

    assert engine.properties["voice"] == "some-other-voice"


def test_pyttsx3_speak_calls_engine(monkeypatch):
    engine = _FakeEngine([])
    monkeypatch.setattr("pyttsx3.init", lambda: engine, raising=False)

    tts = Pyttsx3TTS()
    assert tts.speak("Olá, tudo bem?") is True
    assert engine.said == ["Olá, tudo bem?"]


def test_pyttsx3_speak_to_file(monkeypatch, tmp_path):
    engine = _FakeEngine([])
    monkeypatch.setattr("pyttsx3.init", lambda: engine, raising=False)

    tts = Pyttsx3TTS()
    target = tmp_path / "out.wav"
    assert tts.speak_to_file("Olá", target) is True
    assert target.exists()


def test_pyttsx3_creates_a_fresh_engine_for_every_call(monkeypatch):
    """Regression test for a real, reproduced hang: pyttsx3.init() caches one Engine
    per process, and calling runAndWait() twice on that SAME cached Engine deadlocks
    — even from a single thread with no concurrency involved at all. The fix is to
    request a new engine (bypassing the cache) on every speak()/speak_to_file() call;
    this asserts pyttsx3.init() really is invoked once per call (plus once at
    construction to probe availability/detect the voice), never reused.
    """
    calls = {"count": 0}

    def fake_init():
        calls["count"] += 1
        return _FakeEngine([])

    monkeypatch.setattr("pyttsx3.init", fake_init, raising=False)

    tts = Pyttsx3TTS()
    assert calls["count"] == 1  # construction probes availability once

    assert tts.speak("primeira chamada") is True
    assert calls["count"] == 2

    assert tts.speak("segunda chamada") is True
    assert calls["count"] == 3

    assert tts.speak_to_file("terceira chamada", Path("unused.wav")) is True
    assert calls["count"] == 4


def test_edge_tts_unavailable_when_package_not_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "edge_tts", None)  # forces `import edge_tts` to raise ImportError

    tts = EdgeTTS()

    assert tts.is_available() is False
    assert tts.speak("oi") is False


def test_edge_tts_unavailable_when_no_audio_backend(monkeypatch):
    """edge-tts installed, but neither pygame nor playsound are — EdgeTTS can
    synthesize but never actually play anything, so it must report itself as
    unavailable rather than silently synthesizing audio nobody hears."""
    fake_edge_tts = type("FakeEdgeTTSModule", (), {"Communicate": object})()
    monkeypatch.setitem(sys.modules, "edge_tts", fake_edge_tts)
    monkeypatch.setitem(sys.modules, "pygame", None)
    monkeypatch.setitem(sys.modules, "playsound", None)

    tts = EdgeTTS()

    assert tts.is_available() is False


def test_create_tts_falls_back_to_pyttsx3_when_edge_is_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "edge_tts", None)
    engine = _FakeEngine([])
    monkeypatch.setattr("pyttsx3.init", lambda: engine, raising=False)

    tts = create_tts(preferred="auto")

    assert isinstance(tts, Pyttsx3TTS)


def test_create_tts_returns_null_tts_when_nothing_is_available(monkeypatch):
    monkeypatch.setitem(sys.modules, "edge_tts", None)

    class _BoomPyttsx3:
        @staticmethod
        def init():
            raise RuntimeError("SAPI5 indisponível")

    monkeypatch.setattr("pyttsx3.init", _BoomPyttsx3.init, raising=False)

    tts = create_tts(preferred="auto")

    assert isinstance(tts, NullTTS)


def test_create_tts_pyttsx3_preference_skips_edge_entirely(monkeypatch):
    """preferred="pyttsx3" must not even try to import edge_tts."""
    engine = _FakeEngine([])
    monkeypatch.setattr("pyttsx3.init", lambda: engine, raising=False)
    import_attempted = {"edge": False}
    real_import = __import__

    def spying_import(name, *args, **kwargs):
        if name == "edge_tts":
            import_attempted["edge"] = True
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", spying_import)

    tts = create_tts(preferred="pyttsx3")

    assert isinstance(tts, Pyttsx3TTS)
    assert import_attempted["edge"] is False


def test_pyttsx3_repeated_calls_all_succeed(monkeypatch):
    """The observable symptom of the bug above: without the fix, a second speak()
    call would hang forever instead of returning. Using distinct fake engines per
    call (as the real fix does) keeps this fast and deterministic in a unit test."""

    def fake_init():
        return _FakeEngine([])

    monkeypatch.setattr("pyttsx3.init", fake_init, raising=False)

    tts = Pyttsx3TTS()
    for i in range(5):
        assert tts.speak(f"mensagem {i}") is True
