"""AmplitudeEnvelope / normalized_rms tests — synthetic WAV files only, no microphone
or TTS engine needed."""
from __future__ import annotations

import struct
import wave

import pytest

from ui.desktop.orb.audio_reactive import AmplitudeEnvelope, normalized_rms


def _write_wav(path, samples, sample_rate=16000):
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(b"".join(struct.pack("<h", s) for s in samples))


def test_normalized_rms_of_silence_is_zero():
    assert normalized_rms([0, 0, 0]) == 0.0


def test_normalized_rms_empty_is_zero():
    assert normalized_rms([]) == 0.0


def test_normalized_rms_scalar_and_single_element_array_agree():
    assert normalized_rms(500.0) == normalized_rms([500.0])


def test_normalized_rms_is_clamped_to_one():
    assert normalized_rms([32000, -32000, 32000]) == 1.0


def test_envelope_from_silent_wav_is_all_zero(tmp_path):
    path = tmp_path / "silence.wav"
    _write_wav(path, [0] * 16000)  # 1 second of silence at 16kHz

    envelope = AmplitudeEnvelope.from_wav_file(path)

    assert envelope.duration_seconds == pytest.approx(1.0, abs=0.01)
    assert all(w == 0.0 for w in envelope.windows)


def test_envelope_from_loud_wav_has_high_amplitude(tmp_path):
    path = tmp_path / "loud.wav"
    _write_wav(path, [12000, -12000] * 8000)

    envelope = AmplitudeEnvelope.from_wav_file(path)

    assert max(envelope.windows) > 0.5


def test_amplitude_at_clamps_before_start_and_after_end(tmp_path):
    path = tmp_path / "short.wav"
    _write_wav(path, [5000] * 1600)  # 0.1s

    envelope = AmplitudeEnvelope.from_wav_file(path)

    assert envelope.amplitude_at(-5.0) == envelope.amplitude_at(0.0)
    assert envelope.amplitude_at(999.0) == envelope.windows[-1]


def test_silent_factory_never_raises_and_stays_at_zero():
    envelope = AmplitudeEnvelope.silent()
    assert envelope.amplitude_at(0.0) == 0.0
    assert envelope.amplitude_at(1.0) == 0.0
    assert envelope.duration_seconds == 0.0


def test_non_16bit_wav_degrades_to_silent_envelope(tmp_path):
    path = tmp_path / "8bit.wav"
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(1)  # unsupported width
        f.setframerate(16000)
        f.writeframes(bytes([200] * 100))

    envelope = AmplitudeEnvelope.from_wav_file(path)

    assert envelope.windows == []
    assert envelope.amplitude_at(0.5) == 0.0
