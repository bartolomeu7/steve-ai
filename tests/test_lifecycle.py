from __future__ import annotations

from core.events import EventBus
from core.lifecycle import (
    COMPONENT_FAILED,
    COMPONENT_READY,
    SHUTDOWN_BEGIN,
    SHUTDOWN_COMPLETE,
    STARTUP_BEGIN,
    STEVE_DEGRADED,
    STEVE_READY,
    LifecycleManager,
    LifecycleState,
)


def test_initial_state_is_created():
    manager = LifecycleManager()
    assert manager.state == LifecycleState.CREATED


def test_begin_startup_sets_initializing_and_records_first_run_and_mode():
    manager = LifecycleManager()
    manager.begin_startup(first_run=True, startup_mode="gui")

    assert manager.state == LifecycleState.INITIALIZING
    assert manager.status.first_run is True
    assert manager.status.startup_mode == "gui"


def test_finish_startup_is_ready_when_everything_ok():
    manager = LifecycleManager()
    manager.begin_startup(first_run=False)
    manager.report_component("memory", ok=True, essential=True)
    manager.report_component("ai_router", ok=True)

    status = manager.finish_startup()

    assert status.lifecycle_state == LifecycleState.READY
    assert manager.state == LifecycleState.READY
    assert status.degraded_reasons == []


def test_finish_startup_is_degraded_when_a_secondary_component_fails():
    """AI Router down (Ollama offline) — Steve stays usable, just DEGRADED, exactly like
    the spec's own example."""
    manager = LifecycleManager()
    manager.begin_startup(first_run=False)
    manager.report_component("memory", ok=True, essential=True)
    manager.report_component("gui", ok=True, essential=True)
    manager.report_component("ai_router", ok=False, detail="provider indisponível")

    status = manager.finish_startup()

    assert status.lifecycle_state == LifecycleState.DEGRADED
    assert "provider indisponível" in status.degraded_reasons


def test_finish_startup_degraded_reasons_list_every_failed_component():
    manager = LifecycleManager()
    manager.begin_startup(first_run=False)
    manager.report_component("ai_router", ok=False, detail="ai indisponível")
    manager.report_component("voice", ok=False, detail="tts indisponível")

    status = manager.finish_startup()

    assert set(status.degraded_reasons) == {"ai indisponível", "tts indisponível"}


def test_component_status_lookup_helpers():
    manager = LifecycleManager()
    manager.begin_startup(first_run=False)
    manager.report_component("voice", ok=False, detail="sem microfone")

    assert manager.status.is_ok("voice") is False
    assert manager.status.is_ok("nonexistent") is False
    assert manager.status.component("voice").detail == "sem microfone"
    assert manager.status.component("nonexistent") is None


def test_begin_shutdown_and_finish_shutdown_transition_states():
    manager = LifecycleManager()
    manager.begin_startup(first_run=False)
    manager.finish_startup()

    manager.begin_shutdown()
    assert manager.state == LifecycleState.SHUTTING_DOWN

    manager.finish_shutdown()
    assert manager.state == LifecycleState.STOPPED


def test_lifecycle_events_are_published_on_the_event_bus():
    bus = EventBus()
    received: list[tuple[str, dict]] = []
    for event_name in (STARTUP_BEGIN, COMPONENT_READY, COMPONENT_FAILED, STEVE_READY, STEVE_DEGRADED, SHUTDOWN_BEGIN, SHUTDOWN_COMPLETE):
        bus.subscribe(event_name, lambda data, name=event_name: received.append((name, data)))

    manager = LifecycleManager(event_bus=bus)
    manager.begin_startup(first_run=False)
    manager.report_component("memory", ok=True)
    manager.finish_startup()
    manager.begin_shutdown()
    manager.finish_shutdown()

    event_names = [name for name, _ in received]
    assert event_names == [STARTUP_BEGIN, COMPONENT_READY, STEVE_READY, SHUTDOWN_BEGIN, SHUTDOWN_COMPLETE]


def test_lifecycle_publishes_steve_degraded_not_steve_ready_when_something_failed():
    bus = EventBus()
    received: list[str] = []
    bus.subscribe(STEVE_READY, lambda data: received.append("ready"))
    bus.subscribe(STEVE_DEGRADED, lambda data: received.append("degraded"))

    manager = LifecycleManager(event_bus=bus)
    manager.begin_startup(first_run=False)
    manager.report_component("ai_router", ok=False, detail="down")
    manager.finish_startup()

    assert received == ["degraded"]


def test_manager_creates_its_own_event_bus_when_none_given():
    manager = LifecycleManager()
    assert isinstance(manager.event_bus, EventBus)


def test_begin_startup_resets_components_from_a_previous_run():
    """A fresh SystemStatus each begin_startup() — no stale component data leaking from
    an earlier boot (relevant if the same LifecycleManager were ever reused, e.g. tests)."""
    manager = LifecycleManager()
    manager.begin_startup(first_run=False)
    manager.report_component("voice", ok=False, detail="old failure")

    manager.begin_startup(first_run=False)

    assert manager.status.components == {}
