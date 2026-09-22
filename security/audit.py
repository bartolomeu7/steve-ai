"""Append-only audit trail for tool executions and sensitive decisions. Never logs secrets."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REDACT_KEYS = {"password", "token", "api_key", "apikey", "secret", "key"}


def _redact(data: Any) -> Any:
    if isinstance(data, dict):
        return {
            k: ("***REDACTED***" if k.lower() in _REDACT_KEYS else _redact(v))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_redact(v) for v in data]
    return data


class AuditLogger:
    def __init__(self, log_file: Path):
        self.log_file = log_file
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event_type: str, details: dict | None = None) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "details": _redact(details or {}),
        }
        with self.log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def log_tool_execution(
        self, tool_name: str, parameters: dict, success: bool, verified: bool, message: str
    ) -> None:
        self.log(
            "tool_execution",
            {
                "tool": tool_name,
                "parameters": parameters,
                "success": success,
                "verified": verified,
                "message": message,
            },
        )

    def log_confirmation(self, message: str, approved: bool) -> None:
        self.log("confirmation", {"message": message, "approved": approved})
