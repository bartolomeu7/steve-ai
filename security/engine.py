"""SecurityEngine: the Security Center's core service. Reuses SystemMonitorService's
already-collected process data (subscribes to PROCESS_LIST_UPDATED on the shared
EventBus — never a second psutil.process_iter() loop) and samples network connections
itself (security/network_analysis.py — genuinely new data System Monitor doesn't
already have), turning both into SecurityFindings via security/process_analysis.py.

Lifecycle deliberately mirrors system_monitor/service.py's PATCH 03-hardened model:
idempotent start()/stop() under a `_lifecycle_lock` (same "check-then-act must not
race" justification), a daemon thread, a stop Event, and a bounded `thread.join()` with
loud logging if it's ever exceeded — the same invariants matter here for the same
reasons (no duplicate thread, no orphan thread, clean shutdown). This must not be
reimplemented differently "for security" — the pattern is already correct.

Unlike SystemMonitorService (which only starts when the Dashboard is first opened —
see PATCH 03's confirmed architecture), SecurityEngine starts as part of
main.build_app_services() and stops only at full Steve shutdown: "real-time
protection" that only protects while a dashboard window happens to be open would not
be real-time protection. See docs/ARCHITECTURE.md's Security Center section.
"""
from __future__ import annotations

import dataclasses
import logging
import threading
from collections import deque

from core.events import EventBus
from security.audit import AuditLogger
from security.events import (
    SECURITY_ENGINE_ERROR,
    SECURITY_ENGINE_STARTED,
    SECURITY_ENGINE_STOPPED,
    SECURITY_FINDING_CREATED,
    SECURITY_SCAN_COMPLETED,
    SECURITY_SCAN_STARTED,
)
from security.models import FindingStatus, ProtectionState, SecurityFinding
from security.network_analysis import sample_external_connections
from security.process_analysis import ProcessAnalyzer
from security.scanner import SecurityScanner
from system_monitor.models import ProcessSnapshot
from system_monitor.service import PROCESS_LIST_UPDATED, SystemMonitorService

logger = logging.getLogger("steve.security.engine")

DEFAULT_ANALYSIS_INTERVAL_SECONDS = 5.0
#: Bounded history, same rationale as system_monitor/service.py's HISTORY_LENGTH — a
#: deque so old findings age out automatically instead of growing forever over a
#: long-running Steve process.
MAX_FINDINGS_HISTORY = 200
#: See system_monitor/service.py's THREAD_JOIN_TIMEOUT_SECONDS docstring for the same
#: reasoning: the analysis loop only checks the stop signal between cycles, never
#: mid-cycle, so this must cover the worst realistic single cycle (network sampling +
#: analyzing every newly-seen process, including hashing any new executables).
THREAD_JOIN_TIMEOUT_SECONDS = 10.0


class SecurityEngine:
    def __init__(
        self,
        event_bus: EventBus,
        system_monitor: SystemMonitorService,
        analysis_interval_seconds: float = DEFAULT_ANALYSIS_INTERVAL_SECONDS,
        audit_logger: AuditLogger | None = None,
    ):
        self.event_bus = event_bus
        self.system_monitor = system_monitor
        self.analysis_interval_seconds = analysis_interval_seconds
        #: Optional (defaults to None, same pattern as LifecycleManager's optional
        #: event_bus) so every existing caller/test that doesn't care about audit
        #: logging is unaffected. When given, records scan start/completion, finding
        #: creation, review/dismissal and analysis errors — never file content,
        #: secrets, or tokens (see security/audit.py's own redaction, and the V1.5
        #: spec's explicit "não registrar conteúdo de arquivos/secrets/senhas/tokens").
        self.audit_logger = audit_logger

        self.scanner = SecurityScanner(event_bus)
        self._analyzer = ProcessAnalyzer()
        self.findings: deque[SecurityFinding] = deque(maxlen=MAX_FINDINGS_HISTORY)
        self._findings_lock = threading.Lock()

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._last_error: str | None = None

        self._latest_snapshot_lock = threading.Lock()
        self._latest_process_snapshot: ProcessSnapshot | None = None
        #: Set whenever fresh process data arrives (_on_process_list_updated) so
        #: _run()'s wait can be interrupted early instead of always sitting through the
        #: full analysis_interval_seconds — matters most right after start(): if
        #: System Monitor's very first collection cycle hasn't completed yet by the
        #: time _run()'s first iteration checks, analysis is skipped for that cycle
        #: (see _run()'s docstring), and without this, real analysis wouldn't begin
        #: until a full stale interval later even though real data may arrive within
        #: milliseconds.
        self._new_data_event = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def state(self) -> ProtectionState:
        """MONITORING (not yet PROTECTED) covers the brief window between start() and
        the first completed analysis cycle, during which ProcessAnalyzer is still
        establishing its baseline (see security/process_analysis.py) and has not yet
        evaluated anything — genuinely different from steady-state protection, not a
        synonym for it."""
        if not self.is_running:
            return ProtectionState.DISABLED
        if self._last_error is not None:
            return ProtectionState.DEGRADED
        if not self._analyzer.baseline_established:
            return ProtectionState.MONITORING
        return ProtectionState.PROTECTED

    # --- lifecycle -------------------------------------------------------------------
    def start(self) -> None:
        with self._lifecycle_lock:
            if self.is_running:
                return
            self._stop_event.clear()
            self._last_error = None
            # SecurityEngine starts at boot (see module docstring), but per PATCH 03,
            # SystemMonitorService only starts when the Dashboard is first opened —
            # without this, "real-time protection" could run its first several cycles
            # against zero process data (Steve just booted, Dashboard never opened
            # yet), establishing ProcessAnalyzer's baseline as empty. Once real process
            # data finally arrived (the moment the Dashboard is opened), EVERY process
            # already running at that point would look "new" and get flagged — a flood
            # of false positives, discovered via real-hardware validation. Starting the
            # monitor here (idempotent, safe even if a Dashboard already started it)
            # ensures real process data is flowing before ProcessAnalyzer ever runs.
            # Light mode at boot: Security needs process lists, not GPU every 1s.
            try:
                self.system_monitor.set_light_mode(True)
                self.system_monitor.metrics_interval = max(
                    float(getattr(self.system_monitor, "metrics_interval", 1.0)), 4.0
                )
                self.system_monitor.process_interval = max(
                    float(getattr(self.system_monitor, "process_interval", 2.0)), 5.0
                )
            except Exception:
                pass
            self.system_monitor.start()
            with self._latest_snapshot_lock:
                # Prime with whatever System Monitor already has (same pattern as
                # DashboardWindow._prime_with_latest_data()) so the first analysis
                # cycle isn't spuriously empty just because it ran before the first
                # PROCESS_LIST_UPDATED event after subscribing.
                self._latest_process_snapshot = self.system_monitor.latest_processes
            self.event_bus.subscribe(PROCESS_LIST_UPDATED, self._on_process_list_updated)
            self.event_bus.subscribe(SECURITY_SCAN_STARTED, self._log_scan_started)
            self.event_bus.subscribe(SECURITY_SCAN_COMPLETED, self._log_scan_completed)
            self.event_bus.subscribe(SECURITY_FINDING_CREATED, self._log_finding_created)
            self.event_bus.subscribe(SECURITY_ENGINE_ERROR, self._log_engine_error)
            self._thread = threading.Thread(target=self._run, name="steve-security-engine", daemon=True)
            self._thread.start()
        self.event_bus.publish(SECURITY_ENGINE_STARTED, {})

    def stop(self) -> None:
        with self._lifecycle_lock:
            if not self.is_running:
                return
            self._stop_event.set()
            # _run()'s wait is on _new_data_event, not _stop_event directly (see its
            # docstring) -- without also setting this, a blocked wait wouldn't notice
            # the stop request until analysis_interval_seconds elapsed on its own,
            # which could exceed THREAD_JOIN_TIMEOUT_SECONDS below.
            self._new_data_event.set()
            self.event_bus.unsubscribe(PROCESS_LIST_UPDATED, self._on_process_list_updated)
            self.event_bus.unsubscribe(SECURITY_SCAN_STARTED, self._log_scan_started)
            self.event_bus.unsubscribe(SECURITY_SCAN_COMPLETED, self._log_scan_completed)
            self.event_bus.unsubscribe(SECURITY_FINDING_CREATED, self._log_finding_created)
            self.event_bus.unsubscribe(SECURITY_ENGINE_ERROR, self._log_engine_error)
            thread = self._thread
            thread.join(timeout=THREAD_JOIN_TIMEOUT_SECONDS)
            if thread.is_alive():
                logger.error(
                    "SecurityEngine.stop(): a thread de análise não respondeu em %.0fs "
                    "e pode continuar rodando por mais um tempo.",
                    THREAD_JOIN_TIMEOUT_SECONDS,
                )
            self._thread = None
        self.event_bus.publish(SECURITY_ENGINE_STOPPED, {})

    def _on_process_list_updated(self, data: dict) -> None:
        """Runs on whichever thread published the event — SystemMonitorService's own
        collection thread, per core/events.py's synchronous-dispatch model. Kept
        deliberately trivial (store a reference, nothing else) so a slow security
        analysis cycle (hashing a newly-seen executable, evaluating rules) can never
        delay System Monitor's own collection cadence — the actual analysis happens on
        THIS engine's own thread in _run(), decoupled from this callback."""
        with self._latest_snapshot_lock:
            self._latest_process_snapshot = data.get("snapshot")
        self._new_data_event.set()

    # --- audit logging (all no-ops if no audit_logger was given) --------------------
    def _log_scan_started(self, data: dict) -> None:
        if self.audit_logger:
            self.audit_logger.log("security_scan_started", {"kind": data.get("kind"), "total": data.get("total")})

    def _log_scan_completed(self, data: dict) -> None:
        if self.audit_logger:
            summary = data.get("summary")
            self.audit_logger.log(
                "security_scan_completed",
                {
                    "kind": data.get("kind"),
                    "files_scanned": getattr(summary, "files_scanned", None),
                    "findings_count": len(getattr(summary, "findings", []) or []),
                },
            )

    def _log_finding_created(self, data: dict) -> None:
        if self.audit_logger:
            finding = data.get("finding")
            if finding is None:
                return
            self.audit_logger.log(
                "security_finding_created",
                {
                    "id": finding.id,
                    "severity": finding.severity.name,
                    "category": finding.category.value,
                    "title": finding.title,
                    "pid": finding.pid,
                    "process_name": finding.process_name,
                },
            )

    def _log_engine_error(self, data: dict) -> None:
        if self.audit_logger:
            self.audit_logger.log("security_engine_error", {"error": data.get("error")})

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                with self._latest_snapshot_lock:
                    snapshot = self._latest_process_snapshot
                if snapshot is not None:
                    # Defense in depth alongside start()'s system_monitor.start() call
                    # above: if System Monitor's data is somehow still unavailable
                    # (e.g. it failed to start, or hasn't published its first cycle
                    # yet), skip analysis entirely rather than establish
                    # ProcessAnalyzer's baseline against an empty process list — doing
                    # so would make every real process look "new" the moment real data
                    # finally arrived (see start()'s docstring for the real bug this
                    # was caught fixing during V1.5's hardware validation).
                    network = sample_external_connections()
                    new_findings = self._analyzer.analyze(snapshot.processes, network)
                    for finding in new_findings:
                        with self._findings_lock:
                            self.findings.append(finding)
                        self.event_bus.publish(SECURITY_FINDING_CREATED, {"finding": finding})
                self._last_error = None
            except Exception as exc:  # noqa: BLE001 - an analysis failure must never kill the loop
                logger.exception("Falha no ciclo de análise do Security Engine.")
                self._last_error = str(exc)
                self.event_bus.publish(SECURITY_ENGINE_ERROR, {"error": str(exc)})

            # Wakes up early if fresh process data arrives mid-wait (see
            # _new_data_event's docstring) instead of always sitting through the full
            # interval — most relevant right after start(), before the first real
            # snapshot exists; harmless in steady state (an extra wake-up just re-runs
            # analyze(), which is cheap when there are no new pids to evaluate).
            self._new_data_event.clear()
            self._new_data_event.wait(timeout=self.analysis_interval_seconds)

    # --- scanning ------------------------------------------------------------------
    def quick_scan(self):
        """Convenience wrapper so a UI caller (ui/desktop/dashboard/security_tab.py)
        doesn't need to reach into engine internals to get a process list — uses
        whatever System Monitor snapshot this engine already has cached, never a new
        process enumeration."""
        with self._latest_snapshot_lock:
            snapshot = self._latest_process_snapshot
        processes = snapshot.processes if snapshot is not None else ()
        return self.scanner.quick_scan(processes)

    # --- findings ----------------------------------------------------------------
    def recent_findings(self, limit: int = 50) -> list[SecurityFinding]:
        with self._findings_lock:
            return list(self.findings)[-limit:]

    def update_finding_status(self, finding_id: str, status: FindingStatus) -> bool:
        """The only mutation a Finding ever undergoes: marking it reviewed/dismissed —
        a user decision recorded for their own future reference, never anything that
        touches the process/file/connection the finding is about."""
        with self._findings_lock:
            for index, finding in enumerate(self.findings):
                if finding.id == finding_id:
                    self.findings[index] = dataclasses.replace(finding, status=status)
                    if self.audit_logger:
                        self.audit_logger.log(
                            "security_finding_status_updated", {"id": finding_id, "status": status.value}
                        )
                    return True
        return False
