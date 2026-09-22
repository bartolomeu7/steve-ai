"""Microphone capture with simple energy-based endpointing (detect speech start/end).

Uses sounddevice (PortAudio bindings) — a small, mature library with no ML weight.
Recording is written to a temporary 16 kHz mono WAV file, which is what STT engines
(faster-whisper included) expect as input.
"""
from __future__ import annotations

import logging
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger("steve.voice.audio")

SAMPLE_RATE = 16_000
CHANNELS = 1
DTYPE = "int16"


class AudioCaptureError(Exception):
    """Raised when the microphone can't be accessed or recording otherwise fails."""


@dataclass
class AudioDevice:
    index: int
    name: str
    max_input_channels: int


class AudioCapture:
    """Records from a microphone until the user stops talking, or a safety cap is hit."""

    def __init__(self, input_device: str | int | None = None):
        self.input_device = input_device

    def list_input_devices(self) -> list[AudioDevice]:
        try:
            import sounddevice as sd

            devices = sd.query_devices()
        except Exception as exc:  # pragma: no cover - depends on host audio subsystem
            logger.warning("Não foi possível listar dispositivos de áudio: %s", exc)
            return []
        return [
            AudioDevice(index=i, name=d["name"], max_input_channels=d["max_input_channels"])
            for i, d in enumerate(devices)
            if d["max_input_channels"] > 0
        ]

    def is_microphone_available(self) -> bool:
        try:
            import sounddevice as sd

            device_info = sd.query_devices(self.input_device, "input")
            return device_info["max_input_channels"] > 0
        except Exception as exc:  # pragma: no cover - depends on host audio subsystem
            logger.warning("Microfone indisponível: %s", exc)
            return False

    def record_until_silence(
        self,
        max_duration: float = 15.0,
        silence_duration: float = 1.2,
        silence_threshold: float = 500.0,
        min_speech_duration: float = 0.3,
        on_amplitude: Callable[[float], None] | None = None,
    ) -> Path:
        """Records from the mic until `silence_duration` seconds of quiet follow speech
        (or `max_duration` is reached), and returns the path to a temp WAV file.

        `silence_threshold` is a RMS amplitude cutoff on 16-bit samples (0-32767); the
        default works reasonably for typical mic gain and a normal speaking voice in a
        quiet-ish room, but is intentionally exposed for tuning per machine.

        `on_amplitude`, if given, is called once per ~100ms chunk with that chunk's raw
        RMS (same units as `silence_threshold`) — purely observational, for a UI to
        visualize live input level; it never affects the endpointing decision above.
        Called synchronously from whatever thread calls this method.
        """
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError as exc:
            raise AudioCaptureError(
                "Dependências de áudio não instaladas (sounddevice/numpy)."
            ) from exc

        chunk_seconds = 0.1
        chunk_frames = int(SAMPLE_RATE * chunk_seconds)
        silence_chunks_needed = max(1, int(silence_duration / chunk_seconds))
        min_speech_chunks = max(1, int(min_speech_duration / chunk_seconds))

        frames: list = []
        speech_started = False
        speech_chunk_count = 0
        silence_run = 0

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                device=self.input_device,
                blocksize=chunk_frames,
            ) as stream:
                total_chunks = int(max_duration / chunk_seconds)
                for _ in range(total_chunks):
                    chunk, overflowed = stream.read(chunk_frames)
                    if overflowed:
                        logger.debug("Buffer de áudio estourou um chunk.")
                    frames.append(chunk.copy())

                    rms = float(np.sqrt(np.mean(np.square(chunk.astype(np.float64)))))
                    if on_amplitude:
                        on_amplitude(rms)
                    if rms >= silence_threshold:
                        speech_started = True
                        speech_chunk_count += 1
                        silence_run = 0
                    elif speech_started:
                        silence_run += 1
                        if silence_run >= silence_chunks_needed and speech_chunk_count >= min_speech_chunks:
                            break
        except Exception as exc:  # pragma: no cover - depends on host audio subsystem
            raise AudioCaptureError(f"Não consegui acessar o microfone: {exc}") from exc

        if not speech_started:
            raise AudioCaptureError("Nenhuma fala detectada (áudio permaneceu em silêncio).")

        audio = np.concatenate(frames, axis=0)
        return self._write_wav(audio)

    @staticmethod
    def _write_wav(audio) -> Path:
        tmp_file = tempfile.NamedTemporaryFile(prefix="steve_voice_", suffix=".wav", delete=False)
        path = Path(tmp_file.name)
        tmp_file.close()
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(CHANNELS)
            wav_file.setsampwidth(2)  # int16
            wav_file.setframerate(SAMPLE_RATE)
            wav_file.writeframes(audio.tobytes())
        return path
