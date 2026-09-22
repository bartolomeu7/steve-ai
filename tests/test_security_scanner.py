"""Unit tests for security/scanner.py — Quick/File/Folder scans, cancellation, and the
EventBus events they publish."""
from __future__ import annotations

import threading

from core.events import EventBus
from security.events import (
    SECURITY_FINDING_CREATED,
    SECURITY_SCAN_CANCELLED,
    SECURITY_SCAN_COMPLETED,
    SECURITY_SCAN_PROGRESS,
    SECURITY_SCAN_STARTED,
)
from security.scanner import SecurityScanner
from system_monitor.models import ProcessInfo


def _process(pid, executable_path) -> ProcessInfo:
    return ProcessInfo(pid=pid, name="x", cpu_percent=1.0, memory_percent=1.0, memory_bytes=1, status="running", executable_path=executable_path)


def test_scan_file_path_publishes_started_and_completed(tmp_path):
    path = tmp_path / "safe.txt"
    path.write_text("hi", encoding="utf-8")
    bus = EventBus()
    started, completed = [], []
    bus.subscribe(SECURITY_SCAN_STARTED, started.append)
    bus.subscribe(SECURITY_SCAN_COMPLETED, completed.append)

    scanner = SecurityScanner(bus)
    summary = scanner.scan_file_path(str(path))

    assert len(started) == 1
    assert len(completed) == 1
    assert summary.files_scanned == 1
    assert summary.cancelled is False


def test_quick_scan_deduplicates_and_ignores_processes_without_path():
    bus = EventBus()
    scanner = SecurityScanner(bus)
    processes = [
        _process(1, r"C:\App\a.exe"),
        _process(2, r"C:\App\a.exe"),  # same path -- must only be scanned once
        _process(3, None),
    ]
    summary = scanner.quick_scan(processes)
    assert summary.files_scanned == 1


def test_scan_folder_path_scans_files_within(tmp_path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.txt").write_text("c", encoding="utf-8")

    scanner = SecurityScanner(EventBus())
    summary = scanner.scan_folder_path(str(tmp_path))

    assert summary.files_scanned == 3
    assert summary.error is None


def test_scan_folder_path_missing_folder_is_a_clean_error():
    scanner = SecurityScanner(EventBus())
    summary = scanner.scan_folder_path(r"Z:\this\does\not\exist")
    assert summary.error is not None
    assert summary.files_scanned == 0


def test_scan_folder_path_respects_max_files(tmp_path):
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    scanner = SecurityScanner(EventBus())
    summary = scanner.scan_folder_path(str(tmp_path), max_files=3)
    assert summary.files_scanned == 3


def test_suspicious_file_publishes_finding_created(tmp_path):
    import os
    import tempfile

    suspicious_path = os.path.join(tempfile.gettempdir(), "invoice.pdf.exe")
    with open(suspicious_path, "wb") as handle:
        handle.write(b"x" * 10)
    try:
        bus = EventBus()
        findings_seen = []
        bus.subscribe(SECURITY_FINDING_CREATED, findings_seen.append)
        scanner = SecurityScanner(bus)

        summary = scanner.scan_file_path(suspicious_path)

        assert len(summary.findings) == 1
        assert len(findings_seen) == 1
        assert findings_seen[0]["finding"] is summary.findings[0]
    finally:
        os.remove(suspicious_path)


def test_cancel_stops_a_running_scan(tmp_path):
    """Synchronized with a real threading.Event, never an arbitrary sleep: the scanner
    thread signals it has started, the test releases it to cancel mid-scan, and the
    result is checked deterministically."""
    for i in range(20):
        (tmp_path / f"f{i}.txt").write_text("x" * 1000, encoding="utf-8")

    bus = EventBus()
    scanner = SecurityScanner(bus)
    progress_events = []
    cancelled_events = []
    bus.subscribe(SECURITY_SCAN_PROGRESS, progress_events.append)
    bus.subscribe(SECURITY_SCAN_CANCELLED, cancelled_events.append)

    first_progress = threading.Event()

    def _watch_progress(_data):
        first_progress.set()

    bus.subscribe(SECURITY_SCAN_PROGRESS, _watch_progress)

    result_holder = {}

    def _run():
        result_holder["summary"] = scanner.scan_folder_path(str(tmp_path))

    thread = threading.Thread(target=_run)
    thread.start()
    assert first_progress.wait(timeout=5), "scan never reported any progress"
    scanner.cancel()
    thread.join(timeout=5)

    summary = result_holder["summary"]
    assert summary.cancelled is True
    assert len(cancelled_events) == 1
    assert summary.files_scanned < 20  # must have stopped before finishing all 20


def test_concurrent_scan_is_refused_not_racing(tmp_path, monkeypatch):
    """Mirrors system_monitor/service.py's _lifecycle_lock guarantee: at most one scan
    runs at a time, enforced by the scanner itself, not merely assumed by callers.
    Synchronized with real threading.Events (never an arbitrary sleep) so the second
    call is deterministically issued while the first is provably still scanning."""
    (tmp_path / "f0.txt").write_text("x", encoding="utf-8")

    first_scan_started = threading.Event()
    release_first_scan = threading.Event()
    real_scan_file = __import__("security.scanner", fromlist=["scan_file"]).scan_file

    def _blocking_scan_file(path):
        first_scan_started.set()
        assert release_first_scan.wait(timeout=5), "test setup did not release in time"
        return real_scan_file(path)

    monkeypatch.setattr("security.scanner.scan_file", _blocking_scan_file)

    scanner = SecurityScanner(EventBus())
    first_result = {}

    def _run_first():
        first_result["summary"] = scanner.scan_file_path(str(tmp_path / "f0.txt"))

    thread = threading.Thread(target=_run_first)
    thread.start()
    assert first_scan_started.wait(timeout=5), "first scan never started"
    assert scanner.is_scanning is True

    second_summary = scanner.scan_file_path(str(tmp_path / "f0.txt"))  # called while first is still blocked

    release_first_scan.set()
    thread.join(timeout=5)

    assert second_summary.error == "Um scan já está em andamento."
    assert first_result["summary"].error is None
    assert first_result["summary"].files_scanned == 1
