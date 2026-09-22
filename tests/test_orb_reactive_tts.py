"""OrbReactiveTTS tests. `winsound` is faked (no real audio device I/O), and the inner
TTS synthesizes a real (but tiny, ~0.1s) WAV so the actual envelope/timing code path
runs for real — just without waiting for real speech playback."""
from __future__ import annotations

import struct
import sys
import wave
from pathlib import Path

import pytest

from ui.desktop.orb.reactive_tts import OrbReactiveTTS


class _FakeInnerTTS:
    def __init__(self, available: bool = True, samples=None):
        self._available = available
        self._samples = samples if samples is not None else [6000, -6000] * 800  # ~0.1s @16kHz
        self.spoken: list[str] = []

    def is_available(self) -> bool:
        return self._available

    def speak(self, text: str) -> bool:
        self.spoken.append(text)
        return True

    def speak_to_file(self, text: str, path) -> bool:
        with wave.open(str(path), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(16000)
            f.writeframes(b"".join(struct.pack("<h", s) for s in self._samples))
        return True


@pytest.fixture(autouse=True)
def fake_winsound(monkeypatch):
    calls = []
    fake_module = type(
        "FakeWinsound",
        (),
        {
            "SND_FILENAME": 1,
            "SND_ASYNC": 2,
            "SND_PURGE": 4,
            "PlaySound": staticmethod(lambda *a, **k: calls.append(a)),
        },
    )
    monkeypatch.setitem(sys.modules, "winsound", fake_module)
    return calls


def test_speak_returns_false_when_inner_unavailable():
    tts = OrbReactiveTTS(_FakeInnerTTS(available=False))
    assert tts.speak("oi") is False


def test_speak_plays_and_reports_amplitude(fake_winsound):
    amplitudes = []
    tts = OrbReactiveTTS(_FakeInnerTTS(), on_amplitude=amplitudes.append)

    result = tts.speak("Steve, como está a CPU?")

    assert result is True
    assert len(amplitudes) > 0
    assert amplitudes[-1] == 0.0  # reset to silence once playback ends
    assert any(call for call in fake_winsound)  # PlaySound was actually invoked


def test_speak_falls_back_to_plain_speak_when_synthesis_fails(monkeypatch):
    inner = _FakeInnerTTS()
    monkeypatch.setattr(inner, "speak_to_file", lambda text, path: False)
    tts = OrbReactiveTTS(inner)

    assert tts.speak("oi") is True
    assert inner.spoken == ["oi"]


def test_speak_falls_back_when_inner_has_no_speak_to_file():
    class _PlainTTS:
        def is_available(self) -> bool:
            return True

        def speak(self, text: str) -> bool:
            self.spoken_text = text
            return True

    plain = _PlainTTS()
    tts = OrbReactiveTTS(plain)

    assert tts.speak("oi") is True
    assert plain.spoken_text == "oi"


def test_is_available_reflects_inner_engine():
    assert OrbReactiveTTS(_FakeInnerTTS(available=False)).is_available() is False
    assert OrbReactiveTTS(_FakeInnerTTS(available=True)).is_available() is True


def test_speak_works_without_an_on_amplitude_callback():
    """The hook is optional — nothing should require a caller to supply it."""
    tts = OrbReactiveTTS(_FakeInnerTTS())
    assert tts.speak("oi") is True


def test_speak_falls_back_to_plain_speak_when_inner_writes_a_non_wav_file(tmp_path, monkeypatch):
    """Regression test: EdgeTTS (voice/tts.py) always writes MP3 bytes from
    speak_to_file(), regardless of the ".wav" extension OrbReactiveTTS asks for —
    winsound.PlaySound can't play MP3 at all, and AmplitudeEnvelope.from_wav_file()
    can't parse it as WAV either. Before the fix, this raised out of speak() entirely
    (a real crash on every orb-reactive turn once EdgeTTS was wired in as the active
    TTS); the fix treats "not really WAV" the same as "synthesis failed" and falls back
    to the inner engine's own speak(), which knows how to play its own format."""
    fake_temp_path = tmp_path / "steve_orb_fake.wav"
    monkeypatch.setattr("ui.desktop.orb.reactive_tts.tempfile.mktemp", lambda **kwargs: str(fake_temp_path))

    class _FakeEdgeLikeInnerTTS:
        def is_available(self) -> bool:
            return True

        def speak(self, text: str) -> bool:
            self.spoken = text
            return True

        def speak_to_file(self, text: str, path) -> bool:
            # Not a WAV file at all — stand-in for EdgeTTS's real MP3 output.
            Path(path).write_bytes(b"ID3\x03\x00\x00\x00not actually a wav file")
            return True

    inner = _FakeEdgeLikeInnerTTS()
    tts = OrbReactiveTTS(inner)

    result = tts.speak("Olá, tudo bem?")

    assert result is True
    assert inner.spoken == "Olá, tudo bem?"  # fell back to the inner engine's real player
    assert not fake_temp_path.exists()  # the bogus temp file was still cleaned up
