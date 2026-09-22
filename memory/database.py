"""Thin SQLite access layer. All SQL for Steve's memory lives in this module."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    important INTEGER NOT NULL DEFAULT 0,
    metadata TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_important ON memories(important);

CREATE TABLE IF NOT EXISTS conversation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_history_session ON conversation_history(session_id);
"""

# V1.2 — memory/identity/context. Additive-only migration (no destructive changes to
# the schema or existing V1 data): each entry is (column, DDL-fragment, index-DDL).
# `category` is the IDENTITY/PERMANENT/TEMPORARY axis from the memory architecture;
# SESSION context deliberately does NOT live here — it stays in SessionManager /
# conversation_history, which already served that role since V1.
_MEMORY_COLUMNS_V1_2: list[tuple[str, str, str | None]] = [
    ("category", "TEXT NOT NULL DEFAULT 'permanent'", "CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);"),
    ("key", "TEXT", "CREATE INDEX IF NOT EXISTS idx_memories_key ON memories(key);"),
    ("source", "TEXT NOT NULL DEFAULT 'user'", None),
    ("confidence", "REAL NOT NULL DEFAULT 1.0", None),
    ("expires_at", "TEXT", None),
    ("active", "INTEGER NOT NULL DEFAULT 1", "CREATE INDEX IF NOT EXISTS idx_memories_active ON memories(active);"),
]


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        self._conn.executescript(SCHEMA)
        self._migrate_memories_v1_2()
        self._conn.commit()

    def _migrate_memories_v1_2(self) -> None:
        """Adds the V1.2 memory-architecture columns to an existing `memories` table
        if they aren't there yet — safe to run on a brand-new DB (SCHEMA above just
        created the table) or an existing V1/V1.1 database (columns get added, no
        rows touched, nothing dropped)."""
        existing_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(memories)")}
        for column, ddl, index_ddl in _MEMORY_COLUMNS_V1_2:
            if column not in existing_columns:
                self._conn.execute(f"ALTER TABLE memories ADD COLUMN {column} {ddl}")
            if index_ddl:
                self._conn.execute(index_ddl)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def execute(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        cursor = self._conn.execute(query, params)
        self._conn.commit()
        return cursor

    def query(self, query: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._conn.execute(query, params).fetchall()

    def close(self) -> None:
        self._conn.close()
