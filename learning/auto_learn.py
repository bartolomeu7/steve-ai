"""Auto-learn useful bits from a voice turn into G: SteveLearning."""
from __future__ import annotations

import logging
import re
from typing import Iterable

from learning.router import _norm
from learning.store import LearningStore

logger = logging.getLogger("steve.learning.auto")

_STOP = {
    "o", "a", "os", "as", "de", "da", "do", "e", "é", "um", "uma", "pra", "para", "com",
    "que", "eu", "me", "meu", "minha", "steve", "por", "no", "na", "em", "se", "não", "nao",
}


def auto_learn_from_turn(store: LearningStore, transcript: str, reply: str | None = None) -> list[str]:
    """Extract useful lexicon/entity hints. Returns list of what was learned."""
    learned: list[str] = []
    text = (transcript or "").strip()
    if not text or len(text) < 3:
        return learned
    n = _norm(text)

    # "X é gíria para Y" / "X significa Y"
    m = re.search(r"(.+?)\s+(?:é|e)\s+g[ií]ria\s+para\s+(.+)$", n)
    if not m:
        m = re.search(r"(.+?)\s+significa\s+(.+)$", n)
    if m:
        phrase, meaning = m.group(1).strip(), m.group(2).strip()
        if store.add_lexicon(phrase, meaning, source="auto_voice"):
            learned.append(f"lexicon:{phrase}")

    # "toca <something>" learn as music-ish entity if looks like proper name
    m = re.search(r"\b(?:toca|tocar|ouve)\s+(.+)$", n)
    if m:
        raw = m.group(1).strip()
        raw = re.sub(r"^(a musica|a música|o som|uma musica|uma música)\s+", "", raw)
        if 2 <= len(raw) <= 60 and not raw.startswith("http"):
            key = raw.split()[0]
            if key not in _STOP and store.add_artist(key, raw.title(), source="auto_voice"):
                learned.append(f"artist:{key}")

    # Hungria-style: single proper-looking token after wake already handled by seed
    tokens = [t for t in re.findall(r"[a-z0-9áéíóúâêôãõç]+", n) if t not in _STOP and len(t) > 3]
    # if user said only one content token and reply asked for song, already in slots

    if learned:
        logger.info("Auto-learn: %s", ", ".join(learned))
    return learned
