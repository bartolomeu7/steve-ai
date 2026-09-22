"""Tracks the current conversation's short-term (in-memory) history."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Turn:
    role: str
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SessionManager:
    def __init__(self, session_id: str | None = None):
        self.session_id = session_id or str(uuid.uuid4())
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.turns: list[Turn] = []

    def add_turn(self, role: str, content: str) -> None:
        self.turns.append(Turn(role=role, content=content))

    def recent_turns(self, limit: int = 10) -> list[Turn]:
        return self.turns[-limit:]

    def clear(self) -> None:
        self.turns.clear()
