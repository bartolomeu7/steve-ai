"""Tool Filler — fala frases curtas enquanto uma ferramenta é executada.

Padrão inspirado em LiveKit Agents (with_filler) e na técnica de preamble
usada em voice agents de produção.

Objetivo: eliminar o silêncio constrangedor quando o Steve chama uma tool.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger("steve.voice.tool_filler")

# Frases curtas e naturais em português brasileiro
DEFAULT_FILLERS = [
    "Só um segundo...",
    "Deixa eu verificar isso.",
    "Um momento, por favor.",
    "Estou checando aqui.",
    "Já já te respondo.",
    "Deixa eu olhar isso pra você.",
    "Só um instante.",
]


class ToolFiller:
    """
    Gerencia fillers de voz durante a execução de ferramentas.

    Uso básico:
        filler = ToolFiller(tts=meu_tts)
        with filler.during_tool("search_files"):
            resultado = executar_ferramenta(...)
    """

    def __init__(
        self,
        tts=None,
        fillers: list[str] | None = None,
        delay: float = 0.35,
        speak_fn: Optional[Callable[[str], bool]] = None,
    ):
        """
        tts: objeto TextToSpeech (EdgeTTS etc.)
        fillers: lista customizada de frases
        delay: segundos antes de falar o filler (evita falar se a tool for muito rápida)
        speak_fn: função alternativa de fala (se não quiser usar o tts.speak)
        """
        self.tts = tts
        self.fillers = fillers or DEFAULT_FILLERS
        self.delay = delay
        self.speak_fn = speak_fn
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def during_tool(self, tool_name: str = ""):
        """Context manager que fala um filler enquanto a tool roda."""
        return _FillerContext(self, tool_name)

    def say_filler(self, tool_name: str = "") -> None:
        """Fala um filler imediatamente (sem delay)."""
        phrase = self._choose_phrase(tool_name)
        self._speak(phrase)

    def _choose_phrase(self, tool_name: str = "") -> str:
        # Pode especializar por tipo de ferramenta no futuro
        return random.choice(self.fillers)

    def _speak(self, text: str) -> None:
        try:
            if self.speak_fn:
                self.speak_fn(text)
            elif self.tts and hasattr(self.tts, "speak"):
                self.tts.speak(text)
            else:
                logger.debug("Filler (sem TTS): %s", text)
        except Exception as e:
            logger.warning("Erro ao falar filler: %s", e)

    def _delayed_speak(self, tool_name: str) -> None:
        """Espera `delay` segundos. Se a tool ainda estiver rodando, fala."""
        if self._stop.wait(self.delay):
            return  # tool terminou antes do delay
        if not self._stop.is_set():
            self.say_filler(tool_name)


class _FillerContext:
    def __init__(self, filler: ToolFiller, tool_name: str):
        self.filler = filler
        self.tool_name = tool_name

    def __enter__(self):
        self.filler._stop.clear()
        self.filler._thread = threading.Thread(
            target=self.filler._delayed_speak,
            args=(self.tool_name,),
            daemon=True,
        )
        self.filler._thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.filler._stop.set()
        if self.filler._thread:
            self.filler._thread.join(timeout=1.0)
            self.filler._thread = None
        return False
