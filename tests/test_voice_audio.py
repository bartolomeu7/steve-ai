from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from voice.audio import AudioCapture, AudioCaptureError


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self, frames):
        if self._chunks:
            chunk = self._chunks.pop(0)
        else:
            chunk = np.zeros((frames, 1), dtype=np.int16)
        return chunk, False


def _chunk(value: int, frames: int = 1600) -> np.ndarray:
    return np.full((frames, 1), value, dtype=np.int16)


def _install_fake_sounddevice(monkeypatch, stream=None, query_devices_result=None, query_devices_raises=None):
    fake_sd = types.SimpleNamespace()
    fake_sd.default = types.SimpleNamespace(device=[1, 4])

    def query_devices(device=None, kind=None):
        if query_devices_raises:
            raise query_devices_raises
        return query_devices_result

    fake_sd.query_devices = query_devices
    fake_sd.InputStream = lambda **kwargs: stream
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    return fake_sd


def test_is_microphone_available_true(monkeypatch):
    _install_fake_sounddevice(monkeypatch, query_devices_result={"max_input_channels": 2})
    capture = AudioCapture()
    assert capture.is_microphone_available() is True


def test_is_microphone_available_false_when_no_input_channels(monkeypatch):
    _install_fake_sounddevice(monkeypatch, query_devices_result={"max_input_channels": 0})
    capture = AudioCapture()
    assert capture.is_microphone_available() is False


def test_is_microphone_available_false_when_query_raises(monkeypatch):
    _install_fake_sounddevice(monkeypatch, query_devices_raises=OSError("no audio subsystem"))
    capture = AudioCapture()
    assert capture.is_microphone_available() is False


def test_list_input_devices_filters_input_only(monkeypatch):
    devices = [
        {"name": "Microfone", "max_input_channels": 2},
        {"name": "Alto-falantes", "max_input_channels": 0},
    ]
    _install_fake_sounddevice(monkeypatch, query_devices_result=devices)
    capture = AudioCapture()

    result = capture.list_input_devices()

    assert len(result) == 1
    assert result[0].name == "Microfone"


def test_record_until_silence_raises_when_no_speech_detected(monkeypatch):
    stream = _FakeStream([_chunk(0), _chunk(0), _chunk(0)])
    _install_fake_sounddevice(monkeypatch, stream=stream)
    capture = AudioCapture()

    with pytest.raises(AudioCaptureError):
        capture.record_until_silence(max_duration=0.3, silence_duration=0.2, min_speech_duration=0.1)


def test_record_until_silence_stops_after_speech_then_quiet(monkeypatch):
    chunks = [_chunk(3000), _chunk(0), _chunk(0)]
    stream = _FakeStream(chunks)
    _install_fake_sounddevice(monkeypatch, stream=stream)
    capture = AudioCapture()

    path = capture.record_until_silence(
        max_duration=5.0, silence_duration=0.2, min_speech_duration=0.1, silence_threshold=500
    )

    assert path.exists()
    assert path.suffix == ".wav"
    path.unlink()


def test_record_until_silence_wraps_stream_errors(monkeypatch):
    class _BoomStream:
        def __enter__(self):
            raise OSError("dispositivo ocupado")

        def __exit__(self, *exc_info):
            return False

    _install_fake_sounddevice(monkeypatch, stream=_BoomStream())
    capture = AudioCapture()

    with pytest.raises(AudioCaptureError):
        capture.record_until_silence()
