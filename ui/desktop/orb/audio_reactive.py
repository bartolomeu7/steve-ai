"""Turns audio into a 0..1 amplitude the orb can react to. RMS only — no FFT. The
prompt that asked for this explicitly said RMS is enough for a good-looking reactive
orb and FFT isn't needed for this first version; RMS is also far cheaper to compute
continuously, which matters since LISTENING samples the live microphone.
"""
from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# A normal speaking voice through a typical mic rarely fills the full int16 range, so
# dividing raw RMS by this (rather than 32768) keeps the orb's motion visually lively
# instead of barely nudging at typical volumes. Tuned empirically, not physically exact.
_NORMALIZATION_CEILING = 6000.0


def normalized_rms(samples) -> float:
    """`samples`: array-like of int16 PCM values, OR a single already-computed RMS
    value (RMS of one number is just its magnitude, so this doubles as a plain
    normalizer for e.g. AudioCapture's per-chunk RMS callback). Returns 0..1."""
    arr = np.asarray(samples, dtype=np.float64)
    if arr.size == 0:
        return 0.0
    value = math.sqrt(float(np.mean(np.square(arr))))
    return max(0.0, min(1.0, value / _NORMALIZATION_CEILING))


@dataclass
class AmplitudeEnvelope:
    """A precomputed amplitude-over-time curve for a whole audio clip. Used for TTS
    playback: the audio is already fully synthesized before it starts playing, so
    there's no need to guess live — we just look up the value for however far into
    playback we are."""

    windows: list[float]
    window_seconds: float
    duration_seconds: float

    def amplitude_at(self, elapsed_seconds: float) -> float:
        if not self.windows or self.window_seconds <= 0:
            return 0.0
        index = int(elapsed_seconds / self.window_seconds)
        index = max(0, min(index, len(self.windows) - 1))
        return self.windows[index]

    @classmethod
    def silent(cls) -> "AmplitudeEnvelope":
        return cls(windows=[], window_seconds=0.05, duration_seconds=0.0)

    @classmethod
    def from_wav_file(cls, path: str | Path, window_seconds: float = 0.05) -> "AmplitudeEnvelope":
        with wave.open(str(path), "rb") as wav_file:
            n_channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            n_frames = wav_file.getnframes()
            raw = wav_file.readframes(n_frames)

        if sample_width != 2:
            # Only 16-bit PCM is supported (what Pyttsx3TTS.speak_to_file produces on
            # Windows/SAPI5); anything else degrades to a flat, silent envelope rather
            # than misinterpreting bytes as audio.
            return cls.silent()

        samples = np.frombuffer(raw, dtype=np.int16)
        if n_channels > 1 and samples.size:
            usable = samples.size - (samples.size % n_channels)
            samples = samples[:usable].reshape(-1, n_channels).mean(axis=1)

        window_frames = max(1, int(sample_rate * window_seconds)) if sample_rate else 1
        windows = [
            normalized_rms(samples[start : start + window_frames])
            for start in range(0, len(samples), window_frames)
        ]

        duration = (n_frames / float(sample_rate)) if sample_rate else 0.0
        return cls(windows=windows, window_seconds=window_seconds, duration_seconds=duration)
