"""SentenceBuffer — detecta fim de frase em stream de tokens do LLM.

Reutilizado de padrões open source (speak-to-llm, llm-voice, ollama-voice).
Objetivo: permitir que o TTS comece a falar assim que uma frase completa
é gerada, em vez de esperar a resposta inteira.
"""
from __future__ import annotations

import re
from typing import Optional


# Pontuação que normalmente encerra frase em português e inglês
_SENTENCE_END = re.compile(r'(?<=[.!?…])(?:\s+|$)')


class SentenceBuffer:
    """Acumula tokens e devolve frases completas assim que elas ficam prontas."""

    def __init__(self, min_chars: int = 12, max_chars: int = 220):
        """
        min_chars: evita falar pedaços muito curtos ("Ok.", "Sim.")
        max_chars: força corte se o modelo gerar texto sem pontuação
        """
        self.min_chars = min_chars
        self.max_chars = max_chars
        self._buffer = ""

    def add(self, token: str) -> list[str]:
        """Adiciona um token (ou pedaço de texto) e retorna frases completas."""
        if not token:
            return []

        self._buffer += token
        sentences: list[str] = []

        while True:
            match = _SENTENCE_END.search(self._buffer)
            if not match:
                # Sem pontuação final — verifica se estourou o limite
                if len(self._buffer) >= self.max_chars:
                    # Corta no último espaço para não quebrar palavra
                    cut = self._buffer.rfind(" ", 0, self.max_chars)
                    if cut < self.min_chars:
                        cut = self.max_chars
                    sentence = self._buffer[:cut].strip()
                    self._buffer = self._buffer[cut:].lstrip()
                    if sentence:
                        sentences.append(sentence)
                    continue
                break

            end = match.end()
            sentence = self._buffer[:end].strip()
            self._buffer = self._buffer[end:]

            if len(sentence) >= self.min_chars:
                sentences.append(sentence)

        return sentences

    def flush(self) -> Optional[str]:
        """Retorna o que sobrou no buffer (chamar no final da geração)."""
        leftover = self._buffer.strip()
        self._buffer = ""
        return leftover if leftover else None

    def clear(self) -> None:
        self._buffer = ""

    @property
    def pending(self) -> str:
        return self._buffer
