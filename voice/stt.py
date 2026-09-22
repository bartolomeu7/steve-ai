"""Speech-to-text abstraction, plus a local implementation backed by faster-whisper.

faster-whisper (CTranslate2) was chosen over plain openai-whisper specifically to avoid
pulling in a full PyTorch install (often 1-2GB+): CTranslate2 is a lean, CPU-first C++
inference engine with int8 quantization, and the whole faster-whisper + ctranslate2
stack installs at well under 100MB. Model weights are downloaded separately, on first
use, from Hugging Face — the default "base" model is ~145MB (~74MB int8-quantized) and
gives solid pt-BR accuracy; "small"/"medium" trade more disk and CPU time for accuracy
(see settings.stt_model_size).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("steve.voice.stt")


class SpeechToText(ABC):
    @abstractmethod
    def transcribe(self, audio_path: str) -> str:
        """Return the transcribed text for an audio file."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this engine is ready to transcribe."""


class UnavailableSTT(SpeechToText):
    """Placeholder used when no STT engine could be initialized."""

    def is_available(self) -> bool:
        return False

    def transcribe(self, audio_path: str) -> str:
        raise NotImplementedError(
            "Reconhecimento de voz indisponível. Use o chat por texto por enquanto."
        )


class FasterWhisperSTT(SpeechToText):
    """Offline STT backed by faster-whisper. Model load is lazy and optional —
    if the library or model can't be loaded, is_available() simply returns False."""

    def __init__(
        self,
        model_size: str = "base",
        language: str = "pt",
        device: str = "cpu",
        initial_prompt: str | None = None,
    ):
        self.model_size = model_size
        self.language = language
        self.initial_prompt = initial_prompt or (
            "Assistente Steve em portugues do Brasil. Comandos: tocar musica, YouTube, "
            "abrir navegador, volume. Nomes de artistas e musicas como Hungria Hip Hop."
        )
        self._model = None
        try:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(model_size, device=device, compute_type="int8")
        except Exception:  # pragma: no cover - depends on optional model download
            logger.warning(
                "faster-whisper indisponível (modelo '%s' não carregou); STT ficará desativado.",
                model_size,
            )
            self._model = None

    def is_available(self) -> bool:
        return self._model is not None

    def transcribe(self, audio_path: str) -> str:
        if not self.is_available():
            raise RuntimeError("Motor de STT (faster-whisper) não está carregado.")
        segments, _info = self._model.transcribe(
            audio_path,
            language=self.language,
            vad_filter=True,
            initial_prompt=self.initial_prompt,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return text
