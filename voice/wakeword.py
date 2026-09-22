"""Wake-word detection for continuous listening ('Steve').

Uses openWakeWord when a custom ONNX model is present at data/wakewords/steve.onnx.
Otherwise falls back to a lightweight energy + Whisper phrase spotter so 'Steve'
works out of the box without a paid API or a long training job.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
import time
import wave
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

logger = logging.getLogger("steve.voice.wakeword")

SAMPLE_RATE = 16_000
WAKE_EVENT = "voice.wake_detected"

_STEVE_RE = re.compile(
    r"\b(steve|estiv[ei]|esteve|st[ií]ve|stevi)\b",
    re.IGNORECASE,
)


class WakeWordDetector(ABC):
    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    def process_audio(self, audio_f32: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bool:
        """Feed a chunk of mono float32 audio in [-1, 1]. Return True on wake."""
        ...

    def reset(self) -> None:
        return None


class UnavailableWakeWord(WakeWordDetector):
    def is_available(self) -> bool:
        return False

    def process_audio(self, audio_f32: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bool:
        return False


class OpenWakeWordDetector(WakeWordDetector):
    """openWakeWord wrapper — requires a custom steve.onnx (or other) model path."""

    def __init__(self, model_paths: list[str], threshold: float = 0.5):
        self.threshold = threshold
        self._model = None
        try:
            from openwakeword.model import Model

            self._model = Model(wakeword_models=model_paths, inference_framework="onnx")
            logger.info("openWakeWord carregado (%d modelo(s)).", len(model_paths))
        except Exception:
            logger.exception("Falha ao carregar openWakeWord.")
            self._model = None

    def is_available(self) -> bool:
        return self._model is not None

    def process_audio(self, audio_f32: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bool:
        if self._model is None:
            return False
        pcm = (np.clip(audio_f32, -1, 1) * 32767.0).astype(np.int16)
        try:
            scores = self._model.predict(pcm)
        except Exception:
            logger.debug("openWakeWord predict falhou", exc_info=True)
            return False
        if not scores:
            return False
        best = max(float(v) for v in scores.values())
        return best >= self.threshold

    def reset(self) -> None:
        if self._model is not None and hasattr(self._model, "reset"):
            try:
                self._model.reset()
            except Exception:
                pass


class WhisperPhraseWakeDetector(WakeWordDetector):
    """Spot the wake phrase with short Whisper passes on speech bursts (free, no API)."""

    def __init__(
        self,
        phrase: str = "steve",
        model_size: str = "tiny",
        energy_threshold: float = 0.015,
        window_seconds: float = 1.4,
        cooldown_seconds: float = 2.5,
    ):
        self.phrase = phrase.lower().strip() or "steve"
        self.energy_threshold = energy_threshold
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self._buffer = np.zeros(0, dtype=np.float32)
        self._last_fire = 0.0
        self._model = None
        self._model_size = model_size
        self._busy = False

    def is_available(self) -> bool:
        return True

    def _ensure_model(self) -> bool:
        if self._model is not None:
            return True
        try:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self._model_size, device="cpu", compute_type="int8")
            logger.info("Wake phrase spotter Whisper '%s' pronto.", self._model_size)
            return True
        except Exception:
            logger.exception("Nao foi possivel carregar Whisper para wake word.")
            return False

    def process_audio(self, audio_f32: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bool:
        if self._busy:
            return False
        now = time.monotonic()
        if now - self._last_fire < self.cooldown_seconds:
            self._buffer = np.zeros(0, dtype=np.float32)
            return False

        chunk = np.asarray(audio_f32, dtype=np.float32).reshape(-1)
        if chunk.size == 0:
            return False
        rms = float(np.sqrt(np.mean(np.square(chunk))))
        if rms < self.energy_threshold:
            if self._buffer.size > 0:
                self._buffer = self._buffer[len(chunk) :]
            return False

        self._buffer = np.concatenate([self._buffer, chunk])
        need = int(self.window_seconds * sample_rate)
        if self._buffer.size < need:
            return False

        window = self._buffer[-need:]
        self._buffer = np.zeros(0, dtype=np.float32)
        return self._spot(window, sample_rate)

    def _spot(self, window: np.ndarray, sample_rate: int) -> bool:
        if not self._ensure_model():
            return False
        self._busy = True
        path = None
        try:
            pcm = (np.clip(window, -1, 1) * 32767.0).astype(np.int16)
            fd, name = tempfile.mkstemp(suffix=".wav")
            path = Path(name)
            os.close(fd)
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(pcm.tobytes())
            segments, _ = self._model.transcribe(
                str(path),
                language="pt",
                vad_filter=False,
                initial_prompt="Steve. Comando para o assistente Steve.",
            )
            text = " ".join(s.text.strip() for s in segments).strip().lower()
            logger.debug("Wake spotter ouviu: %r", text)
            hit = bool(_STEVE_RE.search(text)) or self.phrase in text
            if hit:
                self._last_fire = time.monotonic()
            return hit
        except Exception:
            logger.exception("Falha no spotter de wake word.")
            return False
        finally:
            self._busy = False
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    def reset(self) -> None:
        self._buffer = np.zeros(0, dtype=np.float32)


def build_wake_detector(
    phrase: str = "steve",
    custom_model_path: Path | None = None,
) -> WakeWordDetector:
    """Prefer openWakeWord custom model; else Whisper phrase spotter for 'Steve'."""
    model_path = custom_model_path
    if model_path is None:
        model_path = Path(__file__).resolve().parents[1] / "data" / "wakewords" / "steve.onnx"
    if model_path.is_file():
        det = OpenWakeWordDetector([str(model_path)])
        if det.is_available():
            return det
        logger.warning("steve.onnx presente mas openWakeWord falhou; usando spotter Whisper.")
    logger.info(
        "Usando spotter Whisper para wake '%s' (coloque data/wakewords/steve.onnx para openWakeWord puro).",
        phrase,
    )
    return WhisperPhraseWakeDetector(phrase=phrase, model_size="tiny")
