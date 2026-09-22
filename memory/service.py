"""High-level memory operations.

Two orthogonal axes on every memory:
- `type` (MemoryType): WHAT kind of fact it is — preference/habit/project/goal/fact.
- `category` (MemoryCategory): WHERE it belongs in the memory architecture —
  identity (durable facts about who the user is), permanent (preferences/projects/
  goals/habits with no expiry) or temporary (session-scoped, expires on its own).
  SESSION context is deliberately NOT a memory category — that's SessionManager's
  and conversation_history's job, unchanged since V1; mixing "the current chat" into
  the same table as durable facts is exactly what the memory architecture forbids.

Updates go through `remember()`: given a `key`, it supersedes (soft-deletes the old
row, inserts a new one with the same key) instead of leaving two conflicting facts
both "active" — the old row stays in the table for audit, just no longer counted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from memory.database import Database
from memory.smart_recall import MemoryItem, SmartRecall

MAX_SUMMARY_CONTENT_CHARS = 200


class MemoryType(str, Enum):
    PREFERENCE = "preference"
    HABIT = "habit"
    PROJECT = "project"
    GOAL = "goal"
    FACT = "fact"


class MemoryCategory(str, Enum):
    IDENTITY = "identity"
    PERMANENT = "permanent"
    TEMPORARY = "temporary"


class MemorySource(str, Enum):
    USER = "user"  # stated by the user, captured without an explicit "remember" command
    EXPLICIT_COMMAND = "explicit_command"  # "lembre que...", "guarde isso..."
    INFERRED = "inferred"  # the model inferred it from context (used sparingly)
    SYSTEM = "system"


@dataclass
class MemoryRecord:
    id: int
    type: str
    content: str
    important: bool
    metadata: dict
    created_at: str
    updated_at: str
    category: str
    key: str | None
    source: str
    confidence: float
    expires_at: str | None
    active: bool

    @classmethod
    def from_row(cls, row) -> "MemoryRecord":
        return cls(
            id=row["id"],
            type=row["type"],
            content=row["content"],
            important=bool(row["important"]),
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            category=row["category"],
            key=row["key"],
            source=row["source"],
            confidence=row["confidence"],
            expires_at=row["expires_at"],
            active=bool(row["active"]),
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        now = now or datetime.now(timezone.utc)
        return datetime.fromisoformat(self.expires_at) <= now


@dataclass
class HistoryRecord:
    id: int
    session_id: str
    role: str
    content: str
    created_at: str

    @classmethod
    def from_row(cls, row) -> "HistoryRecord":
        return cls(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            created_at=row["created_at"],
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


#: Stateless (min_score/fetch_all_fn are read-only config, no mutable state), so one
#: shared instance is safe across every MemoryService/thread — see _to_memory_item()
#: for why the fields below are adapted this way, not copied verbatim from the
#: Pack 2 integration guide's own example.
_smart_recall = SmartRecall()


def _to_memory_item(record: MemoryRecord) -> MemoryItem:
    """Adapts Steve's real MemoryRecord (V1.2 SQLite schema) to smart_recall.MemoryItem
    — field-by-field, never inventing data the schema doesn't have:
    - importance: MemoryRecord.important is a bool, not a 0..1 float. Mapped to 1.0
      when explicitly marked important, 0.3 otherwise — deliberately NOT SmartRecall's
      own 0.5 "unspecified" default: with min_score=0.12 and SmartRecall's 0.25 weight
      on importance, a 0.5 floor alone contributes 0.125 (> 0.12) regardless of term
      overlap or recency, so nothing not-important would ever actually get filtered
      out — min_score would only ever re-rank, never exclude, silently defeating the
      one behavior change this integration is supposed to add (see
      docs/reports/PACK2_TOOL_FILLER_SMART_RECALL_REPORT.md's "Smart Recall realmente
      filtra?" section for the arithmetic). 0.3 keeps a real, old, zero-overlap memory
      excludable while still leaving recency alone enough to keep a fresh one in.
    - created_at: sourced from `updated_at`, not `created_at` — matches the precedent
      this file's own get_relevant_memories() no-query fallback already set (sorts by
      `updated_at`), since remember()'s upsert-by-key semantics make `updated_at` mean
      "last reaffirmed," the more meaningful recency signal for ranking.
    - tags: MemoryRecord has no tags column. `type`/`category` (e.g. "preference",
      "permanent") are real classification words already on the record, reused as
      pseudo-tags — free signal, not fabricated.
    """
    try:
        recency_at = datetime.fromisoformat(record.updated_at)
    except ValueError:
        recency_at = None
    return MemoryItem(
        id=record.id,
        content=record.content,
        tags=[record.type, record.category],
        created_at=recency_at,
        importance=1.0 if record.important else 0.3,
        source=record.category,
    )


class MemoryService:
    def __init__(self, database: Database):
        self.db = database

    # --- low-level CRUD (unchanged contract from V1, extended with V1.2 fields) ----
    def create_memory(
        self,
        type: MemoryType,
        content: str,
        important: bool = False,
        metadata: dict[str, Any] | None = None,
        category: MemoryCategory = MemoryCategory.PERMANENT,
        key: str | None = None,
        source: MemorySource = MemorySource.USER,
        confidence: float = 1.0,
        expires_at: str | None = None,
    ) -> int:
        now = _now()
        cursor = self.db.execute(
            """INSERT INTO memories
               (type, content, important, metadata, created_at, updated_at,
                category, key, source, confidence, expires_at, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                MemoryType(type).value,
                content,
                int(important),
                json.dumps(metadata or {}, ensure_ascii=False),
                now,
                now,
                MemoryCategory(category).value,
                key,
                MemorySource(source).value,
                confidence,
                expires_at,
            ),
        )
        return cursor.lastrowid

    def get_memory(self, memory_id: int) -> MemoryRecord | None:
        rows = self.db.query("SELECT * FROM memories WHERE id = ?", (memory_id,))
        return MemoryRecord.from_row(rows[0]) if rows else None

    def update_memory(
        self,
        memory_id: int,
        content: str | None = None,
        important: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        existing = self.get_memory(memory_id)
        if existing is None:
            return False
        self.db.execute(
            """UPDATE memories SET content = ?, important = ?, metadata = ?, updated_at = ?
               WHERE id = ?""",
            (
                content if content is not None else existing.content,
                int(important if important is not None else existing.important),
                json.dumps(metadata if metadata is not None else existing.metadata, ensure_ascii=False),
                _now(),
                memory_id,
            ),
        )
        return True

    def mark_important(self, memory_id: int, important: bool = True) -> bool:
        return self.update_memory(memory_id, important=important)

    def delete_memory(self, memory_id: int) -> bool:
        """Hard delete — removes the row entirely. Prefer `forget_by_key`/
        `forget_matching` for anything the user asked Steve to forget, which
        soft-delete (keep the row, mark inactive) so there's still an audit trail."""
        cursor = self.db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        return cursor.rowcount > 0

    def forget(self, memory_id: int) -> bool:
        return self.delete_memory(memory_id)

    # --- V1.2: category/key-aware querying ------------------------------------------
    def list_memories(
        self,
        type: MemoryType | None = None,
        important_only: bool = False,
        category: MemoryCategory | None = None,
        active_only: bool = True,
        include_expired: bool = False,
    ) -> list[MemoryRecord]:
        query = "SELECT * FROM memories WHERE 1=1"
        params: list[Any] = []
        if type is not None:
            query += " AND type = ?"
            params.append(MemoryType(type).value)
        if category is not None:
            query += " AND category = ?"
            params.append(MemoryCategory(category).value)
        if important_only:
            query += " AND important = 1"
        if active_only:
            query += " AND active = 1"
        query += " ORDER BY updated_at DESC"
        records = [MemoryRecord.from_row(r) for r in self.db.query(query, tuple(params))]
        if not include_expired:
            records = [r for r in records if not r.is_expired()]
        return records

    def search_memories(self, text: str) -> list[MemoryRecord]:
        rows = self.db.query(
            "SELECT * FROM memories WHERE content LIKE ? AND active = 1 ORDER BY updated_at DESC",
            (f"%{text}%",),
        )
        return [r for r in (MemoryRecord.from_row(row) for row in rows) if not r.is_expired()]

    def get_active_by_key(self, key: str) -> MemoryRecord | None:
        rows = self.db.query(
            "SELECT * FROM memories WHERE key = ? AND active = 1 ORDER BY updated_at DESC LIMIT 1",
            (key,),
        )
        if not rows:
            return None
        record = MemoryRecord.from_row(rows[0])
        return None if record.is_expired() else record

    def remember(
        self,
        content: str,
        type: MemoryType = MemoryType.FACT,
        category: MemoryCategory = MemoryCategory.PERMANENT,
        key: str | None = None,
        source: MemorySource = MemorySource.EXPLICIT_COMMAND,
        confidence: float = 1.0,
        important: bool = False,
        expires_in_hours: float | None = None,
    ) -> MemoryRecord:
        """Create-or-update: if `key` matches an existing active memory, that one is
        superseded (soft-deleted, not overwritten in place) and a new row with the
        same key takes over as the active version — this is what turns "na verdade
        agora prefiro Y" into an update instead of a second, conflicting memory."""
        expires_at = None
        if expires_in_hours is not None:
            expires_at = (datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)).isoformat()

        if key:
            previous = self.get_active_by_key(key)
            if previous is not None:
                self.db.execute("UPDATE memories SET active = 0, updated_at = ? WHERE id = ?", (_now(), previous.id))

        new_id = self.create_memory(
            type=type,
            content=content,
            important=important,
            category=category,
            key=key,
            source=source,
            confidence=confidence,
            expires_at=expires_at,
        )
        return self.get_memory(new_id)

    def forget_by_key(self, key: str) -> bool:
        """Soft-deletes the active memory with this key, if any. Returns False if
        nothing active matched."""
        record = self.get_active_by_key(key)
        if record is None:
            return False
        self.db.execute("UPDATE memories SET active = 0, updated_at = ? WHERE id = ?", (_now(), record.id))
        return True

    def forget_matching(self, search_text: str) -> int:
        """Soft-deletes every active memory whose content or key contains
        `search_text` (case-insensitive) — 'esqueça tudo sobre X'. Returns how many
        were forgotten."""
        rows = self.db.query(
            "SELECT id, key, content FROM memories WHERE active = 1 AND (content LIKE ? OR key LIKE ?)",
            (f"%{search_text}%", f"%{search_text}%"),
        )
        now = _now()
        for row in rows:
            self.db.execute("UPDATE memories SET active = 0, updated_at = ? WHERE id = ?", (now, row["id"]))
        return len(rows)

    def purge_expired(self) -> int:
        """Hard-deletes rows past their expiry — routine hygiene, not a hidden
        background job: called once at startup (see main.build_app_services) and
        safe to call any time. Active-only filtering already hides expired rows
        from every read path even if this is never called."""
        now = datetime.now(timezone.utc)
        rows = self.db.query("SELECT id, expires_at FROM memories WHERE expires_at IS NOT NULL")
        expired_ids = [row["id"] for row in rows if datetime.fromisoformat(row["expires_at"]) <= now]
        for memory_id in expired_ids:
            self.db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        return len(expired_ids)

    def get_relevant_memories(
        self,
        query: str | None = None,
        limit: int = 8,
        category: MemoryCategory | None = None,
    ) -> list[MemoryRecord]:
        """Selects a small, relevant subset instead of dumping every memory into the
        prompt. With no query, falls back to important-first/most-recent (V1
        behavior, unchanged). With a query (the user's current message), ranks via
        smart_recall.SmartRecall — a weighted score (term overlap + importance +
        recency, see _to_memory_item()) instead of this file's original plain
        word-overlap count — so what's actually relevant to *this* message outranks an
        old important memory about something else, and a genuinely unrelated memory
        can now be excluded outright (SmartRecall.min_score) instead of always filling
        up to `limit` regardless of relevance."""
        candidates = self.list_memories(category=category, active_only=True, include_expired=False)

        if not query:
            candidates.sort(key=lambda m: (m.important, m.updated_at), reverse=True)
            return candidates[:limit]

        items = [_to_memory_item(m) for m in candidates]
        selected = _smart_recall.select(query, limit=limit, memories=items)
        by_id = {m.id: m for m in candidates}
        return [by_id[item.id] for item in selected if item.id in by_id]

    def summarize_for_prompt(self, query: str | None = None, limit: int = 8) -> str:
        memories = self.get_relevant_memories(query=query, limit=limit)
        if not memories:
            return ""
        lines = []
        for m in memories:
            content = m.content
            if len(content) > MAX_SUMMARY_CONTENT_CHARS:
                content = content[:MAX_SUMMARY_CONTENT_CHARS].rstrip() + "..."
            lines.append(f"- ({m.category}/{m.type}) {content}")
        return "\n".join(lines)

    # --- conversation history (session-layer persistence — unchanged from V1) ------
    def add_history(self, session_id: str, role: str, content: str) -> int:
        cursor = self.db.execute(
            "INSERT INTO conversation_history (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, _now()),
        )
        return cursor.lastrowid

    def get_recent_history(self, session_id: str, limit: int = 10) -> list[HistoryRecord]:
        rows = self.db.query(
            """SELECT * FROM conversation_history WHERE session_id = ?
               ORDER BY id DESC LIMIT ?""",
            (session_id, limit),
        )
        return [HistoryRecord.from_row(r) for r in reversed(rows)]
