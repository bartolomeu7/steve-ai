"""GUI + integration tests for the System Dashboard. Real Toplevel windows (this
machine has a display, same approach as tests/test_desktop_window.py) driven
programmatically — never real mouse/keyboard input, and the SystemMonitorService's
background thread is deliberately never started in most of these: events are published
directly on the EventBus with fake data, so these tests are deterministic and don't
depend on real hardware or thread timing. A couple of true integration tests do start
the real service briefly to prove the whole pipe (collector -> EventBus -> widgets)
actually connects end to end.
"""
from __future__ import annotations

import time

import pytest

from core.events import EventBus
from core.lifecycle import STEVE_READY
from core.orchestrator import AI_REQUEST_STARTED, AI_RESPONSE_RECEIVED, TOOL_COMPLETED, TOOL_STARTED
from security.engine import SecurityEngine
from system_monitor.models import (
    CpuMetrics,
    DiskMetrics,
    GpuMetrics,
    MemoryMetrics,
    NetworkMetrics,
    ProcessInfo,
    ProcessSnapshot,
    SystemSnapshot,
)
from system_monitor.service import PROCESS_LIST_UPDATED, SYSTEM_METRICS_UPDATED, SystemMonitorService
from ui.desktop.async_bridge import BackgroundRunner
from ui.desktop.dashboard.window import SYSTEM_DASHBOARD_CLOSED, SYSTEM_DASHBOARD_OPENED, DashboardWindow
from ui.desktop.styles import LIGHT
from ui.desktop.window import MainWindow


def _status() -> dict:
    return {"model": "llama3.2", "ollama_online": True, "mic_available": False, "voice_available": False, "tools_count": 12}


def _fake_snapshot(cpu_percent: float = 12.3) -> SystemSnapshot:
    return SystemSnapshot(
        timestamp=time.time(),
        cpu=CpuMetrics(percent=cpu_percent, core_count_logical=8, core_count_physical=4),
        gpu=GpuMetrics(available=False, unavailable_reason="Nenhum adaptador de vídeo encontrado."),
        memory=MemoryMetrics(percent=40.0, used_bytes=1_000_000, available_bytes=1_000_000, total_bytes=2_000_000),
        disk=DiskMetrics(path="C:\\", percent=55.0, used_bytes=1, free_bytes=1, total_bytes=2),
        network=NetworkMetrics(upload_bytes_per_sec=100.0, download_bytes_per_sec=200.0, bytes_sent_total=1000, bytes_recv_total=2000),
    )


@pytest.fixture
def root():
    window = MainWindow(app_status=_status(), voice_available=False, orb_enabled=False)
    window.withdraw()
    yield window
    try:
        window.destroy()
    except Exception:
        pass


@pytest.fixture
def runner(root):
    r = BackgroundRunner(root)
    yield r
    r.close()


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def monitor(event_bus):
    """A deliberately SLOW interval: DashboardWindow.__init__ starts this for real (it
    must, to test the idempotent-start/monitor-outlives-window-close behavior), but most
    tests inject synthetic events by publishing directly on the EventBus and must not
    race against the service's own real background collection. Only
    test_integration_real_monitor_thread_updates_dashboard needs (and builds) a fast one."""
    service = SystemMonitorService(event_bus=event_bus, metrics_interval=30.0, process_interval=30.0)
    yield service
    service.stop()


@pytest.fixture
def security_engine(event_bus, monitor):
    """Never started by default — DashboardWindow only reads .state/.recent_findings()
    at construction time, neither of which requires the engine to be running (a never-
    started engine simply reports ProtectionState.DISABLED and an empty findings list,
    both valid states these tests don't otherwise care about)."""
    engine = SecurityEngine(event_bus=event_bus, system_monitor=monitor)
    yield engine
    engine.stop()


def _pump(root, iterations: int = 10, delay: float = 0.05) -> None:
    """Drains BackgroundRunner's queue by pumping the real Tk event loop — the same
    mechanism a running app uses via widget.after(), just driven manually here instead
    of waiting on a real timer."""
    for _ in range(iterations):
        root.update()
        time.sleep(delay)


def _open_dashboard(root, event_bus, monitor, security_engine, runner) -> DashboardWindow:
    window = DashboardWindow(root, LIGHT, event_bus, monitor, security_engine, runner)
    window.withdraw()
    return window


# --- open / close / reopen -------------------------------------------------------------


def test_open_dashboard_creates_a_window(root, event_bus, monitor, security_engine, runner):
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)
    assert dashboard.winfo_exists() == 1
    dashboard._handle_close()


def test_open_dashboard_publishes_opened_event(root, event_bus, monitor, security_engine, runner):
    seen = []
    event_bus.subscribe(SYSTEM_DASHBOARD_OPENED, seen.append)
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)
    assert len(seen) == 1
    dashboard._handle_close()


def test_close_dashboard_publishes_closed_event(root, event_bus, monitor, security_engine, runner):
    seen = []
    event_bus.subscribe(SYSTEM_DASHBOARD_CLOSED, seen.append)
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)

    dashboard._handle_close()

    assert len(seen) == 1


def test_close_dashboard_does_not_destroy_main_window(root, event_bus, monitor, security_engine, runner):
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)
    dashboard._handle_close()
    assert root.winfo_exists() == 1


def test_close_dashboard_does_not_stop_the_system_monitor(root, event_bus, monitor, security_engine, runner):
    """Monitor lifecycle is independent of the window's — see window.py's docstring."""
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)
    assert monitor.is_running is True

    dashboard._handle_close()

    assert monitor.is_running is True


def test_reopening_dashboard_after_close_creates_a_fresh_window(root, event_bus, monitor, security_engine, runner):
    first = _open_dashboard(root, event_bus, monitor, security_engine, runner)
    first._handle_close()

    second = _open_dashboard(root, event_bus, monitor, security_engine, runner)

    assert second is not first
    assert second.winfo_exists() == 1
    second._handle_close()


def test_opening_dashboard_starts_the_monitor_idempotently(root, event_bus, monitor, security_engine, runner):
    assert monitor.is_running is False
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)
    assert monitor.is_running is True
    dashboard._handle_close()


def test_no_exceptions_after_destroy(root, event_bus, monitor, security_engine, runner):
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)
    dashboard._handle_close()
    # Publishing after close must not raise even though the window (and its widget
    # callbacks) no longer exist — subscriptions were removed in _handle_close().
    event_bus.publish(SYSTEM_METRICS_UPDATED, {"snapshot": _fake_snapshot()})
    event_bus.publish(PROCESS_LIST_UPDATED, {"snapshot": ProcessSnapshot(timestamp=time.time(), processes=())})


# --- responsiveness ----------------------------------------------------------------------


def test_resizing_small_uses_compact_layout(root, event_bus, monitor, security_engine, runner):
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)
    dashboard.overview.event_generate = None  # not used; direct call below is deterministic
    dashboard.overview._on_resize(type("E", (), {"width": 300})())
    assert dashboard.overview._current_columns == 1
    dashboard._handle_close()


def test_resizing_medium_uses_normal_layout(root, event_bus, monitor, security_engine, runner):
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)
    dashboard.overview._on_resize(type("E", (), {"width": 600})())
    assert dashboard.overview._current_columns == 2
    dashboard._handle_close()


def test_resizing_large_uses_expanded_layout(root, event_bus, monitor, security_engine, runner):
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)
    dashboard.overview._on_resize(type("E", (), {"width": 900})())
    assert dashboard.overview._current_columns == 3
    dashboard._handle_close()


def test_resize_does_not_recreate_card_widgets(root, event_bus, monitor, security_engine, runner):
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)
    cpu_card_before = dashboard.overview._frames["cpu"]
    dashboard.overview._on_resize(type("E", (), {"width": 900})())
    assert dashboard.overview._frames["cpu"] is cpu_card_before
    dashboard._handle_close()


# --- metrics / process updates (via EventBus, marshaled through the real runner) --------


def _open_dashboard_and_settle(root, event_bus, monitor, security_engine, runner) -> DashboardWindow:
    """Opening the Dashboard calls SystemMonitorService.start(), which — by design, so
    the Dashboard shows real data immediately instead of waiting a full interval —
    always does one real collection cycle right away, regardless of metrics_interval.
    Tests that want to assert on a hand-published *synthetic* snapshot need that one
    real cycle fully drained first, or it's a race which one the widget ends up
    showing. `monitor.stop()` blocks until the in-flight cycle (if any) finishes, so
    stopping then pumping deterministically drains exactly that, and only that."""
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)
    monitor.stop()
    _pump(root, iterations=5)
    return dashboard


def test_metrics_update_reaches_overview_tab(root, event_bus, monitor, security_engine, runner):
    dashboard = _open_dashboard_and_settle(root, event_bus, monitor, security_engine, runner)

    event_bus.publish(SYSTEM_METRICS_UPDATED, {"snapshot": _fake_snapshot(cpu_percent=77.7)})
    _pump(root)

    assert "77.7" in dashboard.overview._bodies["cpu"].cget("text")
    dashboard._handle_close()


def test_process_update_reaches_processes_tab(root, event_bus, monitor, security_engine, runner):
    dashboard = _open_dashboard_and_settle(root, event_bus, monitor, security_engine, runner)
    snapshot = ProcessSnapshot(
        timestamp=time.time(),
        processes=(ProcessInfo(pid=123, name="steve_test.exe", cpu_percent=5.0, memory_percent=1.0, memory_bytes=1024, status="running"),),
    )

    event_bus.publish(PROCESS_LIST_UPDATED, {"snapshot": snapshot})
    _pump(root)

    rows = dashboard.processes.tree.get_children()
    assert len(rows) == 1
    assert dashboard.processes.tree.item(rows[0], "values")[0] == "steve_test.exe"
    dashboard._handle_close()


def test_processes_tab_sorts_on_heading_click(root, event_bus, monitor, security_engine, runner):
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)
    snapshot = ProcessSnapshot(
        timestamp=time.time(),
        processes=(
            ProcessInfo(pid=1, name="b_proc", cpu_percent=1.0, memory_percent=1.0, memory_bytes=1, status="running"),
            ProcessInfo(pid=2, name="a_proc", cpu_percent=9.0, memory_percent=1.0, memory_bytes=1, status="running"),
        ),
    )
    dashboard.processes.update_processes(snapshot)

    dashboard.processes._on_heading_click("name")  # first click on a fresh column: ascending

    rows = dashboard.processes.tree.get_children()
    names = [dashboard.processes.tree.item(r, "values")[0] for r in rows]
    assert names == ["a_proc", "b_proc"]
    dashboard._handle_close()


# --- Live Activity: reacts to real events, sanitizes, returns to idle -------------------


def test_live_activity_starts_idle(root, event_bus, monitor, security_engine, runner):
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)
    assert dashboard.live_activity.state_label.cget("text") == "IDLE"
    dashboard._handle_close()


def test_live_activity_reacts_to_tool_started_event(root, event_bus, monitor, security_engine, runner):
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)

    event_bus.publish(TOOL_STARTED, {"tool": "check_cpu"})
    _pump(root)

    assert dashboard.live_activity.state_label.cget("text") == "EXECUTING"
    feed_text = dashboard.live_activity.feed.get("1.0", "end")
    assert "CORE MODULE" in feed_text
    dashboard._handle_close()


def test_live_activity_returns_to_idle_after_completion_event(root, event_bus, monitor, security_engine, runner):
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)
    event_bus.publish(TOOL_STARTED, {"tool": "check_cpu"})
    _pump(root, iterations=2)

    event_bus.publish(TOOL_COMPLETED, {"tool": "check_cpu", "success": True})
    _pump(root, iterations=2)
    dashboard.live_activity._idle_after_id and dashboard.live_activity.after_cancel(dashboard.live_activity._idle_after_id)
    dashboard.live_activity._go_idle()

    assert dashboard.live_activity.state_label.cget("text") == "IDLE"
    dashboard._handle_close()


def test_live_activity_ignores_unmapped_events(root, event_bus, monitor, security_engine, runner):
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)
    _pump(root, iterations=2)  # drain the SYSTEM_MONITOR_STARTED callback queued by opening the dashboard
    before = dashboard.live_activity.feed.get("1.0", "end")

    event_bus.publish("some.unmapped.event", {"secret": "should never appear"})
    _pump(root, iterations=2)

    after = dashboard.live_activity.feed.get("1.0", "end")
    assert after == before


def test_live_activity_sanitizes_windows_paths():
    from ui.desktop.dashboard.live_activity import _sanitize

    text = r"> FILE C:\Users\Junior\Documents\secret.txt"
    assert "Documents" not in _sanitize(text)
    assert "[caminho ocultado]" in _sanitize(text)


def test_live_activity_never_shows_raw_tool_payload(root, event_bus, monitor, security_engine, runner):
    """TOOL_COMPLETED events never carry tool result data (see core/orchestrator.py) —
    even if a future event somehow included one, only the fixed stylized lines are
    ever inserted into the feed, never arbitrary payload values."""
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)

    event_bus.publish(TOOL_COMPLETED, {"tool": "recall_memories", "success": True, "data": {"memories": ["segredo do usuário"]}})
    _pump(root)

    feed_text = dashboard.live_activity.feed.get("1.0", "end")
    assert "segredo do usuário" not in feed_text
    dashboard._handle_close()


# --- integration: System Monitor -> EventBus -> Dashboard (real thread, briefly) --------


def test_integration_real_monitor_thread_updates_dashboard(root, event_bus, runner):
    """Unlike every other test here, this one deliberately wants the real background
    collector running against real hardware (a fast interval, not the `monitor` fixture's
    deliberately slow one) to prove the whole pipe — collector thread -> EventBus ->
    BackgroundRunner -> widget — actually connects end to end, not just that the widget
    reacts correctly to a hand-published event."""
    fast_monitor = SystemMonitorService(event_bus=event_bus, metrics_interval=0.2, process_interval=0.5)
    local_security_engine = SecurityEngine(event_bus=event_bus, system_monitor=fast_monitor)
    try:
        dashboard = _open_dashboard(root, event_bus, fast_monitor, local_security_engine, runner)

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            _pump(root, iterations=1, delay=0.1)
            if dashboard.overview._bodies["cpu"].cget("text") != "—":
                break

        assert dashboard.overview._bodies["cpu"].cget("text") != "—"
        assert fast_monitor.latest_snapshot is not None
        dashboard._handle_close()
    finally:
        local_security_engine.stop()
        fast_monitor.stop()


# --- integration: Orchestrator -> EventBus -> Live Activity ------------------------------


def test_integration_orchestrator_tool_events_reach_live_activity(root, event_bus, monitor, security_engine, runner, tool_manager, context_manager, session, memory_service, settings, permission_manager, audit_logger):
    from ai.base import AIResponse, ToolCall
    from ai.router import AIRouter
    from core.orchestrator import Orchestrator
    from tests.conftest import FakeConfirmationService
    from tests.fakes import FakeAIProvider

    provider = FakeAIProvider(
        [
            AIResponse(content="", tool_calls=[ToolCall(id="1", name="check_cpu", arguments={})]),
            AIResponse(content="ok", tool_calls=[]),
        ]
    )
    router = AIRouter()
    router.register("fake", provider)
    orchestrator = Orchestrator(
        ai_router=router, tool_manager=tool_manager, context_manager=context_manager, session=session,
        memory_service=memory_service, settings=settings, permission_manager=permission_manager,
        confirmation_service=FakeConfirmationService(approve=True), audit_logger=audit_logger, event_bus=event_bus,
    )
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)

    orchestrator.handle_message("qual o uso de cpu?")
    _pump(root, iterations=3)

    feed_text = dashboard.live_activity.feed.get("1.0", "end")
    assert "CORE MODULE" in feed_text or "AI CORE" in feed_text
    dashboard._handle_close()


# --- integration: Lifecycle -> System Monitor -> Shutdown --------------------------------


def test_integration_lifecycle_ready_event_reaches_live_activity(root, event_bus, monitor, security_engine, runner):
    from core.lifecycle import LifecycleManager

    lifecycle = LifecycleManager(event_bus=event_bus)
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)

    lifecycle.begin_startup(first_run=False)
    lifecycle.finish_startup()
    _pump(root, iterations=2)

    feed_text = dashboard.live_activity.feed.get("1.0", "end")
    assert "STARTUP" in feed_text
    dashboard._handle_close()


# --- Security tab: state, findings, scan events ------------------------------------------


def test_security_tab_shows_disabled_state_before_engine_starts(root, event_bus, monitor, security_engine, runner):
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)
    assert "DESATIVADO" in dashboard.security._status_label.cget("text")
    dashboard._handle_close()


def test_security_tab_shows_protected_state_once_engine_running(root, event_bus, monitor, security_engine, runner):
    security_engine.start()
    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)
    text = dashboard.security._status_label.cget("text")
    assert "MONITORANDO" in text or "PROTEGIDO" in text
    dashboard._handle_close()


def test_security_finding_created_event_reaches_security_tab(root, event_bus, monitor, security_engine, runner):
    from security.events import SECURITY_FINDING_CREATED
    from security.models import FindingCategory, SecurityFinding, Severity, now

    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)
    finding = SecurityFinding(
        id="f1", timestamp=now(), severity=Severity.HIGH, category=FindingCategory.PROCESS,
        title="Processo suspeito", description="d", evidence=("motivo um", "motivo dois"), confidence=0.7,
        process_name="evil.exe", pid=4242,
    )

    event_bus.publish(SECURITY_FINDING_CREATED, {"finding": finding})
    _pump(root)

    rows = dashboard.security.tree.get_children()
    assert len(rows) == 1
    assert dashboard.security.tree.item(rows[0], "values")[1] == "Processo suspeito"
    assert "1 achado" in dashboard.security._summary_label.cget("text")
    dashboard._handle_close()


def test_security_scan_events_update_progress_label(root, event_bus, monitor, security_engine, runner):
    from security.events import SECURITY_SCAN_COMPLETED, SECURITY_SCAN_PROGRESS, SECURITY_SCAN_STARTED

    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)

    event_bus.publish(SECURITY_SCAN_STARTED, {"kind": "quick", "total": 5})
    _pump(root)
    assert dashboard.security._quick_scan_btn.cget("state") == "disabled"
    assert dashboard.security._cancel_btn.cget("state") == "normal"

    event_bus.publish(SECURITY_SCAN_PROGRESS, {"kind": "quick", "scanned": 2, "total": 5})
    _pump(root)
    assert "2/5" in dashboard.security._progress_label.cget("text")

    event_bus.publish(SECURITY_SCAN_COMPLETED, {"kind": "quick", "summary": None})
    _pump(root)
    assert dashboard.security._quick_scan_btn.cget("state") == "normal"
    assert dashboard.security._cancel_btn.cget("state") == "disabled"
    dashboard._handle_close()


def test_security_tab_no_callbacks_after_dashboard_close(root, event_bus, monitor, security_engine, runner):
    """Same stale-callback guard as the rest of the window (see window.py's
    _still_alive()) -- publishing security events after close must not raise."""
    from security.events import SECURITY_FINDING_CREATED, SECURITY_SCAN_STARTED
    from security.models import FindingCategory, SecurityFinding, Severity, now

    dashboard = _open_dashboard(root, event_bus, monitor, security_engine, runner)
    dashboard._handle_close()

    event_bus.publish(SECURITY_SCAN_STARTED, {"kind": "quick", "total": 1})
    event_bus.publish(
        SECURITY_FINDING_CREATED,
        {"finding": SecurityFinding(id="x", timestamp=now(), severity=Severity.LOW, category=FindingCategory.FILE, title="t", description="d", evidence=(), confidence=0.5)},
    )
