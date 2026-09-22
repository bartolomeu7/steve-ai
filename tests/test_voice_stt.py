from __future__ import annotations

import pytest

from voice.stt import FasterWhisperSTT, UnavailableSTT


def test_unavailable_stt_reports_unavailable():
    stt = UnavailableSTT()
    assert stt.is_available() is False


def test_unavailable_stt_raises_on_transcribe():
    stt = UnavailableSTT()
    with pytest.raises(NotImplementedError):
        stt.transcribe("audio.wav")


def test_faster_whisper_stt_unavailable_when_model_fails_to_load(monkeypatch):
    class _BoomWhisperModel:
        def __init__(self, *args, **kwargs):
            raise OSError("modelo não pôde ser baixado/carregado")

    monkeypatch.setattr("faster_whisper.WhisperModel", _BoomWhisperModel, raising=False)

    stt = FasterWhisperSTT(model_size="base")

    assert stt.is_available() is False
    with pytest.raises(RuntimeError):
        stt.transcribe("audio.wav")


def test_faster_whisper_stt_transcribes_using_loaded_model(monkeypatch):
    class _Segment:
        def __init__(self, text):
            self.text = text

    class _FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, audio_path, **kwargs):
            return [_Segment(" Olá "), _Segment("Steve ")], object()

    monkeypatch.setattr("faster_whisper.WhisperModel", _FakeWhisperModel, raising=False)

    stt = FasterWhisperSTT(model_size="base", language="pt")

    assert stt.is_available() is True
    assert stt.transcribe("audio.wav") == "Olá Steve"
