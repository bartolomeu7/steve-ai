"""Text-to-speech abstraction.

Priority order (see create_tts()):
1. EdgeTTS  -> vozes neurais da Microsoft (melhor qualidade, gratuito, requer internet)
2. pyttsx3  -> fallback offline (SAPI5 no Windows) — o motor original do Steve
3. NullTTS  -> quando nada estiver disponível

EdgeTTS oferece vozes em português brasileiro de alta qualidade (ex:
pt-BR-AntonioNeural, pt-BR-FranciscaNeural), com som bem mais natural que o pyttsx3, mas
depende de internet e de um pacote/serviço de terceiros (Microsoft) — por isso continua
sendo uma camada acima do pyttsx3, nunca uma substituição: se a rede cair ou o pacote não
estiver instalado, o Steve volta a falar offline em vez de ficar mudo.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger("steve.voice.tts")


class TextToSpeech(ABC):
    @abstractmethod
    def speak(self, text: str) -> bool:
        """Speak the given text. Return True if playback succeeded."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this engine is ready to speak."""

    def speak_to_file(self, text: str, path: Path) -> bool:
        """Optional: synthesize to a file. Default implementation returns False —
        callers (e.g. ui/desktop/orb/reactive_tts.py's amplitude-reactive playback) must
        check the return value and fall back to speak() rather than assume this exists
        or produces a WAV file specifically (EdgeTTS below produces MP3)."""
        return False


class NullTTS(TextToSpeech):
    """No-op fallback used when no speech engine is installed."""

    def is_available(self) -> bool:
        return False

    def speak(self, text: str) -> bool:
        logger.debug("TTS indisponível, ignorando fala: %s", text[:80])
        return False


# ---------------------------------------------------------------------------
# EdgeTTS — vozes neurais de alta qualidade (opcional, requer internet)
# ---------------------------------------------------------------------------


class EdgeTTS(TextToSpeech):
    """High-quality neural TTS using Microsoft Edge voices (edge-tts).

    Vozes recomendadas em pt-BR:
      - pt-BR-AntonioNeural   (masculina, natural)
      - pt-BR-FranciscaNeural (feminina, natural)
      - pt-BR-ThalitaMultilingualNeural (multilíngue)

    Requer: pip install edge-tts. Também precisa de um backend de reprodução de áudio
    (pygame, preferencial — playsound como alternativa) porque o serviço devolve MP3, não
    WAV. IMPORTANTE: por isso `speak_to_file()` desta classe NUNCA deve ser tratado como
    produtor de WAV por quem o chama (ver ui/desktop/orb/reactive_tts.py).
    """

    DEFAULT_VOICE = "pt-BR-AntonioNeural"

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
    ):
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self.pitch = pitch
        self._available = False
        self._edge_tts = None
        self._play_backend = None

        try:
            import edge_tts

            self._edge_tts = edge_tts
            self._available = True
            logger.info("EdgeTTS pronto (voz: %s).", self.voice)
        except ImportError:
            logger.info("edge-tts não instalado (pip install edge-tts); EdgeTTS ficará indisponível.")
            return

        try:
            import pygame

            pygame.mixer.init()
            self._play_backend = "pygame"
        except Exception:
            try:
                from playsound import playsound  # noqa: F401 - only probing availability

                self._play_backend = "playsound"
            except Exception:
                logger.warning(
                    "Nenhum player de áudio encontrado (pygame ou playsound); EdgeTTS sintetizaria mas não "
                    "conseguiria tocar o áudio — tratando como indisponível."
                )
                self._available = False

    def is_available(self) -> bool:
        return self._available

    def speak(self, text: str) -> bool:
        if not self._available or not text.strip():
            return False

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp_path = Path(tmp.name)

            if not self._synthesize(text, tmp_path):
                return False
            return self._play(tmp_path)
        except Exception:
            logger.exception("Falha no EdgeTTS.speak().")
            return False
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

    def speak_to_file(self, text: str, path: Path) -> bool:
        """Synthesizes to `path` as MP3 (edge-tts's only output format), regardless of
        the extension given — never assume this is a WAV file, unlike Pyttsx3TTS's own
        speak_to_file()."""
        if not self._available:
            return False
        return self._synthesize(text, path)

    def _synthesize(self, text: str, path: Path) -> bool:
        try:
            communicate = self._edge_tts.Communicate(
                text, self.voice, rate=self.rate, volume=self.volume, pitch=self.pitch
            )
            asyncio.run(communicate.save(str(path)))
            return path.exists() and path.stat().st_size > 0
        except Exception:
            logger.exception("Erro ao sintetizar com EdgeTTS.")
            return False

    def _play(self, path: Path) -> bool:
        if self._play_backend == "pygame":
            try:
                import pygame

                pygame.mixer.music.load(str(path))
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    pygame.time.Clock().tick(10)
                return True
            except Exception:
                logger.exception("Erro ao tocar áudio com pygame.")
                return False
        elif self._play_backend == "playsound":
            try:
                from playsound import playsound

                playsound(str(path))
                return True
            except Exception:
                logger.exception("Erro ao tocar áudio com playsound.")
                return False
        logger.warning("Sem backend de áudio para reproduzir o arquivo gerado pelo EdgeTTS.")
        return False


# ---------------------------------------------------------------------------
# pyttsx3 — motor offline original do Steve (fallback confiável)
# ---------------------------------------------------------------------------


class Pyttsx3TTS(TextToSpeech):
    """Offline TTS backed by pyttsx3 (SAPI5 on Windows). Import is lazy and optional.

    Auto-picks a pt-BR voice if one is installed and none was explicitly configured —
    on Windows this is usually "Microsoft Maria" once the pt-BR language pack is
    present. Falls back to the system default voice otherwise (never fails silently:
    a warning is logged either way, but the engine remains usable).

    IMPORTANT: a fresh pyttsx3 Engine is created for every speak()/speak_to_file()
    call, instead of reusing one Engine for this object's lifetime. Reproduced by
    direct testing: pyttsx3.init() caches a single Engine per driver name for the
    whole process, and running runAndWait() a second time on that same cached Engine
    hangs indefinitely — even from the very same thread that made the first call.
    This is a known pyttsx3/SAPI5 quirk, not something specific to Steve. Voice
    selection is resolved once (via a throwaway probe engine at construction) so
    repeated calls don't re-scan installed voices every time.
    """

    def __init__(
        self,
        voice_id: str | None = None,
        rate: int = 175,
        volume: float = 1.0,
        language_hint: str = "pt-br",
    ):
        self._rate = rate
        self._volume = max(0.0, min(1.0, volume))
        self._voice_id = voice_id
        self._available = False

        try:
            probe = self._new_engine()
        except Exception:  # pragma: no cover - depends on optional system dependency
            logger.warning("pyttsx3 indisponível; TTS ficará desativado.")
            return

        self._available = True
        if not self._voice_id:
            self._voice_id = self._detect_voice(probe, language_hint)

    @staticmethod
    def _new_engine():
        import pyttsx3

        try:
            pyttsx3._activeEngines.pop(None, None)  # noqa: SLF001 - force a real new Engine
        except AttributeError:  # pragma: no cover - only if pyttsx3 changes internals
            pass
        return pyttsx3.init()

    @staticmethod
    def _detect_voice(engine, language_hint: str) -> str | None:
        for voice in engine.getProperty("voices"):
            languages = " ".join(str(lang) for lang in getattr(voice, "languages", []) or [])
            haystack = f"{voice.id} {voice.name} {languages}".lower()
            if language_hint in haystack or "pt-br" in haystack or "brazil" in haystack:
                return voice.id
        logger.info("Nenhuma voz '%s' encontrada; usando a voz padrão do sistema.", language_hint)
        return None

    def is_available(self) -> bool:
        return self._available

    def speak(self, text: str) -> bool:
        return self._run(lambda engine: engine.say(text))

    def speak_to_file(self, text: str, path: Path) -> bool:
        """Synthesizes speech straight to a real WAV file — used for automated pipeline
        tests and orb-reactive playback that need real (if synthetic) audio input."""
        if not self._run(lambda engine: engine.save_to_file(text, str(path))):
            return False
        return path.exists()

    def _run(self, action) -> bool:
        if not self._available:
            return False
        try:
            engine = self._new_engine()
            engine.setProperty("rate", self._rate)
            engine.setProperty("volume", self._volume)
            if self._voice_id:
                engine.setProperty("voice", self._voice_id)
            action(engine)
            engine.runAndWait()
            return True
        except Exception:  # pragma: no cover - depends on optional system dependency
            logger.exception("Falha ao sintetizar fala.")
            return False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_tts(
    preferred: str = "auto",
    edge_voice: str = EdgeTTS.DEFAULT_VOICE,
    pyttsx3_voice_id: str | None = None,
    rate: int = 175,
    volume: float = 1.0,
) -> TextToSpeech:
    """Builds the best available TTS engine.

    preferred:
      - "edge"/"auto" -> tries EdgeTTS first, falls back to pyttsx3 if it isn't
        installed/available (no internet, missing dependency, no audio backend)
      - "pyttsx3"      -> forces the offline engine, skipping EdgeTTS entirely
    """
    if preferred in ("edge", "auto"):
        tts = EdgeTTS(voice=edge_voice)
        if tts.is_available():
            return tts
        logger.info("EdgeTTS indisponível, usando pyttsx3 (fallback offline)...")

    tts = Pyttsx3TTS(voice_id=pyttsx3_voice_id, rate=rate, volume=volume)
    if tts.is_available():
        return tts

    logger.warning("Nenhum motor de TTS disponível (nem EdgeTTS, nem pyttsx3).")
    return NullTTS()
