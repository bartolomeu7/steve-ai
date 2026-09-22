"""DashboardWindow: the System Dashboard — a second, independent Toplevel window (not a
second application; see ui/desktop/app.py for how SteveApp owns exactly one of these at
a time). Reuses the main window's BackgroundRunner for thread-safe marshaling instead of
starting a second polling loop, and the shared EventBus/SystemMonitorService instead of
a parallel monitor or event system. Monitoring only — nothing reachable from this window
can kill/suspend/delete a process or modify anything on the system.
"""
from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from core.events import EventBus
from core.lifecycle import STEVE_DEGRADED, STEVE_READY
from core.orchestrator import AI_REQUEST_STARTED, AI_RESPONSE_RECEIVED, TOOL_COMPLETED, TOOL_STARTED
from security.engine import SecurityEngine
from security.events import (
    SECURITY_ENGINE_ERROR,
    SECURITY_ENGINE_STARTED,
    SECURITY_ENGINE_STOPPED,
    SECURITY_FINDING_CREATED,
    SECURITY_SCAN_CANCELLED,
    SECURITY_SCAN_COMPLETED,
    SECURITY_SCAN_PROGRESS,
    SECURITY_SCAN_STARTED,
)
from system_monitor.service import (
    PROCESS_LIST_UPDATED,
    SYSTEM_METRICS_UPDATED,
    SYSTEM_MONITOR_ERROR,
    SYSTEM_MONITOR_STARTED,
    SYSTEM_MONITOR_STOPPED,
    SystemMonitorService,
)
from system_monitor.models import ProcessSnapshot, SystemSnapshot
from ui.desktop.async_bridge import BackgroundRunner
from ui.desktop.dashboard.live_activity import LiveActivityTab
from ui.desktop.dashboard.overview import OverviewTab
from ui.desktop.dashboard.performance import PerformanceTab
from ui.desktop.dashboard.processes_view import ProcessesTab
from ui.desktop.dashboard.security_tab import SecurityTab
from ui.desktop.styles import Palette
from voice.service import VOICE_STATE_CHANGED

SYSTEM_DASHBOARD_OPENED = "dashboard.opened"
SYSTEM_DASHBOARD_CLOSED = "dashboard.closed"

#: Every event Live Activity presents — see ui/desktop/dashboard/live_activity.py's
#: _EVENT_PRESENTATION table for how each one is stylized.
_LIVE_ACTIVITY_EVENTS = (
    STEVE_READY,
    STEVE_DEGRADED,
    AI_REQUEST_STARTED,
    AI_RESPONSE_RECEIVED,
    TOOL_STARTED,
    TOOL_COMPLETED,
    VOICE_STATE_CHANGED,
    SYSTEM_MONITOR_STARTED,
    SYSTEM_MONITOR_STOPPED,
    SYSTEM_MONITOR_ERROR,
    SECURITY_ENGINE_STARTED,
    SECURITY_ENGINE_STOPPED,
    SECURITY_ENGINE_ERROR,
    SECURITY_FINDING_CREATED,
    SECURITY_SCAN_STARTED,
    SECURITY_SCAN_COMPLETED,
    SECURITY_SCAN_CANCELLED,
    # Deliberately NOT SECURITY_SCAN_PROGRESS here -- it fires once per file scanned,
    # which would spam the Live Activity feed; SecurityTab shows scan progress instead.
)


class DashboardWindow(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        palette: Palette,
        event_bus: EventBus,
        system_monitor: SystemMonitorService,
        security_engine: SecurityEngine,
        runner: BackgroundRunner,
        on_close: Callable[[], None] | None = None,
    ):
        super().__init__(master)
        self.palette = palette
        self.event_bus = event_bus
        self.system_monitor = system_monitor
        self.security_engine = security_engine
        self._runner = runner
        self._on_close_cb = on_close
        self._subscriptions: list[tuple[str, Callable[[dict], None]]] = []

        self.title("Steve — System Dashboard")
        self.geometry("880x640")
        self.minsize(420, 360)
        self.configure(fg_color=palette.bg)

        self._build_ui()
        self._subscribe()

        try:

            self.system_monitor.set_light_mode(False)

            # Restore snappier polls while Dashboard is open

            self.system_monitor.metrics_interval = min(

                float(self.system_monitor.metrics_interval), 1.0

            )

            self.system_monitor.process_interval = min(

                float(self.system_monitor.process_interval), 2.0

            )

        except Exception:

            pass

        self.system_monitor.start()  # idempotent — safe even if a prior window already started it
        self.event_bus.publish(SYSTEM_DASHBOARD_OPENED, {})
        self._prime_with_latest_data()

        self.protocol("WM_DELETE_WINDOW", self._handle_close)

    # --- layout -----------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.tabview = ctk.CTkTabview(self, fg_color=self.palette.surface_alt)
        self.tabview.pack(fill="both", expand=True, padx=12, pady=12)

        overview_tab = self.tabview.add("Overview")
        performance_tab = self.tabview.add("Performance")
        processes_tab = self.tabview.add("Processes")
        security_tab = self.tabview.add("Security")
        live_activity_tab = self.tabview.add("Steve Core")

        self.overview = OverviewTab(overview_tab, self.palette)
        self.overview.pack(fill="both", expand=True)

        self.performance = PerformanceTab(performance_tab, self.palette)
        self.performance.pack(fill="both", expand=True)

        self.processes = ProcessesTab(processes_tab, self.palette)
        self.processes.pack(fill="both", expand=True)

        self.security = SecurityTab(security_tab, self.palette, self.security_engine, self._runner)
        self.security.pack(fill="both", expand=True)

        self.live_activity = LiveActivityTab(live_activity_tab, self.palette)
        self.live_activity.pack(fill="both", expand=True)

    # --- events -------------------------------------------------------------------
    def _subscribe(self) -> None:
        self._add_subscription(SYSTEM_METRICS_UPDATED, self._on_metrics_updated)
        self._add_subscription(PROCESS_LIST_UPDATED, self._on_processes_updated)
        self._add_subscription(SECURITY_FINDING_CREATED, self._on_security_finding_created)
        self._add_subscription(SECURITY_SCAN_STARTED, self._on_security_scan_started)
        self._add_subscription(SECURITY_SCAN_PROGRESS, self._on_security_scan_progress)
        self._add_subscription(SECURITY_SCAN_COMPLETED, self._on_security_scan_completed)
        self._add_subscription(SECURITY_SCAN_CANCELLED, self._on_security_scan_cancelled)
        self._add_subscription(SECURITY_ENGINE_STARTED, self._on_security_engine_state_event)
        self._add_subscription(SECURITY_ENGINE_STOPPED, self._on_security_engine_state_event)
        self._add_subscription(SECURITY_ENGINE_ERROR, self._on_security_engine_state_event)
        for event_name in _LIVE_ACTIVITY_EVENTS:
            self._add_subscription(event_name, self._make_live_activity_handler(event_name))

    def _add_subscription(self, event_name: str, handler: Callable[[dict], None]) -> None:
        self.event_bus.subscribe(event_name, handler)
        self._subscriptions.append((event_name, handler))

    def _make_live_activity_handler(self, event_name: str) -> Callable[[dict], None]:
        def handler(data: dict) -> None:
            # May run on whichever thread published the event (often a background
            # worker) — never touch a widget here directly, always marshal first.
            self._runner.post(self._apply_live_activity_event, event_name, data)

        return handler

    def _on_metrics_updated(self, data: dict) -> None:
        self._runner.post(self._apply_snapshot, data["snapshot"])

    def _on_processes_updated(self, data: dict) -> None:
        self._runner.post(self._apply_processes, data["snapshot"])

    def _on_security_finding_created(self, data: dict) -> None:
        self._runner.post(self._apply_security_finding, data.get("finding"))

    def _on_security_scan_started(self, data: dict) -> None:
        self._runner.post(self._apply_security_scan_started, data.get("kind", ""))

    def _on_security_scan_progress(self, data: dict) -> None:
        self._runner.post(self._apply_security_scan_progress, data.get("scanned", 0), data.get("total"))

    def _on_security_scan_completed(self, data: dict) -> None:
        self._runner.post(self._apply_security_scan_finished, "Scan concluído.")

    def _on_security_scan_cancelled(self, data: dict) -> None:
        self._runner.post(self._apply_security_scan_finished, "Scan cancelado.")

    def _on_security_engine_state_event(self, data: dict) -> None:
        self._runner.post(self._apply_security_state)

    def _still_alive(self) -> bool:
        """A subscription can still be queued (BackgroundRunner.post already called from
        the publishing thread) at the exact moment _handle_close() unsubscribes and
        destroys this window — EventBus.publish() iterates a snapshot of subscribers
        taken before unsubscribe() can remove one mid-iteration. BackgroundRunner's own
        drain loop already catches and logs any exception a stale callback raises (never
        crashes), but checking here first turns "an ERROR-level TclError traceback in the
        log for an entirely expected race" into a silent, cheap no-op — same
        winfo_exists() guard already used by _open_settings()/_open_dashboard()."""
        return self.winfo_exists() == 1

    def _apply_processes(self, snapshot: ProcessSnapshot) -> None:
        if not self._still_alive():
            return
        self.processes.update_processes(snapshot)

    def _apply_security_finding(self, finding) -> None:
        if not self._still_alive() or finding is None:
            return
        self.security.add_finding(finding)

    def _apply_security_scan_started(self, kind: str) -> None:
        if not self._still_alive():
            return
        self.security.apply_scan_started(kind)

    def _apply_security_scan_progress(self, scanned: int, total: int | None) -> None:
        if not self._still_alive():
            return
        self.security.apply_scan_progress(scanned, total)

    def _apply_security_scan_finished(self, text: str) -> None:
        if not self._still_alive():
            return
        self.security.apply_scan_finished(text)

    def _apply_security_state(self) -> None:
        if not self._still_alive():
            return
        self.security.apply_state(self.security_engine.state)

    def _apply_live_activity_event(self, event_name: str, data: dict) -> None:
        if not self._still_alive():
            return
        self.live_activity.handle_event(event_name, data)

    def _apply_snapshot(self, snapshot: SystemSnapshot) -> None:
        if not self._still_alive():
            return
        self.overview.update_snapshot(snapshot)
        self.performance.update_history(
            list(self.system_monitor.cpu_history),
            list(self.system_monitor.gpu_history),
            list(self.system_monitor.ram_history),
            list(self.system_monitor.network_download_history),
        )

    def _prime_with_latest_data(self) -> None:
        """If the monitor was already running (e.g. this is a reopen), show what it
        already has immediately instead of a blank tab until the next ~1s tick."""
        if self.system_monitor.latest_snapshot is not None:
            self._apply_snapshot(self.system_monitor.latest_snapshot)
        latest_processes: ProcessSnapshot | None = self.system_monitor.latest_processes
        if latest_processes is not None:
            self.processes.update_processes(latest_processes)
        self.security.apply_state(self.security_engine.state)
        self.security.apply_findings(self.security_engine.recent_findings())

    # --- shutdown ------------------------------------------------------------------
    def _handle_close(self) -> None:
        for event_name, handler in self._subscriptions:
            self.event_bus.unsubscribe(event_name, handler)
        self._subscriptions.clear()
        self.live_activity.cancel_pending_callbacks()
        self.event_bus.publish(SYSTEM_DASHBOARD_CLOSED, {})
        if self._on_close_cb:
            self._on_close_cb()
        self.destroy()
        # Deliberately does NOT call system_monitor.stop(): the monitor's lifecycle is
        # independent of this window's (see system_monitor/service.py's module
        # docstring and docs/ARCHITECTURE.md) — it keeps running until Steve itself
        # shuts down, so reopening the Dashboard doesn't need GPU/process-CPU counters
        # to re-prime from zero, and it's never torn down "just because the window
        # closed" if something else were ever listening to the same events.
