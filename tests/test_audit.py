from __future__ import annotations

import json

from security.audit import AuditLogger


def test_audit_logger_writes_json_lines(tmp_path):
    log_file = tmp_path / "audit.log"
    logger = AuditLogger(log_file)

    logger.log_tool_execution("open_application", {"app_name": "notepad"}, True, True, "notepad aberto.")

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["event_type"] == "tool_execution"
    assert entry["details"]["tool"] == "open_application"


def test_audit_logger_redacts_secrets(tmp_path):
    log_file = tmp_path / "audit.log"
    logger = AuditLogger(log_file)

    logger.log("test_event", {"password": "hunter2", "note": "ok"})

    entry = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert entry["details"]["password"] == "***REDACTED***"
    assert entry["details"]["note"] == "ok"
