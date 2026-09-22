"""OrbReactiveTTS: makes the orb pulse to Steve's own voice while he's speaking.

pyttsx3/SAPI5 has no live amplitude callback during playback, so this uses the
standard envelope-following trick instead of real-time capture: synthesize to a WAV
first (Pyttsx3TTS.speak_to_file, already existed), precompute its RMS amplitude curve
(ui.desktop.orb.audio_reactive.AmplitudeEnvelope), then play that WAV asynchronously
(stdlib `winsound`) while sampling the precomputed curve against elapsed playback time.
This is genuinely driven by what's being said (loud syllables move the orb more than
quiet ones) — not a random or fixed-tempo animation — while staying cheap: no live
audio capture, no FFT, just an array lookup per tick.

Implements the same TextToSpeech contract as Pyttsx3TTS (speak() still blocks until
done), so nothing downstream (VoiceService, the CLI) needs to know this exists.
"""
from __future__ import annotations

import logging
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

from ui.desktop.orb.audio_reactive import AmplitudeEnvelope
from voice.tts import TextToSpeech

logger = logging.getLogger("steve.ui.desktop.orb.reactive_tts")

SAMPLE_INTERVAL_S = 0.03


class OrbReactiveTTS(TextToSpeech):
    def __init__(
        self,
        inner: TextToSpeech,
        on_amplitude: Callable[[float], None] | None = None,
    ):
        """`on_amplitude` is called (possibly many times) from whatever thread calls
        speak() — the caller is responsible for marshaling it to the UI thread safely
        (e.g. via BackgroundRunner.post), exactly like the on_tool_call/on_state hooks
        elsewhere in the desktop UI."""
        self._inner = inner
        self._on_amplitude = on_amplitude
        self._winsound_available = sys.platform == "win32"

    def is_available(self) -> bool:
        return self._inner.is_available()

    def speak(self, text: str) -> bool:
        if not self.is_available():
            return False

        if not self._winsound_available or not hasattr(self._inner, "speak_to_file"):
            return self._inner.speak(text)

        temp_path = Path(tempfile.mktemp(suffix=".wav", prefix="steve_orb_"))
        try:
            synthesized = self._inner.speak_to_file(text, temp_path)  # type: ignore[attr-defined]
        except Exception:
            logger.exception("Falha ao sintetizar fala para o orb; usando fala simples.")
            synthesized = False

        if not synthesized or not temp_path.exists():
            return self._inner.speak(text)

        try:
            # speak_to_file() is only guaranteed to produce real PCM WAV for engines like
            # Pyttsx3TTS/SAPI5 — EdgeTTS (voice/tts.py) always writes MP3 bytes regardless
            # of the ".wav" extension used above, and winsound.PlaySound cannot play MP3
            # at all. Rather than special-case every TTS implementation here, treat
            # "can't be parsed/played as WAV" the same as "synthesis failed": fall back to
            # the inner engine's own speak(), which knows how to play its own format
            # correctly (e.g. EdgeTTS uses pygame/playsound for MP3). The orb just won't
            # get amplitude-reactive pulsing for that engine — it still speaks correctly.
            try:
                envelope = AmplitudeEnvelope.from_wav_file(temp_path)
            except Exception:
                logger.info(
                    "Arquivo gerado por %s não é WAV reproduzível (esperado para engines "
                    "não-SAPI5, ex.: EdgeTTS); tocando sem reatividade do orb.",
                    type(self._inner).__name__,
                )
                return self._inner.speak(text)

            return self._play_with_amplitude(temp_path, envelope)
        finally:
            temp_path.unlink(missing_ok=True)

    def _play_with_amplitude(self, path: Path, envelope: AmplitudeEnvelope) -> bool:
        import winsound

        try:
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except RuntimeError:
            logger.exception("Falha ao reproduzir áudio via winsound; usando fala simples.")
            return self._inner.speak_to_file is not None and self._fallback_play(path)

        start = time.perf_counter()
        try:
            while True:
                elapsed = time.perf_counter() - start
                if elapsed >= envelope.duration_seconds:
                    break
                if self._on_amplitude:
                    self._on_amplitude(envelope.amplitude_at(elapsed))
                time.sleep(SAMPLE_INTERVAL_S)
        finally:
            if self._on_amplitude:
                self._on_amplitude(0.0)
            winsound.PlaySound(None, winsound.SND_PURGE)

        return True

    def _fallback_play(self, path: Path) -> bool:  # pragma: no cover - last-resort path
        try:
            import winsound

            winsound.PlaySound(str(path), winsound.SND_FILENAME)
            return True
        except Exception:
            logger.exception("Reprodução de áudio indisponível.")
            return False
