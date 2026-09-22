"""Smart Recall — seleção inteligente de memórias relevantes.

O Steve já tem memória em SQLite. Este módulo melhora a *seleção*
do que é injetado no contexto do LLM, evitando prompt inchado
e informações irrelevantes.

Estratégia (leve e local):
1. Busca candidatas por palavras-chave / tags
2. Ranqueia por relevância simples (overlap de termos + recência)
3. Limita a quantidade de memórias injetadas
4. Formata de forma limpa para o system/context prompt
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger("steve.memory.smart_recall")


@dataclass
class MemoryItem:
    id: str | int
    content: str
    tags: list[str] | None = None
    created_at: datetime | None = None
    importance: float = 0.5  # 0.0 a 1.0
    source: str = "permanent"  # permanent | temporary | session


def _tokenize(text: str) -> set[str]:
    """Tokenização simples em português (remove pontuação e lower)."""
    text = text.lower()
    text = re.sub(r"[^\w\sáéíóúâêôãõç]", " ", text)
    return {t for t in text.split() if len(t) > 2}


def _score(
    query_tokens: set[str],
    item: MemoryItem,
    now: datetime,
) -> float:
    """Pontuação simples: overlap de termos + recência + importância."""
    content_tokens = _tokenize(item.content)
    tag_tokens = _tokenize(" ".join(item.tags or []))

    overlap = len(query_tokens & (content_tokens | tag_tokens))
    if not query_tokens:
        term_score = 0.0
    else:
        term_score = overlap / max(len(query_tokens), 1)

    # Recência (memórias mais novas ganham um pouco)
    recency = 0.0
    if item.created_at:
        try:
            delta_days = (now - item.created_at).total_seconds() / 86400
            recency = max(0.0, 1.0 - (delta_days / 90.0))  # decai em ~90 dias
        except Exception:
            recency = 0.0

    importance = max(0.0, min(1.0, item.importance))

    # Pesos
    return (0.55 * term_score) + (0.25 * importance) + (0.20 * recency)


class SmartRecall:
    """
    Seleciona as memórias mais relevantes para uma pergunta.

    Uso:
        recall = SmartRecall(fetch_all_fn=meu_memory_service.list_all)
        relevant = recall.select(user_message, limit=6)
        context = recall.format_for_prompt(relevant)
    """

    def __init__(
        self,
        fetch_all_fn: Callable[[], list[MemoryItem]] | None = None,
        min_score: float = 0.12,
    ):
        """
        fetch_all_fn: função que retorna todas as memórias (ou um subconjunto recente).
                      Deve retornar list[MemoryItem] ou dicts compatíveis.
        min_score: pontuação mínima para incluir a memória.
        """
        self.fetch_all_fn = fetch_all_fn
        self.min_score = min_score

    def select(
        self,
        query: str,
        limit: int = 6,
        memories: list[MemoryItem] | None = None,
    ) -> list[MemoryItem]:
        """Retorna as memórias mais relevantes para a query."""
        if memories is None:
            if not self.fetch_all_fn:
                return []
            raw = self.fetch_all_fn()
            memories = [self._normalize(m) for m in raw]

        if not memories or not query.strip():
            return []

        query_tokens = _tokenize(query)
        now = datetime.now(timezone.utc)

        scored = []
        for item in memories:
            s = _score(query_tokens, item, now)
            if s >= self.min_score:
                scored.append((s, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def format_for_prompt(self, items: list[MemoryItem]) -> str:
        """Formata as memórias para injetar no contexto do LLM."""
        if not items:
            return ""

        lines = ["### Memórias relevantes sobre o usuário:"]
        for i, item in enumerate(items, 1):
            tag_str = f" [{', '.join(item.tags)}]" if item.tags else ""
            lines.append(f"{i}. {item.content.strip()}{tag_str}")
        return "\n".join(lines)

    def _normalize(self, m: Any) -> MemoryItem:
        if isinstance(m, MemoryItem):
            return m
        if isinstance(m, dict):
            created = m.get("created_at")
            if isinstance(created, str):
                try:
                    created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                except Exception:
                    created = None
            return MemoryItem(
                id=m.get("id", ""),
                content=m.get("content") or m.get("text") or str(m),
                tags=m.get("tags") or [],
                created_at=created,
                importance=float(m.get("importance", 0.5)),
                source=m.get("source", "permanent"),
            )
        # fallback
        return MemoryItem(id="", content=str(m))
