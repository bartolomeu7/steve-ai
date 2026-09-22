"""VoiceService: microphone -> STT -> Orchestrator -> TTS.

This does NOT duplicate any conversation logic — it captures audio, turns it into
text, and hands that text to Orchestrator.handle_message(), exactly like ui/cli.py
does for typed input. Same brain, different ears and mouth.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from core.events import EventBus
from core.orchestrator import TOOL_COMPLETED, TOOL_STARTED, Orchestrator
from tools.active_window import get_active_window_title
from voice.action_narrator import ActionNarrator
from voice.audio import AudioCapture, AudioCaptureError
from voice.stt import SpeechToText
from voice.streaming_speaker import StreamingSpeaker
from voice.tts import TextToSpeech

logger = logging.getLogger("steve.voice.service")

MIC_ERROR_MESSAGE = "Não consegui acessar o microfone."
NO_SPEECH_MESSAGE = "Não consegui entender o áudio."
STT_UNAVAILABLE_MESSAGE = "O serviço de reconhecimento de voz está indisponível."
EMPTY_REPLY_MESSAGE = "Não obtive uma resposta."

LISTENING_STATE_MESSAGE = "🎙️ Ouvindo..."
PROCESSING_STATE_MESSAGE = "🧠 Processando..."
SPEAKING_STATE_MESSAGE = "🔊 Respondendo..."

#: Live Activity's "voice" category (V1.4) — published on the same state changes that
#: already drive the orb via on_state, so no new state machine, just an extra observer.
VOICE_STATE_CHANGED = "voice.state_changed"


@dataclass
class VoiceTurnResult:
    success: bool
    transcript: str | None = None
    reply: str | None = None
    error: str | None = None
    timings: dict[str, float] = field(default_factory=dict)


class _LiveTTSProxy:
    """Forwards .speak() to `service.tts` at call time, not at construction time — see
    VoiceService.__init__'s comment on why ActionNarrator can't just capture `self.tts`
    directly."""

    def __init__(self, service: "VoiceService"):
        self._service = service

    def speak(self, text: str) -> bool:
        return self._service.tts.speak(text)


class VoiceService:
    def __init__(
        self,
        audio_capture: AudioCapture,
        stt: SpeechToText,
        orchestrator: Orchestrator,
        tts: TextToSpeech,
        event_bus: EventBus | None = None,
        narrator: ActionNarrator | None = None,
    ):
        self.audio_capture = audio_capture
        self.stt = stt
        self.orchestrator = orchestrator
        self.tts = tts
        self.event_bus = event_bus
        # speak_fn-equivalent below reads self.tts through a lambda-like property, not
        # a captured reference — main.py's SteveApp swaps this attribute for an
        # OrbReactiveTTS wrapper *after* VoiceService is constructed (see
        # ui/desktop/app.py::_wrap_tts_for_orb); binding `tts` at construction time
        # would silently keep narrating through the pre-wrap engine forever. Orb state
        # isn't wired here (orb_callback=None) — VoiceService has no orb reference of
        # its own; the desktop app already drives orb state from on_tool_call/on_state
        # (see ui/desktop/controller.py), so the narrator's orb callback would just be
        # a second, redundant path to the same widget.
        self.narrator = narrator if narrator is not None else ActionNarrator(
            tts=_LiveTTSProxy(self), get_active_window=get_active_window_title
        )

    def listen_and_respond(
        self,
        on_state: Callable[[str], None] | None = None,
        on_tool_call: Callable[[str], None] | None = None,
        on_amplitude: Callable[[float], None] | None = None,
    ) -> VoiceTurnResult:
        def notify(message: str) -> None:
            if on_state:
                on_state(message)
            if self.event_bus is not None:
                self.event_bus.publish(VOICE_STATE_CHANGED, {"state": message})

        timings: dict[str, float] = {}

        if not self.stt.is_available():
            return VoiceTurnResult(success=False, error=STT_UNAVAILABLE_MESSAGE, timings=timings)

        notify(LISTENING_STATE_MESSAGE)
        audio_path, capture_error = self._capture(timings, on_amplitude)
        if capture_error is not None:
            return VoiceTurnResult(success=False, error=capture_error, timings=timings)

        notify(PROCESSING_STATE_MESSAGE)
        transcript, transcribe_error = self._transcribe(audio_path, timings)
        if transcribe_error is not None:
            return VoiceTurnResult(success=False, error=transcribe_error, timings=timings)

        with self._narrator_active():
            reply = self._ask_orchestrator(transcript, timings, on_tool_call)
        if not reply or not reply.strip():
            return VoiceTurnResult(
                success=False, transcript=transcript, error=EMPTY_REPLY_MESSAGE, timings=timings
            )

        notify(SPEAKING_STATE_MESSAGE)
        self._speak(reply, timings)

        timings["total_s"] = round(sum(timings.values()), 3)
        return VoiceTurnResult(success=True, transcript=transcript, reply=reply, timings=timings)

    def listen_and_respond_streaming(
        self,
        on_state: Callable[[str], None] | None = None,
        on_tool_call: Callable[[str], None] | None = None,
        on_amplitude: Callable[[float], None] | None = None,
    ) -> VoiceTurnResult:
        """Same capture/STT steps as listen_and_respond(), but speaks the reply
        sentence-by-sentence as Orchestrator.handle_message_stream() generates it,
        instead of waiting for the complete text — see that method's docstring
        (core/orchestrator.py) for the streaming design and its known trade-off. Opt-in
        via Settings.voice_streaming_enabled; callers choose between this and
        listen_and_respond() (see ui/desktop/controller.py), never both at once."""

        def notify(message: str) -> None:
            if on_state:
                on_state(message)
            if self.event_bus is not None:
                self.event_bus.publish(VOICE_STATE_CHANGED, {"state": message})

        timings: dict[str, float] = {}

        if not self.stt.is_available():
            return VoiceTurnResult(success=False, error=STT_UNAVAILABLE_MESSAGE, timings=timings)

        notify(LISTENING_STATE_MESSAGE)
        audio_path, capture_error = self._capture(timings, on_amplitude)
        if capture_error is not None:
            return VoiceTurnResult(success=False, error=capture_error, timings=timings)

        notify(PROCESSING_STATE_MESSAGE)
        transcript, transcribe_error = self._transcribe(audio_path, timings)
        if transcribe_error is not None:
            return VoiceTurnResult(success=False, error=transcribe_error, timings=timings)

        notify(SPEAKING_STATE_MESSAGE)
        start = time.perf_counter()
        with self._narrator_active():
            token_stream = self.orchestrator.handle_message_stream(transcript, on_tool_call=on_tool_call)
            speaker = StreamingSpeaker(tts=self.tts)
            reply = speaker.speak_stream(token_stream)
        timings["orchestrator_and_tts_s"] = round(time.perf_counter() - start, 3)

        timings["total_s"] = round(sum(timings.values()), 3)
        return VoiceTurnResult(success=True, transcript=transcript, reply=reply, timings=timings)

    @contextmanager
    def _narrator_active(self) -> Iterator[None]:
        """Narrates a real tool call ("Vou pesquisar isso pra você agora.") for its
        actual duration — driven by the Orchestrator's existing TOOL_STARTED/
        TOOL_COMPLETED EventBus events (core/orchestrator.py), not a new callback.
        Same mechanism PACK 2's ToolFiller used (see voice/tool_filler.py, no longer
        wired in here — running both on the same events would speak two overlapping
        phrases every tool call). Deliberately NOT wired through `on_tool_call` (fires
        once before a tool runs, with no matching "done" signal), and deliberately NOT
        wrapping the whole orchestrator call (a plain conversational reply routinely
        takes longer to generate than a tool does to run, which would narrate over
        every reply, not just tool calls). A no-op with no EventBus."""
        if self.event_bus is None:
            yield
            return

        active_action_context = {"ctx": None}

        def on_started(data: dict) -> None:
            ctx = self.narrator.action(data.get("tool", ""))
            ctx.__enter__()
            active_action_context["ctx"] = ctx

        def on_completed(data: dict) -> None:
            ctx = active_action_context.pop("ctx", None)
            if ctx is not None:
                ctx.__exit__(None, None, None)

        self.event_bus.subscribe(TOOL_STARTED, on_started)
        self.event_bus.subscribe(TOOL_COMPLETED, on_completed)
        try:
            yield
        finally:
            self.event_bus.unsubscribe(TOOL_STARTED, on_started)
            self.event_bus.unsubscribe(TOOL_COMPLETED, on_completed)
            leftover_ctx = active_action_context.get("ctx")
            if leftover_ctx is not None:
                leftover_ctx.__exit__(None, None, None)  # safety net if TOOL_COMPLETED never fired

    def _capture(
        self, timings: dict[str, float], on_amplitude: Callable[[float], None] | None = None
    ) -> tuple[Path | None, str | None]:
        start = time.perf_counter()
        try:
            path = self.audio_capture.record_until_silence(on_amplitude=on_amplitude)
        except AudioCaptureError as exc:
            logger.warning("Falha na captura de áudio: %s", exc)
            return None, MIC_ERROR_MESSAGE
        timings["capture_s"] = round(time.perf_counter() - start, 3)
        return path, None

    def _transcribe(self, audio_path: Path, timings: dict[str, float]) -> tuple[str | None, str | None]:
        start = time.perf_counter()
        try:
            transcript = self.stt.transcribe(str(audio_path))
        except Exception:
            logger.exception("Falha na transcrição de voz.")
            return None, NO_SPEECH_MESSAGE
        finally:
            self._cleanup(audio_path)
        timings["stt_s"] = round(time.perf_counter() - start, 3)

        if not transcript or not transcript.strip():
            return None, NO_SPEECH_MESSAGE
        return transcript.strip(), None

    def _ask_orchestrator(
        self,
        transcript: str,
        timings: dict[str, float],
        on_tool_call: Callable[[str], None] | None = None,
    ) -> str:
        start = time.perf_counter()
        reply = self.orchestrator.handle_message(transcript, on_tool_call=on_tool_call)
        timings["orchestrator_s"] = round(time.perf_counter() - start, 3)
        return reply

    def _speak(self, reply: str, timings: dict[str, float]) -> None:
        if not self.tts.is_available():
            logger.info("TTS indisponível; resposta ficará só em texto.")
            return
        start = time.perf_counter()
        if not self.tts.speak(reply):
            logger.warning("Falha ao reproduzir a resposta por voz.")
        timings["tts_s"] = round(time.perf_counter() - start, 3)

    @staticmethod
    def _cleanup(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
