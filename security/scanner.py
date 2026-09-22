"""SecurityScanner: Quick/File/Folder scans. Deliberately does NOT spawn its own thread
or polling loop — exactly like every blocking operation elsewhere in Steve
(Orchestrator's AI calls, VoiceService's STT), the caller runs `quick_scan`/
`scan_file_path`/`scan_folder_path` through the existing
`ui/desktop/async_bridge.py::BackgroundRunner.run()`, which already handles "block on a
worker thread, marshal the result back to the GUI thread" — no new async machinery is
introduced here. Progress/completion are also published on the existing EventBus so any
consumer (not just whichever call started the scan) can observe it, same as
SystemMonitorService.

NEVER executes, opens, or deletes anything it finds — see security/file_analysis.py.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from core.events import EventBus
from security.events import (
    SECURITY_FINDING_CREATED,
    SECURITY_SCAN_CANCELLED,
    SECURITY_SCAN_COMPLETED,
    SECURITY_SCAN_PROGRESS,
    SECURITY_SCAN_STARTED,
)
from security.file_analysis import scan_file
from security.models import FindingCategory, ScanKind, ScanResultStatus, ScanSummary, SecurityFinding, Severity, now
from system_monitor.models import ProcessInfo

logger = logging.getLogger("steve.security.scanner")

#: Folder scans are bounded in this version — an unbounded recursive walk of an
#: arbitrarily large folder (or an entire drive) could run for a very long time with no
#: way to size the work in advance; the user can always scan a more specific
#: subfolder. Cancellation (see SecurityScanner.cancel) works throughout regardless.
DEFAULT_MAX_FILES_PER_FOLDER_SCAN = 500


def _finding_from_suspicious_file(result) -> SecurityFinding:
    return SecurityFinding(
        id=SecurityFinding.new_id(),
        timestamp=now(),
        severity=Severity.MEDIUM if len(result.reasons) <= 1 else Severity.HIGH,
        category=FindingCategory.FILE,
        title=f"Arquivo suspeito: {Path(result.path).name}",
        description=f"O arquivo '{result.path}' apresentou sinais que podem indicar risco.",
        evidence=result.reasons,
        confidence=min(0.95, 0.4 + 0.15 * len(result.reasons)),
        executable_path=result.path,
    )


class SecurityScanner:
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self._cancel_event = threading.Event()
        self._lifecycle_lock = threading.Lock()  # same pattern/justification as
        # system_monitor/service.py's _lifecycle_lock: guards "at most one scan runs at
        # a time" against a genuinely concurrent second scan request, not because any
        # caller in this codebase today issues concurrent scans (the Dashboard's scan
        # buttons are disabled while a scan runs), but so that invariant is enforced
        # here rather than assumed at every call site.
        self._scanning = False

    @property
    def is_scanning(self) -> bool:
        return self._scanning

    def cancel(self) -> None:
        self._cancel_event.set()

    # --- scan entry points --------------------------------------------------------
    def quick_scan(self, running_processes: Sequence[ProcessInfo]) -> ScanSummary:
        """A light, fast check of currently-running processes' executables — NOT a
        full-disk sweep. Reuses whatever process list the caller already has (see
        security/engine.py, which reuses SystemMonitorService's ProcessSnapshot) rather
        than enumerating processes a second time."""
        paths = sorted({p.executable_path for p in running_processes if p.executable_path})
        return self._run_scan(ScanKind.QUICK, paths)

    def scan_file_path(self, path: str) -> ScanSummary:
        return self._run_scan(ScanKind.FILE, [path])

    def scan_folder_path(self, folder: str, *, max_files: int = DEFAULT_MAX_FILES_PER_FOLDER_SCAN) -> ScanSummary:
        if not Path(folder).is_dir():
            # Path.rglob() on a nonexistent path silently yields nothing rather than
            # raising — without this explicit check, a typo'd/nonexistent folder would
            # look identical to "scanned, found nothing", which would be misleading.
            summary = ScanSummary(kind=ScanKind.FOLDER, started_at=time.time())
            summary.finished_at = time.time()
            summary.error = "Pasta não encontrada."
            return summary

        candidates: list[str] = []
        try:
            for entry in Path(folder).rglob("*"):
                if self._cancel_event.is_set():
                    break
                try:
                    if entry.is_file():
                        candidates.append(str(entry))
                except OSError:
                    continue
                if len(candidates) >= max_files:
                    break
        except OSError as exc:
            logger.warning("Falha ao enumerar pasta %s.", folder, exc_info=True)
            summary = ScanSummary(kind=ScanKind.FOLDER, started_at=time.time())
            summary.finished_at = time.time()
            summary.error = str(exc)
            return summary
        return self._run_scan(ScanKind.FOLDER, candidates)

    # --- shared scan loop ----------------------------------------------------------
    def _run_scan(self, kind: ScanKind, paths: list[str]) -> ScanSummary:
        with self._lifecycle_lock:
            if self._scanning:
                summary = ScanSummary(kind=kind, started_at=time.time())
                summary.finished_at = time.time()
                summary.error = "Um scan já está em andamento."
                return summary
            self._scanning = True
            self._cancel_event.clear()

        summary = ScanSummary(kind=kind, started_at=time.time())
        total = len(paths)
        self.event_bus.publish(SECURITY_SCAN_STARTED, {"kind": kind.value, "total": total})

        try:
            for path in paths:
                if self._cancel_event.is_set():
                    summary.cancelled = True
                    break

                result = scan_file(path)
                summary.files_scanned += 1

                if result.status == ScanResultStatus.SUSPICIOUS:
                    finding = _finding_from_suspicious_file(result)
                    summary.findings.append(finding)
                    self.event_bus.publish(SECURITY_FINDING_CREATED, {"finding": finding})

                self.event_bus.publish(
                    SECURITY_SCAN_PROGRESS,
                    {"kind": kind.value, "scanned": summary.files_scanned, "total": total, "current_path": path},
                )
        finally:
            summary.finished_at = time.time()
            with self._lifecycle_lock:
                self._scanning = False

        if summary.cancelled:
            self.event_bus.publish(SECURITY_SCAN_CANCELLED, {"kind": kind.value, "scanned": summary.files_scanned})
        else:
            self.event_bus.publish(SECURITY_SCAN_COMPLETED, {"kind": kind.value, "summary": summary})
        return summary
