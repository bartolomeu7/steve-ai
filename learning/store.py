"""Persistent learning store on G:\\Grok\\SteveLearning (JSON files)."""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("steve.learning.store")

DEFAULT_ROOT = Path(r"G:\Grok\SteveLearning")


class LearningStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else DEFAULT_ROOT
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        for name, default in (
            ("lexicon.json", {"version": 1, "entries": {}}),
            ("commands.json", {"version": 1, "intents": {}}),
            ("entities.json", {"version": 1, "artists": {}, "apps": {}}),
            ("slots_state.json", {"version": 1, "active": None}),
        ):
            path = self.root / name
            if not path.exists():
                path.write_text(json.dumps(default, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log = self.root / "learned_log.jsonl"
        if not log.exists():
            log.write_text("", encoding="utf-8")

    def _read(self, name: str) -> dict:
        path = self.root / name
        with self._lock:
            return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, name: str, data: dict) -> None:
        path = self.root / name
        with self._lock:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def lexicon(self) -> dict[str, str]:
        return dict(self._read("lexicon.json").get("entries") or {})

    def intents(self) -> dict[str, Any]:
        return dict(self._read("commands.json").get("intents") or {})

    def entities(self) -> dict[str, Any]:
        return self._read("entities.json")

    def get_slot(self) -> dict | None:
        return self._read("slots_state.json").get("active")

    def set_slot(self, active: dict | None) -> None:
        data = self._read("slots_state.json")
        data["active"] = active
        self._write("slots_state.json", data)

    def clear_slot(self) -> None:
        self.set_slot(None)

    def add_lexicon(self, phrase: str, meaning: str, source: str = "auto") -> bool:
        phrase = (phrase or "").strip().lower()
        meaning = (meaning or "").strip()
        if not phrase or not meaning or len(phrase) < 2:
            return False
        data = self._read("lexicon.json")
        entries = data.setdefault("entries", {})
        if entries.get(phrase) == meaning:
            return False
        entries[phrase] = meaning
        self._write("lexicon.json", data)
        self.log("lexicon", {"phrase": phrase, "meaning": meaning, "source": source})
        return True

    def add_artist(self, key: str, canonical: str, aliases: list[str] | None = None, source: str = "auto") -> bool:
        key = (key or "").strip().lower()
        canonical = (canonical or "").strip()
        if not key or not canonical:
            return False
        data = self._read("entities.json")
        artists = data.setdefault("artists", {})
        if key in artists and artists[key].get("canonical") == canonical:
            return False
        artists[key] = {
            "canonical": canonical,
            "domain": "music",
            "aliases": aliases or [],
        }
        self._write("entities.json", data)
        self.log("artist", {"key": key, "canonical": canonical, "source": source})
        return True

    def log(self, kind: str, payload: dict) -> None:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            **payload,
        }
        path = self.root / "learned_log.jsonl"
        with self._lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
