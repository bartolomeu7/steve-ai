"""LearningRouter: normalize slang, detect intents/entities, manage multi-turn slots."""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from learning.store import LearningStore

logger = logging.getLogger("steve.learning.router")


def _norm(text: str) -> str:
    text = (text or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text)


@dataclass
class RouteResult:
    original: str
    normalized: str
    intent: str | None = None
    tool_hint: str | None = None
    slots: dict[str, str] = field(default_factory=dict)
    ask: str | None = None
    ready: bool = True
    rewritten: str | None = None  # text to send to Orchestrator when ready
    notes: list[str] = field(default_factory=list)


class LearningRouter:
    def __init__(self, store: LearningStore | None = None):
        self.store = store or LearningStore()

    def apply_lexicon(self, text: str) -> str:
        out = text
        # longer phrases first
        entries = sorted(self.store.lexicon().items(), key=lambda kv: len(kv[0]), reverse=True)
        low = out
        for phrase, meaning in entries:
            if phrase in _norm(low) and meaning and not meaning.startswith("("):
                # replace case-insensitive in original-ish form
                pattern = re.compile(re.escape(phrase), re.IGNORECASE)
                out = pattern.sub(meaning, out)
        return out

    def find_artist(self, text: str) -> tuple[str, dict] | None:
        n = _norm(text)
        ents = self.store.entities().get("artists") or {}
        # sort by key length desc
        for key, meta in sorted(ents.items(), key=lambda kv: len(kv[0]), reverse=True):
            aliases = [key, _norm(meta.get("canonical", "")), *[_norm(a) for a in meta.get("aliases") or []]]
            for a in aliases:
                if a and a in n:
                    return key, meta
        return None

    def match_intent(self, text: str) -> tuple[str, dict] | None:
        n = _norm(text)
        for name, spec in self.store.intents().items():
            for pat in spec.get("patterns") or []:
                try:
                    if re.search(pat, n, re.IGNORECASE):
                        return name, spec
                except re.error:
                    continue
        return None

    def route(self, user_text: str) -> RouteResult:
        original = (user_text or "").strip()
        normalized = self.apply_lexicon(original)
        result = RouteResult(original=original, normalized=normalized)

        active = self.store.get_slot()
        if active:
            return self._continue_slot(result, active)

        artist_hit = self.find_artist(normalized)
        intent_hit = self.match_intent(normalized)

        # Bare artist mention => play_music with missing query slot filled partially
        if artist_hit and (intent_hit is None or intent_hit[0] == "play_music"):
            key, meta = artist_hit
            intent_name, spec = ("play_music", self.store.intents().get("play_music") or {})
            if intent_hit:
                intent_name, spec = intent_hit
            result.intent = intent_name
            result.tool_hint = spec.get("tool")
            # If user said only artist (or artist + tocar sem música), ask for song
            song = self._extract_song(normalized, meta.get("canonical", key))
            if song:
                query = f"{meta.get('canonical', key)} {song}".strip()
                result.slots = {"query": query, "artist": meta.get("canonical", key), "song": song}
                result.ready = True
                result.rewritten = f"Toque a música {song} do artista {meta.get('canonical', key)} usando play_music."
                result.notes.append("artist+song")
                return result
            # incomplete
            ask = "Qual música do " + meta.get("canonical", key) + "? Pode dizer o nome ou 'qualquer uma'."
            self.store.set_slot(
                {
                    "intent": "play_music",
                    "tool": "play_music",
                    "slots": {"artist": meta.get("canonical", key)},
                    "ask": ask,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            result.slots = {"artist": meta.get("canonical", key)}
            result.ask = ask
            result.ready = False
            result.notes.append("slot_open_artist")
            return result

        if intent_hit:
            name, spec = intent_hit
            result.intent = name
            result.tool_hint = spec.get("tool")
            if name == "play_music":
                # try pull query after verb
                q = self._extract_play_query(normalized)
                if q:
                    result.slots = {"query": q}
                    result.ready = True
                    result.rewritten = f"Toque: {q} (use a tool play_music com query exactly '{q}')."
                    return result
                ask = spec.get("ask") or "Qual música você quer?"
                self.store.set_slot(
                    {
                        "intent": name,
                        "tool": spec.get("tool"),
                        "slots": {},
                        "ask": ask,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                result.ask = ask
                result.ready = False
                return result
            # other intents: pass rewritten soft hint
            result.rewritten = normalized
            result.ready = True
            return result

        result.rewritten = normalized
        result.ready = True
        return result

    def _continue_slot(self, result: RouteResult, active: dict) -> RouteResult:
        intent = active.get("intent")
        slots = dict(active.get("slots") or {})
        text = result.normalized
        result.intent = intent
        result.tool_hint = active.get("tool")

        if intent == "play_music":
            artist = slots.get("artist", "")
            n = _norm(text)
            if any(x in n for x in ("qualquer", "a que tiver", "pode ser qualquer", "primeira")):
                song = "músicas mais tocadas"
                query = f"{artist} {song}".strip()
            else:
                song = text.strip()
                # strip wake remnants
                song = re.sub(r"^(steve[,\s]+)", "", song, flags=re.I).strip()
                query = f"{artist} {song}".strip() if artist else song
            slots.update({"song": song, "query": query})
            self.store.clear_slot()
            result.slots = slots
            result.ready = True
            result.rewritten = (
                f"Toque a música '{song}'"
                + (f" do artista {artist}" if artist else "")
                + f" usando play_music com query '{query}'."
            )
            result.notes.append("slot_filled")
            return result

        # unknown active slot — clear
        self.store.clear_slot()
        result.rewritten = result.normalized
        result.ready = True
        return result

    def _extract_song(self, text: str, artist_canonical: str) -> str | None:
        n = _norm(text)
        a = _norm(artist_canonical)
        stop = {
            "toca", "tocar", "ouve", "ouvir", "coloca", "joga", "poe", "poe", "play",
            "musica", "musicas", "som", "uma", "um", "uns", "umas", "o", "a", "os", "as",
            "do", "da", "de", "dos", "das", "no", "na", "em", "pro", "pra", "para", "steve", "esteve",
        }
        if a:
            n = n.replace(a, " ")
            for part in a.split():
                if len(part) > 2:
                    n = n.replace(part, " ")
        tokens = [tok for tok in re.findall(r"[a-z0-9]+", n) if tok not in stop]
        if not tokens:
            return None
        joined = " ".join(tokens)
        if joined in ("qualquer", "qualquer uma", "qualquer um") or tokens == ["qualquer"]:
            return "musicas mais tocadas"
        # ignore leftover junk like only 'hip' 'hop' fragments already removed with artist
        if len(joined) < 2:
            return None
        return joined

    def _extract_play_query(self, text: str) -> str | None:
        n = _norm(text)
        m = re.search(
            r"\b(?:toca|tocar|ouve|ouvir|coloca|joga|p[oõ]e|play)\s+(?:a\s+m[uú]sica\s+|o\s+som\s+|uma\s+m[uú]sica\s+)?(.+)$",
            n,
        )
        if not m:
            return None
        q = m.group(1).strip(" .,!")
        if len(q) < 2:
            return None
        return q
