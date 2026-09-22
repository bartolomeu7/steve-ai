"""SystemMonitorService: the one and only place in Steve that repeatedly calls psutil,
GPU performance counters, or iterates processes. Runs its own background thread (a
long-lived service, not a one-shot BackgroundRunner task) and only ever hands the rest
of the app finished, immutable snapshots — published on the existing EventBus
(core/events.py), never a second event system. No widget/tool should call psutil
directly for anything this service already collects.

Events published here follow the same "module owns its own event name constants,
routed through the shared EventBus" pattern core/lifecycle.py established in V1.3.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

from core.events import EventBus
from system_monitor.gpu import GpuMonitor
from system_monitor.metrics import CpuUsageTracker, collect_cpu_metrics, collect_disk_metrics, collect_memory_metrics
from system_monitor.models import ProcessSnapshot, SystemSnapshot
from system_monitor.network import DiskIoMonitor, NetworkMonitor
from system_monitor.processes import ProcessCollector

logger = logging.getLogger("steve.system_monitor.service")

SYSTEM_MONITOR_STARTED = "system_monitor.started"
SYSTEM_MONITOR_STOPPED = "system_monitor.stopped"
SYSTEM_METRICS_UPDATED = "system_monitor.metrics_updated"
PROCESS_LIST_UPDATED = "system_monitor.process_list_updated"
NETWORK_METRICS_UPDATED = "system_monitor.network_metrics_updated"
SYSTEM_MONITOR_ERROR = "system_monitor.error"

DEFAULT_METRICS_INTERVAL_SECONDS = 1.0
DEFAULT_PROCESS_INTERVAL_SECONDS = 2.0
#: How long stop() waits for the collection thread to notice _stop_event and exit,
#: before giving up and logging it as stuck. The loop only checks the stop signal
#: between collection calls, never mid-call, so this has to cover the worst realistic
#: single collection cycle — real process enumeration (psutil.process_iter on Windows)
#: was measured to occasionally take up to ~1.2s under load from external factors
#: (antivirus/OS scheduling, not this code — see the V1.4 report), and was observed
#: once during real validation to exceed the previous 5s timeout entirely. Doubled to
#: 10s as a pragmatic margin; this does not fix the underlying OS-level variance (out
#: of this project's control), it only reduces how often stop() gives up prematurely.
THREAD_JOIN_TIMEOUT_SECONDS = 10.0
#: ~2 minutes of history at the default 1s metrics interval ("últimos 60-120 segundos");
#: a deque so old samples fall off automatically instead of growing forever.
HISTORY_LENGTH = 120


class SystemMonitorService:
    def __init__(
        self,
        event_bus: EventBus,
        metrics_interval: float = DEFAULT_METRICS_INTERVAL_SECONDS,
        process_interval: float = DEFAULT_PROCESS_INTERVAL_SECONDS,
        disk_path: str = "C:\\",
    ):
        self.event_bus = event_bus
        self.metrics_interval = metrics_interval
        self.process_interval = process_interval
        self.disk_path = disk_path
        self.light_mode = False  # True: skip GPU, slower polls (Security boot)

        self._cpu_tracker = CpuUsageTracker()
        self._gpu_monitor = GpuMonitor()
        self._network_monitor = NetworkMonitor()
        self._disk_io_monitor = DiskIoMonitor()
        self._process_collector = ProcessCollector()

        self.latest_snapshot: SystemSnapshot | None = None
        self.latest_processes: ProcessSnapshot | None = None
        #: `None` for the first sample after start() — see CpuUsageTracker.
        self.cpu_history: deque[float | None] = deque(maxlen=HISTORY_LENGTH)
        self.gpu_history: deque[float | None] = deque(maxlen=HISTORY_LENGTH)
        self.ram_history: deque[float] = deque(maxlen=HISTORY_LENGTH)
        self.network_download_history: deque[float | None] = deque(maxlen=HISTORY_LENGTH)
        self.network_upload_history: deque[float | None] = deque(maxlen=HISTORY_LENGTH)

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        #: Guards the check-then-act sequence in start()/stop() — separate from _lock
        #: (which only guards snapshot/history reads/writes) because it protects a
        #: different invariant: "at most one collection thread exists at a time".
        #: Every caller in this codebase today (DashboardWindow.__init__, SteveApp.
        #: _shutdown) only ever calls start()/stop() from the single Tk main thread, so
        #: this lock is never contended in normal operation — it exists for the
        #: documented, tested case of genuinely concurrent start()/stop() calls (e.g. a
        #: future non-GUI consumer), where without it two threads could both pass the
        #: `is_running` check before either assigns self._thread, leaking one of them.
        self._lifecycle_lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_light_mode(self, enabled: bool) -> None:
        """Toggle lightweight collection (no GPU; longer intervals applied by caller)."""
        self.light_mode = bool(enabled)


    def start(self) -> None:
        """A no-op if already running — the one guard against duplicate collectors if
        e.g. the Dashboard window were somehow opened twice (see
        ui/desktop/dashboard/window.py, which also guards this at the window level)."""
        with self._lifecycle_lock:
            if self.is_running:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, name="steve-system-monitor", daemon=True)
            self._thread.start()
        self.event_bus.publish(
            SYSTEM_MONITOR_STARTED, {"metrics_interval": self.metrics_interval, "process_interval": self.process_interval}
        )

    def stop(self) -> None:
        with self._lifecycle_lock:
            if not self.is_running:
                return
            self._stop_event.set()
            thread = self._thread
            thread.join(timeout=THREAD_JOIN_TIMEOUT_SECONDS)
            if thread.is_alive():
                # join() timed out — the collection thread is still genuinely running
                # (e.g. stuck in an unusually slow OS call), not just slow to notice
                # _stop_event. Log this loudly instead of silently proceeding as if
                # stopped: self._thread is still cleared below so a fresh start() isn't
                # blocked, but the old thread itself is NOT tracked anymore and may
                # still be alive for a while — this is the one case where a
                # "steve-system-monitor"-named thread can outlive stop() returning.
                logger.error(
                    "SystemMonitorService.stop(): a thread de coleta não respondeu em %.0fs "
                    "e pode continuar rodando por mais um tempo. Isso não deveria acontecer "
                    "em uso normal — investigar se alguma coleta está travando.",
                    THREAD_JOIN_TIMEOUT_SECONDS,
                )
            self._thread = None
            self._gpu_monitor.close()
        self.event_bus.publish(SYSTEM_MONITOR_STOPPED, {})

    def _run(self) -> None:
        next_process_poll = 0.0
        while not self._stop_event.is_set():
            cycle_start = time.monotonic()

            try:
                self._collect_metrics()
            except Exception as exc:  # noqa: BLE001 - a collection failure must never kill the loop
                logger.exception("Falha ao coletar métricas do sistema.")
                self.event_bus.publish(SYSTEM_MONITOR_ERROR, {"error": str(exc), "stage": "metrics"})

            if cycle_start >= next_process_poll:
                try:
                    self._collect_processes()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Falha ao coletar processos.")
                    self.event_bus.publish(SYSTEM_MONITOR_ERROR, {"error": str(exc), "stage": "processes"})
                next_process_poll = cycle_start + self.process_interval

            elapsed = time.monotonic() - cycle_start
            self._stop_event.wait(max(0.0, self.metrics_interval - elapsed))

    def _collect_metrics(self) -> None:
        cpu = collect_cpu_metrics(self._cpu_tracker)
        if self.light_mode:
            from system_monitor.models import GpuMetrics
            gpu = GpuMetrics(available=False, unavailable_reason="light_mode")
        else:
            gpu = self._gpu_monitor.sample()
        memory = collect_memory_metrics()
        read_rate, write_rate = self._disk_io_monitor.sample()
        disk = collect_disk_metrics(self.disk_path, read_bytes_per_sec=read_rate, write_bytes_per_sec=write_rate)
        network = self._network_monitor.sample()

        snapshot = SystemSnapshot(timestamp=time.time(), cpu=cpu, gpu=gpu, memory=memory, disk=disk, network=network)
        with self._lock:
            self.latest_snapshot = snapshot
            self.cpu_history.append(cpu.percent)
            self.gpu_history.append(gpu.utilization_percent)
            self.ram_history.append(memory.percent)
            self.network_download_history.append(network.download_bytes_per_sec)
            self.network_upload_history.append(network.upload_bytes_per_sec)

        self.event_bus.publish(SYSTEM_METRICS_UPDATED, {"snapshot": snapshot})
        self.event_bus.publish(NETWORK_METRICS_UPDATED, {"network": network})

    def _collect_processes(self) -> None:
        snapshot = self._process_collector.sample()
        with self._lock:
            self.latest_processes = snapshot
        self.event_bus.publish(PROCESS_LIST_UPDATED, {"snapshot": snapshot})
