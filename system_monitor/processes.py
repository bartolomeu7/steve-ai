"""Real running-process snapshots via psutil (same library tools/applications.py's
ListProcessesTool already uses — this is the shared collection point for the Dashboard's
Processes tab, not a second implementation).

Per-process cpu_percent needs priming (psutil's own documented behavior: the first
reading for a process is always 0.0, meaningful only once psutil has seen that process
at least twice) — ProcessCollector keeps `psutil.Process` handles across calls so a
persistent SystemMonitorService gets accurate values from the second poll onward,
exactly like GpuMonitor's utilization and NetworkMonitor's rates.
"""
from __future__ import annotations

import logging
import time

import psutil

from system_monitor.models import ProcessInfo, ProcessSnapshot

logger = logging.getLogger("steve.system_monitor.processes")

_ATTRS = ("pid", "name", "memory_percent", "memory_info", "status", "exe", "ppid")


class ProcessCollector:
    def __init__(self):
        self._handles: dict[int, psutil.Process] = {}

    def sample(self, limit: int = 1000) -> ProcessSnapshot:
        """V1.5 Security Center finding (via real-hardware validation): the previous
        default of 100, combined with sorting by CPU% descending below, silently
        dropped any process using near-zero CPU once more than 100 processes were
        running — which is routine on a real desktop (this dev machine regularly runs
        150-250). security/engine.py subscribes to this same snapshot for process-
        based security analysis, and a quiet/idle process is exactly the profile a
        deliberately stealthy one would have — the previous cap made such a process
        invisible to security monitoring by construction, not just unlikely to be
        shown. Raised well above any realistic Windows process count instead of
        removed outright, so a pathological runaway-process-count scenario still can't
        make a single snapshot unbounded. ui/desktop/dashboard/processes_view.py
        already applies its own, independent display cap (_MAX_DISPLAYED_ROWS = 60),
        so the Dashboard's Processes tab is completely unaffected by this change.
        """
        seen_pids: set[int] = set()
        infos: list[ProcessInfo] = []

        for proc in psutil.process_iter(_ATTRS):
            try:
                info = proc.info
                pid = info["pid"]
                seen_pids.add(pid)
                handle = self._handles.get(pid)
                if handle is None or handle.create_time() != proc.create_time():
                    handle = proc
                    self._handles[pid] = handle
                    handle.cpu_percent(None)  # prime — first reading is always 0.0
                    cpu_percent = 0.0
                else:
                    cpu_percent = handle.cpu_percent(None)

                memory_info = info.get("memory_info")
                infos.append(
                    ProcessInfo(
                        pid=pid,
                        name=info.get("name") or "",
                        cpu_percent=cpu_percent,
                        memory_percent=info.get("memory_percent"),
                        memory_bytes=memory_info.rss if memory_info else None,
                        status=info.get("status"),
                        executable_path=info.get("exe") or None,
                        parent_pid=info.get("ppid"),
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                # A process that exited mid-iteration, or one this user can't inspect
                # (common for system/elevated processes) — skip it, never crash the
                # whole snapshot over one process. exe()/other privileged fields
                # already come back None from psutil in the access-denied case.
                continue

        stale_pids = set(self._handles) - seen_pids
        for pid in stale_pids:
            del self._handles[pid]

        infos.sort(key=lambda p: p.cpu_percent or 0.0, reverse=True)
        return ProcessSnapshot(timestamp=time.time(), processes=tuple(infos[:limit]))
