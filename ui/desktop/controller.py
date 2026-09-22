"""ChatController: the desktop UI's only connection to the Core.

No AI, tool-execution or permission logic lives here — every actual decision still
happens inside Orchestrator -> ToolManager -> PermissionManager, exactly like the CLI.
This module only: (1) runs those calls off the main thread via BackgroundRunner, (2)
guards against two turns running at once, and (3) translates real events (a tool about
to run, the voice pipeline's own state messages) into OrbState so the orb reflects what
Steve is actually doing — it never invents a state on its own. Deliberately does not
import customtkinter, so it can be unit-tested without a real window.
"""
from __future__ import annotations

import logging
from typing import Callable

from core.orchestrator import Orchestrator
from ui.desktop.async_bridge import BackgroundRunner
from ui.desktop.orb.states import OrbState
from voice.service import (
    LISTENING_STATE_MESSAGE,
    PROCESSING_STATE_MESSAGE,
    SPEAKING_STATE_MESSAGE,
    VoiceService,
    VoiceTurnResult,
)

logger = logging.getLogger("steve.ui.desktop.controller")

GENERIC_ERROR_MESSAGE = "Ocorreu um erro inesperado. Veja os logs para mais detalhes."

TOOL_STATUS_LABELS = {
    "check_cpu": "🧠 Consultando CPU...",
    "check_ram": "🧠 Consultando memória...",
    "check_disk": "🧠 Consultando disco...",
    "list_processes": "🧠 Listando processos...",
    "list_directory": "🧠 Listando arquivos...",
    "create_directory": "🔧 Criando pasta...",
    "open_file": "🔧 Abrindo arquivo...",
    "open_application": "🔧 Abrindo aplicativo...",
    "open_url": "🔧 Abrindo link...",
    "play_music": "🎵 Abrindo player...",
    "remember_fact": "🧠 Salvando na memória...",
    "forget_memory": "🧠 Esquecendo...",
    "recall_memories": "🧠 Consultando memória...",
}
DEFAULT_TOOL_STATUS_LABEL = "🔧 Executando ferramenta..."

_VOICE_MESSAGE_TO_ORB_STATE = {
    LISTENING_STATE_MESSAGE: OrbState.LISTENING,
    PROCESSING_STATE_MESSAGE: OrbState.THINKING,
    SPEAKING_STATE_MESSAGE: OrbState.SPEAKING,
}


def tool_status_label(tool_name: str) -> str:
    """Maps a tool name to a discreet, human status line — never exposes raw JSON."""
    return TOOL_STATUS_LABELS.get(tool_name, DEFAULT_TOOL_STATUS_LABEL)


class ChatController:
    def __init__(
        self,
        runner: BackgroundRunner,
        orchestrator: Orchestrator,
        voice_service: VoiceService | None,
        voice_streaming_enabled: bool = False,
    ):
        self._runner = runner
        self.orchestrator = orchestrator
        self.voice_service = voice_service
        self.voice_streaming_enabled = voice_streaming_enabled
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def voice_available(self) -> bool:
        return self.voice_service is not None

    def send_text(
        self,
        text: str,
        on_status: Callable[[str], None],
        on_reply: Callable[[str], None],
        on_error: Callable[[str], None],
        on_orb_state: Callable[[OrbState], None] | None = None,
    ) -> bool:
        """Kicks off one turn in the background. Returns False (no-op) if a turn is
        already running or the text is empty — this is what prevents two messages from
        being processed at once."""
        if self._busy or not text.strip():
            return False
        self._busy = True
        on_status("🧠 Pensando...")
        if on_orb_state:
            on_orb_state(OrbState.THINKING)

        def on_tool_call(tool_name: str) -> None:
            # called from the worker thread — must go through the thread-safe queue
            self._runner.post(on_status, tool_status_label(tool_name))
            if on_orb_state:
                self._runner.post(on_orb_state, OrbState.TOOL_EXECUTION)

        def work() -> str:
            return self.orchestrator.handle_message(text, on_tool_call=on_tool_call)

        def done(reply: str) -> None:
            self._busy = False
            if on_orb_state:
                on_orb_state(OrbState.IDLE)
            on_reply(reply)

        def failed(exc: Exception) -> None:
            self._busy = False
            if on_orb_state:
                on_orb_state(OrbState.ERROR)
            on_error(GENERIC_ERROR_MESSAGE)

        self._runner.run(work, on_done=done, on_error=failed)
        return True

    def send_voice(
        self,
        on_status: Callable[[str], None],
        on_result: Callable[[VoiceTurnResult], None],
        on_error: Callable[[str], None],
        on_orb_state: Callable[[OrbState], None] | None = None,
        on_mic_amplitude: Callable[[float], None] | None = None,
    ) -> bool:
        if self._busy or self.voice_service is None:
            return False
        self._busy = True

        def on_tool_call(tool_name: str) -> None:
            self._runner.post(on_status, tool_status_label(tool_name))
            if on_orb_state:
                self._runner.post(on_orb_state, OrbState.TOOL_EXECUTION)

        def on_state(state: str) -> None:
            self._runner.post(on_status, state)
            orb_state = _VOICE_MESSAGE_TO_ORB_STATE.get(state)
            if on_orb_state and orb_state is not None:
                self._runner.post(on_orb_state, orb_state)

        def on_amplitude(value: float) -> None:
            if on_mic_amplitude:
                self._runner.post(on_mic_amplitude, value)

        def work() -> VoiceTurnResult:
            if self.voice_streaming_enabled:
                return self.voice_service.listen_and_respond_streaming(
                    on_state=on_state, on_tool_call=on_tool_call, on_amplitude=on_amplitude
                )
            return self.voice_service.listen_and_respond(
                on_state=on_state, on_tool_call=on_tool_call, on_amplitude=on_amplitude
            )

        def done(result: VoiceTurnResult) -> None:
            self._busy = False
            if on_orb_state:
                on_orb_state(OrbState.IDLE if result.success else OrbState.ERROR)
            on_result(result)

        def failed(exc: Exception) -> None:
            self._busy = False
            if on_orb_state:
                on_orb_state(OrbState.ERROR)
            on_error(GENERIC_ERROR_MESSAGE)

        self._runner.run(work, on_done=done, on_error=failed)
        return True
