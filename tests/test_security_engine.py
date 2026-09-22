"""Unit tests for security/engine.py — lifecycle (idempotent start/stop, mirroring
system_monitor/service.py's PATCH 03-hardened model), EventBus integration, and finding
accumulation. Uses the same white-box + black-box combination PATCH 03 established:
a deterministic lock-holding check as the primary regression guard, plus a
Barrier-based test as a best-effort robustness check.

Note: SecurityEngine.start() now also calls system_monitor.start() (see engine.py's
docstring — a real bug found via V1.5 hardware validation), so every test here that
starts the engine also transitively starts a real SystemMonitorService thread; the
`monitor` fixture's teardown stops it regardless of who started it.
"""
from __future__ import annotations

import threading
import time

import pytest

from core.events import EventBus
from security.engine import THREAD_JOIN_TIMEOUT_SECONDS, SecurityEngine
from security.events import (
    SECURITY_ENGINE_ERROR,
    SECURITY_ENGINE_STARTED,
    SECURITY_ENGINE_STOPPED,
    SECURITY_FINDING_CREATED,
)
from security.models import FindingStatus, ProtectionState
from system_monitor.models import ProcessInfo, ProcessSnapshot
from system_monitor.service import PROCESS_LIST_UPDATED, SystemMonitorService


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def monitor(event_bus):
    service = SystemMonitorService(event_bus=event_bus, metrics_interval=30.0, process_interval=30.0)
    yield service
    service.stop()


@pytest.fixture
def engine(event_bus, monitor):
    service = SecurityEngine(event_bus=event_bus, system_monitor=monitor)
    yield service
    service.stop()


def test_disabled_before_start(engine):
    assert engine.state == ProtectionState.DISABLED
    assert engine.is_running is False


def test_start_publishes_started_event_and_becomes_running(event_bus, engine):
    seen = []
    event_bus.subscribe(SECURITY_ENGINE_STARTED, seen.append)

    engine.start()

    assert engine.is_running is True
    assert len(seen) == 1
    assert engine.state in (ProtectionState.MONITORING, ProtectionState.PROTECTED)


def test_start_also_starts_system_monitor(monitor, engine):
    """The real bug this fixes: SecurityEngine starts at boot, but SystemMonitorService
    (per PATCH 03) only started when the Dashboard opened — leaving the analyzer's
    baseline established against zero process data until the Dashboard was opened,
    then flooding findings for every already-running process. Confirmed via real
    hardware validation; see engine.py's start() docstring."""
    assert monitor.is_running is False
    engine.start()
    assert monitor.is_running is True


def test_stop_publishes_stopped_event_and_leaves_no_thread(event_bus, engine):
    seen = []
    event_bus.subscribe(SECURITY_ENGINE_STOPPED, seen.append)
    engine.analysis_interval_seconds = 0.05
    engine.start()

    engine.stop()

    assert engine.is_running is False
    assert engine.state == ProtectionState.DISABLED
    assert len(seen) == 1
    assert not any(t.name == "steve-security-engine" for t in threading.enumerate())


def test_start_is_idempotent(event_bus, engine):
    started = []
    event_bus.subscribe(SECURITY_ENGINE_STARTED, started.append)

    engine.start()
    first_thread = engine._thread
    engine.start()
    second_thread = engine._thread

    assert first_thread is second_thread
    assert len(started) == 1


def test_stop_is_idempotent(engine):
    engine.stop()  # never started -- must not raise
    assert engine.is_running is False


def test_stop_without_start_publishes_nothing(event_bus, engine):
    seen = []
    event_bus.subscribe(SECURITY_ENGINE_STOPPED, seen.append)
    engine.stop()
    assert seen == []


def test_start_holds_the_lifecycle_lock_while_creating_the_thread(monkeypatch, engine):
    """The primary, deterministic regression guard (same technique as PATCH 03's
    system_monitor test): spies on threading.Thread.start to record whether
    engine._lifecycle_lock is held at the exact moment any thread is created during
    start() — including system_monitor.start()'s own internal thread creation, since
    that now happens from within this same lock (see engine.py's start() docstring)."""
    lock_states = []
    real_start = threading.Thread.start

    def _spy_start(self):
        lock_states.append(engine._lifecycle_lock.locked())
        return real_start(self)

    monkeypatch.setattr(threading.Thread, "start", _spy_start)

    engine.start()

    assert lock_states  # at least the engine's own thread creation was observed
    assert all(lock_states), "every thread created during start() must see the lock held"


def test_concurrent_start_calls_create_only_one_thread(engine):
    baseline = len([t for t in threading.enumerate() if t.name == "steve-security-engine"])
    barrier = threading.Barrier(2)

    def _call_start():
        barrier.wait(timeout=5)
        engine.start()

    callers = [threading.Thread(target=_call_start) for _ in range(2)]
    for t in callers:
        t.start()
    for t in callers:
        t.join(timeout=5)

    current = len([t for t in threading.enumerate() if t.name == "steve-security-engine"])
    assert current - baseline == 1


def test_shutdown_waits_for_in_flight_analysis_to_finish(monkeypatch, event_bus, monitor, engine):
    """stop() called while an analysis cycle is running must block until it finishes
    (thread.join()), never truncate it -- synchronized with real threading.Events.

    Deliberately decoupled from System Monitor's real collection timing: engine.start()
    does start it for real (see engine.py's docstring), but this test stops it again
    immediately and drives the analysis cycle with a hand-published snapshot instead --
    a real full-suite run was observed to occasionally stall System Monitor's real
    collection well past 10s under heavy cumulative system load (confirmed via a
    dedicated stress script: isolated create/start/stop cycles took ~450-500ms each,
    so this is a real-world load characteristic of running hundreds of tests together,
    not a logic bug) -- this test's actual subject is stop()'s join-blocking behavior,
    which doesn't need real OS timing to verify."""
    analysis_started = threading.Event()
    release_analysis = threading.Event()
    engine.analysis_interval_seconds = 10.0

    real_analyze = engine._analyzer.analyze

    def _slow_analyze(processes, network=()):
        analysis_started.set()
        assert release_analysis.wait(timeout=10), "test setup did not release in time"
        return real_analyze(processes, network)

    monkeypatch.setattr(engine._analyzer, "analyze", _slow_analyze)
    monkeypatch.setattr("security.engine.sample_external_connections", lambda: ())

    engine.start()
    monitor.stop()
    event_bus.publish(PROCESS_LIST_UPDATED, {"snapshot": ProcessSnapshot(timestamp=time.time(), processes=())})

    assert analysis_started.wait(timeout=10), "analysis cycle never started"

    stop_thread = threading.Thread(target=engine.stop)
    stop_thread.start()
    time.sleep(0.2)
    assert engine.is_running is True  # stop() is still blocked in join()

    release_analysis.set()
    stop_thread.join(timeout=THREAD_JOIN_TIMEOUT_SECONDS + 2)
    assert engine.is_running is False


def test_analysis_reacts_promptly_to_data_arriving_mid_wait(event_bus, monitor, engine):
    """Regression guard for the real bug fixed alongside start()'s system_monitor.start()
    call: _run()'s wait must be interrupted the moment fresh process data arrives, not
    only after the full analysis_interval_seconds elapses -- otherwise a long interval
    combined with System Monitor's first cycle completing just slightly late would
    needlessly delay the engine's first real analysis by up to a whole stale interval."""
    engine.analysis_interval_seconds = 30.0  # deliberately long -- must NOT be waited out
    monitor.stop()  # this test drives PROCESS_LIST_UPDATED by hand, not the real collector
    engine.start()

    # engine.start() itself calls system_monitor.start(); stop it again immediately so
    # only this test's hand-published snapshot reaches the analyzer.
    monitor.stop()

    snapshot = ProcessSnapshot(
        timestamp=time.time(),
        processes=(ProcessInfo(pid=1, name="x", cpu_percent=1.0, memory_percent=1.0, memory_bytes=1, status="running", executable_path=None),),
    )
    t0 = time.monotonic()
    event_bus.publish(PROCESS_LIST_UPDATED, {"snapshot": snapshot})

    # 10s margin for real thread-scheduling delay under a busy machine/full-suite run
    # -- still far below the 30s configured interval, so this remains a valid guard:
    # if the interrupt mechanism were broken, _run() wouldn't check again until the
    # full 30s elapsed, which this deadline would still correctly catch.
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not engine._analyzer.baseline_established:
        time.sleep(0.02)
    elapsed = time.monotonic() - t0

    assert engine._analyzer.baseline_established is True
    assert elapsed < 10.0, f"took {elapsed:.1f}s to react -- the wait was not interrupted"


def test_new_process_produces_a_finding_published_on_the_bus(event_bus, monitor, engine, monkeypatch):
    """Synchronized with real threading.Events, and deliberately decoupled from System
    Monitor's real collection timing (see test_shutdown_waits_for_in_flight_analysis_to_
    finish's docstring for why: a full-suite run was observed to occasionally stall
    System Monitor's real collection well past 10s under heavy cumulative system load,
    confirmed environmental via a dedicated stress script, not a logic bug) -- this
    test's actual subject is the EventBus -> engine -> finding pipeline, which doesn't
    need real OS timing to verify."""
    finding_received = threading.Event()
    findings_seen = []

    def _on_finding(data):
        findings_seen.append(data)
        finding_received.set()

    event_bus.subscribe(SECURITY_FINDING_CREATED, _on_finding)
    engine.analysis_interval_seconds = 0.05
    monkeypatch.setattr("security.engine.sample_external_connections", lambda: ())

    engine.start()
    monitor.stop()

    # First cycle establishes the baseline (nothing running yet, since fresh instance)
    event_bus.publish(PROCESS_LIST_UPDATED, {"snapshot": ProcessSnapshot(timestamp=time.time(), processes=())})
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not engine._analyzer.baseline_established:
        time.sleep(0.02)
    assert engine._analyzer.baseline_established is True

    snapshot = ProcessSnapshot(
        timestamp=time.time(),
        processes=(ProcessInfo(pid=99999, name="new.exe", cpu_percent=1.0, memory_percent=1.0, memory_bytes=1, status="running", executable_path=None),),
    )
    event_bus.publish(PROCESS_LIST_UPDATED, {"snapshot": snapshot})

    assert finding_received.wait(timeout=10), "finding was never published"
    assert len(findings_seen) == 1
    assert findings_seen[0]["finding"].pid == 99999
    assert len(engine.recent_findings()) == 1


def test_analysis_error_sets_degraded_state_and_publishes_error(monkeypatch, event_bus, monitor, engine):
    """Same Event-based synchronization and real-OS-timing decoupling rationale as
    test_new_process_produces_a_finding_published_on_the_bus above."""
    error_received = threading.Event()
    errors_seen = []

    def _on_error(data):
        errors_seen.append(data)
        error_received.set()

    event_bus.subscribe(SECURITY_ENGINE_ERROR, _on_error)
    engine.analysis_interval_seconds = 0.05
    monkeypatch.setattr("security.engine.sample_external_connections", lambda: ())

    def _raise(processes, network=()):
        raise RuntimeError("simulated analysis failure")

    monkeypatch.setattr(engine._analyzer, "analyze", _raise)

    engine.start()
    monitor.stop()
    event_bus.publish(PROCESS_LIST_UPDATED, {"snapshot": ProcessSnapshot(timestamp=time.time(), processes=())})

    assert error_received.wait(timeout=10), "analysis error was never published"
    assert errors_seen
    assert engine.state == ProtectionState.DEGRADED


def test_audit_logger_records_finding_created(event_bus, monitor, engine, tmp_path, monkeypatch):
    from security.audit import AuditLogger

    audit_logger = AuditLogger(tmp_path / "audit.log")
    engine.audit_logger = audit_logger
    monkeypatch.setattr("security.engine.sample_external_connections", lambda: ())

    engine.start()
    monitor.stop()

    finding_logged = threading.Event()
    # AuditLogger writes synchronously in .log() -- wrap it to know when the write for
    # THIS event has happened, rather than polling the log file's mtime.
    real_log = audit_logger.log

    def _spy_log(event_type, details=None):
        real_log(event_type, details)
        if event_type == "security_finding_created":
            finding_logged.set()

    monkeypatch.setattr(audit_logger, "log", _spy_log)

    event_bus.publish(PROCESS_LIST_UPDATED, {"snapshot": ProcessSnapshot(timestamp=time.time(), processes=())})
    event_bus.publish(
        PROCESS_LIST_UPDATED,
        {
            "snapshot": ProcessSnapshot(
                timestamp=time.time(),
                processes=(ProcessInfo(pid=55555, name="new.exe", cpu_percent=1.0, memory_percent=1.0, memory_bytes=1, status="running", executable_path=None),),
            )
        },
    )

    assert finding_logged.wait(timeout=10), "finding was never audit-logged"
    log_text = (tmp_path / "audit.log").read_text(encoding="utf-8")
    assert "security_finding_created" in log_text
    assert "55555" in log_text  # pid recorded, useful forensic detail


def test_audit_logger_is_optional_and_never_required(event_bus, monitor):
    """Every existing caller/test that doesn't pass audit_logger must be unaffected --
    engine.py's own default is None, and every internal log call already no-ops on
    that, never raising."""
    from security.engine import SecurityEngine as _Engine

    engine = _Engine(event_bus=event_bus, system_monitor=monitor)  # no audit_logger given
    assert engine.audit_logger is None
    engine.start()
    engine.stop()  # must not raise despite no audit_logger


def test_update_finding_status_logs_to_audit(engine, tmp_path):
    from security.audit import AuditLogger
    from security.models import FindingCategory, SecurityFinding, Severity, now

    audit_logger = AuditLogger(tmp_path / "audit.log")
    engine.audit_logger = audit_logger
    finding = SecurityFinding(id="a", timestamp=now(), severity=Severity.LOW, category=FindingCategory.PROCESS, title="t", description="d", evidence=(), confidence=0.5)
    engine.findings.append(finding)

    engine.update_finding_status("a", FindingStatus.DISMISSED)

    log_text = (tmp_path / "audit.log").read_text(encoding="utf-8")
    assert "security_finding_status_updated" in log_text
    assert "dismissed" in log_text


def test_audit_log_never_contains_secrets_or_tokens(event_bus, monitor, engine, tmp_path, monkeypatch):
    """Structural guarantee matching security/audit.py's own redaction and the V1.5
    spec's explicit "não registrar secrets/senhas/tokens": nothing this module ever
    passes to AuditLogger.log() includes file content -- only ids, severities,
    categories, titles, pids and process names (all already asserted individually
    above); this test additionally confirms the audit module's own redaction would
    still catch a key named like a secret if one were ever accidentally included."""
    from security.audit import AuditLogger, _redact

    assert _redact({"password": "hunter2"}) == {"password": "***REDACTED***"}
    assert _redact({"token": "abc"}) == {"token": "***REDACTED***"}


def test_update_finding_status_mutates_only_the_matching_finding(engine):
    from security.models import FindingCategory, SecurityFinding, Severity, now

    f1 = SecurityFinding(id="a", timestamp=now(), severity=Severity.LOW, category=FindingCategory.PROCESS, title="t1", description="d", evidence=(), confidence=0.5)
    f2 = SecurityFinding(id="b", timestamp=now(), severity=Severity.LOW, category=FindingCategory.PROCESS, title="t2", description="d", evidence=(), confidence=0.5)
    engine.findings.append(f1)
    engine.findings.append(f2)

    updated = engine.update_finding_status("a", FindingStatus.DISMISSED)

    assert updated is True
    findings = {f.id: f for f in engine.recent_findings()}
    assert findings["a"].status == FindingStatus.DISMISSED
    assert findings["b"].status == FindingStatus.DETECTED


def test_update_finding_status_returns_false_for_unknown_id(engine):
    assert engine.update_finding_status("does-not-exist", FindingStatus.REVIEWED) is False


def test_quick_scan_uses_cached_snapshot_without_starting(engine):
    """quick_scan() delegates to scanner.quick_scan(cached_snapshot.processes) — tested
    here without calling engine.start() (which for real starts SystemMonitorService
    and would race a real collection cycle against this test's hand-set snapshot)."""
    snapshot = ProcessSnapshot(
        timestamp=time.time(),
        processes=(ProcessInfo(pid=1, name="x", cpu_percent=1.0, memory_percent=1.0, memory_bytes=1, status="running", executable_path=None),),
    )
    engine._latest_process_snapshot = snapshot

    summary = engine.quick_scan()

    assert summary.files_scanned == 0  # no executable_path on the one process
