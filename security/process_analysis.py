"""Turns ProcessSnapshot data the SystemMonitorService already collects (reused, never
duplicated — see security/engine.py, which subscribes to system_monitor's
PROCESS_LIST_UPDATED instead of running a second psutil.process_iter() loop) into
SecurityFindings, via security/rules.py's signals and security/risk.py's scoring.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from security import rules
from security.file_analysis import analyze_file
from security.models import FileMetadata, FindingCategory, NetworkEvidence, SecurityFinding, now
from security.risk import assess
from system_monitor.models import ProcessInfo

logger = logging.getLogger("steve.security.process_analysis")


class ProcessAnalyzer:
    """Stateful, one instance per SecurityEngine lifetime (same pattern as
    system_monitor/gpu.py::GpuMonitor's per-service caches). "New process" only ever
    means "appeared after this analyzer's first cycle" — the FIRST cycle silently
    establishes a baseline of whatever's already running instead of flagging every
    pre-existing process as new, which would be a flood of meaningless findings for
    completely normal, already-running system processes (explorer.exe, svchost.exe,
    ...) — matching the spec's explicit "evitar falsos positivos excessivos".

    File analysis (hashing) only ever runs once per distinct executable path, cached
    for this analyzer's lifetime — an already-known process is never re-hashed on every
    poll cycle, which would be wasteful (see docs/ARCHITECTURE.md's Security Center
    performance notes).
    """

    def __init__(self):
        self._known_pids: set[int] = set()
        self._baseline_established = False
        self._file_cache: dict[str, FileMetadata] = {}

    @property
    def baseline_established(self) -> bool:
        return self._baseline_established

    def analyze(
        self,
        processes: Sequence[ProcessInfo],
        network: Sequence[NetworkEvidence] = (),
    ) -> list[SecurityFinding]:
        current_pids = {p.pid for p in processes}

        if not self._baseline_established:
            self._known_pids = current_pids
            self._baseline_established = True
            return []

        new_pids = current_pids - self._known_pids
        self._known_pids = current_pids
        if not new_pids:
            return []

        network_by_pid: dict[int, NetworkEvidence] = {}
        for evidence in network:
            if evidence.pid is not None and evidence.pid not in network_by_pid:
                network_by_pid[evidence.pid] = evidence

        findings: list[SecurityFinding] = []
        for process in processes:
            if process.pid not in new_pids:
                continue

            file_metadata = self._metadata_for(process.executable_path)
            net_evidence = network_by_pid.get(process.pid)

            signals = rules.evaluate_process(
                process, is_new=True, file_metadata=file_metadata, network=net_evidence
            )
            if not signals:
                continue  # a new process with zero signals is not a finding at all

            assessment = assess(signals)
            findings.append(
                SecurityFinding(
                    id=SecurityFinding.new_id(),
                    timestamp=now(),
                    severity=assessment.severity,
                    category=FindingCategory.PROCESS,
                    title=f"Atividade observada: {process.name}",
                    description=(
                        f"O processo '{process.name}' (PID {process.pid}) apareceu recentemente e "
                        "apresentou sinais que podem indicar atividade incomum."
                    ),
                    evidence=assessment.signals,
                    confidence=assessment.confidence,
                    process_name=process.name,
                    pid=process.pid,
                    executable_path=process.executable_path,
                )
            )
        return findings

    def _metadata_for(self, executable_path: str | None) -> FileMetadata | None:
        if not executable_path:
            return None
        cached = self._file_cache.get(executable_path)
        if cached is not None:
            return cached
        metadata = analyze_file(executable_path)
        self._file_cache[executable_path] = metadata
        return metadata
