"""Unit tests for tools/security.py — the AI-facing, read-only (mostly) Security
Center tools. Confirms the AI can only ever read evidence the engine already gathered,
never create a finding or trigger a destructive action (see security/engine.py's
module docstring and the V1.5 spec's "IA não deve ser a única fonte da detecção")."""
from __future__ import annotations

from core.events import EventBus
from security.engine import SecurityEngine
from security.models import FindingCategory, SecurityFinding, Severity, now
from system_monitor.service import SystemMonitorService
from tools.security import CheckSecurityStatusTool, ListSecurityFindingsTool, ScanFileTool


def _engine() -> SecurityEngine:
    bus = EventBus()
    monitor = SystemMonitorService(event_bus=bus, metrics_interval=30.0, process_interval=30.0)
    return SecurityEngine(event_bus=bus, system_monitor=monitor)


def _finding(finding_id="a", severity=Severity.LOW) -> SecurityFinding:
    return SecurityFinding(
        id=finding_id, timestamp=now(), severity=severity, category=FindingCategory.PROCESS,
        title="t", description="d", evidence=("motivo",), confidence=0.5,
        process_name="x.exe", pid=123,
    )


def test_check_security_status_reports_disabled_before_start():
    tool = CheckSecurityStatusTool(security_engine=_engine())
    result = tool.execute({})
    assert result.success is True
    assert result.data["state"] == "disabled"
    assert result.data["total_findings"] == 0


def test_check_security_status_counts_findings_by_severity():
    engine = _engine()
    engine.findings.append(_finding("a", Severity.LOW))
    engine.findings.append(_finding("b", Severity.HIGH))
    tool = CheckSecurityStatusTool(security_engine=engine)

    result = tool.execute({})

    assert result.data["total_findings"] == 2
    assert result.data["findings_by_severity"] == {"LOW": 1, "HIGH": 1}


def test_list_security_findings_returns_structured_evidence():
    engine = _engine()
    engine.findings.append(_finding("a"))
    tool = ListSecurityFindingsTool(security_engine=engine)

    result = tool.execute({"limit": 5})

    assert result.success is True
    assert len(result.data["findings"]) == 1
    entry = result.data["findings"][0]
    assert entry["evidence"] == ["motivo"]
    assert entry["severity"] == "LOW"
    assert entry["pid"] == 123


def test_list_security_findings_invalid_limit_is_rejected():
    tool = ListSecurityFindingsTool(security_engine=_engine())
    result = tool.execute({"limit": -1})
    assert result.success is False


def test_list_security_findings_never_lets_ai_create_a_finding():
    """Structural guarantee: this tool has no method that appends to engine.findings —
    only security/process_analysis.py and security/scanner.py ever do that."""
    tool = ListSecurityFindingsTool(security_engine=_engine())
    assert not hasattr(tool, "create_finding")
    assert not hasattr(tool, "add_finding")


def test_scan_file_tool_reports_safe_for_boring_file(tmp_path, monkeypatch):
    monkeypatch.setattr("security.rules.location_signal", lambda path: None)
    monkeypatch.setattr("security.rules.recently_created_signal", lambda metadata, reference_time=None: None)
    path = tmp_path / "safe.txt"
    path.write_text("hello", encoding="utf-8")

    tool = ScanFileTool(security_engine=_engine())
    result = tool.execute({"path": str(path)})

    assert result.success is True
    assert result.data["status"] == "safe"


def test_scan_file_tool_reports_suspicious_with_reasons():
    import os
    import tempfile

    suspicious_path = os.path.join(tempfile.gettempdir(), "invoice.pdf.exe")
    with open(suspicious_path, "wb") as handle:
        handle.write(b"x" * 10)
    try:
        tool = ScanFileTool(security_engine=_engine())
        result = tool.execute({"path": suspicious_path})
        assert result.success is True
        assert result.data["status"] == "suspicious"
        assert result.data["evidence"]
    finally:
        os.remove(suspicious_path)


def test_scan_file_tool_missing_path_is_rejected():
    tool = ScanFileTool(security_engine=_engine())
    result = tool.execute({})
    assert result.success is False


def test_scan_file_tool_never_executes_the_scanned_file(tmp_path):
    """The tool must only ever call scanner.scan_file_path (read + hash), never
    os.startfile/subprocess/exec on the given path."""
    import inspect

    import tools.security as tools_security_module

    source = inspect.getsource(tools_security_module)
    for forbidden in ("os.startfile", "subprocess.", "exec(", "eval("):
        assert forbidden not in source
