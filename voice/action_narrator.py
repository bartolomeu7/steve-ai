"""ActionNarrator — Steve narrates what it's doing while a real tool runs (Jarvis-style:
"Vou pesquisar isso pra você agora.", not silence).

Adapted from Pack 3's narrator/action_narrator.py. Two real adaptations were needed,
not just a copy:

1. The package's own guide wired this straight into the Orchestrator with a captured
   `tts=self.tts`. The Orchestrator has no TTS coupling by design (voice/text/CLI all
   share it without it knowing which one is calling) — narrating is exclusively
   VoiceService's job. So this is driven the same way Pack 2's ToolFiller was: from
   VoiceService, off the Orchestrator's existing TOOL_STARTED/TOOL_COMPLETED EventBus
   events (see voice/service.py::VoiceService._narrator_active), never a second event
   system and never invented activity — every phrase here only ever wraps a tool call
   that is genuinely running.
2. `orb_callback` now takes a real `ui.desktop.orb.states.OrbState`, not the package's
   raw strings ("idle", "tool_execution", ...) — so a typo here can't silently produce
   an orb state nobody recognizes.

ActionNarrator REPLACES ToolFiller as VoiceService's active tool-narration mechanism
(see voice/service.py) — running both on the same TOOL_STARTED/TOOL_COMPLETED pair
would speak two overlapping phrases every time a tool runs. voice/tool_filler.py is
left in place (still tested, still a smaller/simpler option) but no longer wired in by
default.
"""
from __future__ import annotations

import logging
import random
import threading
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

from ui.desktop.orb.states import OrbState

logger = logging.getLogger("steve.voice.narrator")

NARRATIVES = {
    "web_search": {
        "start": [
            "Vou pesquisar isso pra você agora.",
            "Deixa eu buscar essa informação.",
            "Pesquisando o que você pediu.",
        ],
        "browser_aware": [
            "Vi que você está no {app}. Vou pesquisar sem atrapalhar sua tela.",
            "Você está no {app} agora. Eu pesquiso em paralelo, pode continuar.",
        ],
    },
    "screenshot": {"start": ["Vou capturar a tela agora.", "Tirando um print rapidinho."]},
    "volume": {"start": ["Ajustando o volume.", "Mudando o áudio do sistema."]},
    "clipboard": {"start": ["Olhando o que tem no seu clipboard.", "Lendo a área de transferência."]},
    "open_file": {"start": ["Abrindo isso pra você.", "Já estou abrindo."]},
    "open_application": {"start": ["Claro. Abrindo agora.", "Vou abrir pra você."]},
    "active_window": {"start": ["Deixa eu ver o que está aberto agora."]},
    "generic_tool": {"start": ["Só um segundo, estou resolvendo isso.", "Executando agora.", "Deixa comigo."]},
    "done": ["Pronto.", "Feito.", "Concluído."],
}


def _pick(key: str, subkey: str = "start") -> str:
    block = NARRATIVES.get(key, NARRATIVES["generic_tool"])
    # Most entries are {"start": [...]} dicts, but a few ("done") are bare lists —
    # NARRATIVES["done"].get(...) would raise AttributeError if treated like the rest.
    options = block if isinstance(block, list) else (block.get(subkey) or block.get("start") or ["..."])
    return random.choice(options)


class ActionNarrator:
    """Narrates real tool executions in real time.

    tts: object with speak(text) -> bool
    orb_callback: function(OrbState) — reflects real state, never invented activity
    get_active_window: function() -> str, used only for "you're on Chrome" awareness
                        during web_search (defaults to the real active_window tool)
    """

    def __init__(
        self,
        tts=None,
        orb_callback: Optional[Callable[[OrbState], None]] = None,
        get_active_window: Optional[Callable[[], str]] = None,
        speak_enabled: bool = True,
    ):
        self.tts = tts
        self.orb_callback = orb_callback
        self.get_active_window = get_active_window
        self.speak_enabled = speak_enabled
        self._lock = threading.Lock()

    def say(self, text: str, orb_state: OrbState = OrbState.SPEAKING) -> None:
        self._set_orb(orb_state)
        if self.speak_enabled and text and self.tts:
            try:
                self.tts.speak(text)
            except Exception as exc:
                logger.warning("Falha ao narrar: %s", exc)

    def conclude(self) -> None:
        self.say(_pick("done"), orb_state=OrbState.SPEAKING)
        self._set_orb(OrbState.IDLE)

    @contextmanager
    def action(self, tool_name: str) -> Iterator["ActionNarrator"]:
        """Narrates the start of a real tool call, for its actual duration."""
        self._set_orb(OrbState.TOOL_EXECUTION)
        self.say(self._build_start_phrase(tool_name), orb_state=OrbState.TOOL_EXECUTION)
        try:
            yield self
        finally:
            self._set_orb(OrbState.THINKING)

    def _build_start_phrase(self, tool_name: str) -> str:
        base = _pick(tool_name) if tool_name in NARRATIVES else _pick("generic_tool")

        if tool_name == "web_search" and self.get_active_window:
            try:
                title = (self.get_active_window() or "").lower()
                app = self._detect_app(title)
                if app:
                    return f"{base} {_pick('web_search', 'browser_aware').format(app=app)}"
            except Exception:
                pass
        return base

    @staticmethod
    def _detect_app(title: str) -> str:
        mapping = [
            ("brave", "Brave"), ("chrome", "Chrome"), ("msedge", "Edge"), ("edge", "Edge"),
            ("firefox", "Firefox"), ("opera", "Opera"), ("code", "VS Code"),
            ("notepad", "Bloco de Notas"), ("discord", "Discord"), ("spotify", "Spotify"),
        ]
        for key, name in mapping:
            if key in title:
                return name
        return ""

    def _set_orb(self, state: OrbState) -> None:
        if self.orb_callback:
            try:
                self.orb_callback(state)
            except Exception as exc:
                logger.debug("Erro no callback do orb: %s", exc)
