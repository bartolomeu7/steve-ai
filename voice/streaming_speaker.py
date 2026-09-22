"""StreamingSpeaker — fala frases assim que elas ficam prontas.

Usa o SentenceBuffer + qualquer TextToSpeech (EdgeTTS preferencialmente).
Projetado para ser chamado enquanto o Ollama ainda está gerando tokens.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Iterator, Optional

from voice.sentence_buffer import SentenceBuffer
from voice.tts import TextToSpeech, NullTTS

logger = logging.getLogger("steve.voice.streaming")


class StreamingSpeaker:
    """Recebe um stream de tokens e fala frase por frase em paralelo."""

    def __init__(self, tts: TextToSpeech | None = None):
        self.tts = tts or NullTTS()
        self.buffer = SentenceBuffer()
        self._queue: deque[str] = deque()
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Inicia a thread que consome a fila e fala."""
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._speak_loop, daemon=True)
        self._worker.start()

    def stop(self) -> None:
        """Para a thread e limpa a fila."""
        self._stop.set()
        with self._lock:
            self._queue.clear()
        if self._worker:
            self._worker.join(timeout=2.0)
            self._worker = None

    def feed(self, token: str) -> None:
        """Recebe um token do stream do LLM."""
        sentences = self.buffer.add(token)
        for s in sentences:
            self._enqueue(s)

    def finish(self) -> None:
        """Chamar quando o stream do LLM terminar."""
        leftover = self.buffer.flush()
        if leftover:
            self._enqueue(leftover)

    def speak_stream(self, token_stream: Iterator[str]) -> str:
        """
        Método conveniente: recebe um iterator de tokens,
        fala frase a frase e retorna a resposta completa.
        """
        self.start()
        full = []
        try:
            for token in token_stream:
                full.append(token)
                self.feed(token)
            self.finish()
        finally:
            # Espera a fila esvaziar (com timeout de segurança)
            self._wait_queue_empty(timeout=60.0)
            self.stop()
        return "".join(full)

    # ------------------------------------------------------------------
    def _enqueue(self, sentence: str) -> None:
        with self._lock:
            self._queue.append(sentence)

    def _speak_loop(self) -> None:
        while not self._stop.is_set():
            sentence = None
            with self._lock:
                if self._queue:
                    sentence = self._queue.popleft()

            if sentence:
                try:
                    self.tts.speak(sentence)
                except Exception as e:
                    logger.warning("Erro ao falar frase: %s", e)
            else:
                # Evita busy-loop
                self._stop.wait(0.05)

    def _wait_queue_empty(self, timeout: float = 30.0) -> None:
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._queue:
                    return
            time.sleep(0.1)
