"""Steve's lifecycle: an explicit, observable state machine for startup/shutdown, and
the single source of truth for "is Steve actually working right now" — a `SystemStatus`
snapshot any UI layer can read instead of re-deriving it from scattered flags
(`ai_available`, `voice_service is None`, ...).

Boot sequence (see docs/ARCHITECTURE.md for the full rationale): config -> logging ->
memory -> AI Router -> tools -> Orchestrator -> voice (optional) -> GUI (optional) ->
READY/DEGRADED. Each step reports itself via `report_component()` as it happens;
`finish_startup()` computes the final state from what was reported — nothing sets
READY/DEGRADED by hand.

Publishes lifecycle events on the existing `core.events.EventBus` (previously defined but
never wired to anything) so a future consumer (System Dashboard, a Live Activity view)
can observe startup/shutdown without this module knowing anything about them.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from core.events import EventBus

logger = logging.getLogger("steve.lifecycle")

STARTUP_BEGIN = "lifecycle.startup_begin"
COMPONENT_INITIALIZING = "lifecycle.component_initializing"
COMPONENT_READY = "lifecycle.component_ready"
COMPONENT_FAILED = "lifecycle.component_failed"
STEVE_READY = "lifecycle.steve_ready"
STEVE_DEGRADED = "lifecycle.steve_degraded"
SHUTDOWN_BEGIN = "lifecycle.shutdown_begin"
SHUTDOWN_COMPLETE = "lifecycle.shutdown_complete"


class LifecycleState(str, Enum):
    CREATED = "created"
    INITIALIZING = "initializing"
    READY = "ready"
    DEGRADED = "degraded"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"


@dataclass
class ComponentStatus:
    name: str
    ok: bool
    detail: str = ""
    #: Informational only in this version (surfaced to future consumers like a System
    #: Dashboard, see module docstring) — a failed essential component still results in
    #: DEGRADED here, not a special state. Whether an essential failure should actually
    #: abort startup is a decision made by the caller (main.py), not by this module: some
    #: essential failures (e.g. the database not opening at all) can't be "reported and
    #: continued" — main.py catches those directly and exits cleanly, with a
    #: COMPONENT_FAILED event already on record before it does.
    essential: bool = False


@dataclass
class SystemStatus:
    """The single source of truth a UI layer should read instead of re-deriving status
    from scattered flags."""

    lifecycle_state: LifecycleState = LifecycleState.CREATED
    first_run: bool = False
    startup_mode: str = "gui"
    components: dict[str, ComponentStatus] = field(default_factory=dict)

    @property
    def degraded_reasons(self) -> list[str]:
        return [c.detail or c.name for c in self.components.values() if not c.ok]

    def component(self, name: str) -> ComponentStatus | None:
        return self.components.get(name)

    def is_ok(self, name: str) -> bool:
        status = self.components.get(name)
        return status.ok if status is not None else False


class LifecycleManager:
    """One instance per process, shared between main.py's boot sequence and whichever UI
    (CLI or GUI) is running — so both read the exact same status instead of keeping their
    own copies in sync by hand."""

    def __init__(self, event_bus: EventBus | None = None):
        self.event_bus = event_bus or EventBus()
        self.status = SystemStatus()

    @property
    def state(self) -> LifecycleState:
        return self.status.lifecycle_state

    def begin_startup(self, first_run: bool, startup_mode: str = "gui") -> None:
        self.status = SystemStatus(
            lifecycle_state=LifecycleState.INITIALIZING, first_run=first_run, startup_mode=startup_mode
        )
        self._publish(STARTUP_BEGIN, {"first_run": first_run, "startup_mode": startup_mode})

    def component_initializing(self, name: str) -> None:
        self._publish(COMPONENT_INITIALIZING, {"component": name})

    def report_component(self, name: str, ok: bool, detail: str = "", essential: bool = False) -> None:
        self.status.components[name] = ComponentStatus(name=name, ok=ok, detail=detail, essential=essential)
        self._publish(COMPONENT_READY if ok else COMPONENT_FAILED, {"component": name, "detail": detail})
        if not ok:
            logger.warning("Componente '%s' falhou na inicialização: %s", name, detail or "sem detalhes")

    def finish_startup(self) -> SystemStatus:
        any_failed = any(not c.ok for c in self.status.components.values())
        self.status.lifecycle_state = LifecycleState.DEGRADED if any_failed else LifecycleState.READY
        event = STEVE_DEGRADED if any_failed else STEVE_READY
        self._publish(event, {"degraded_reasons": self.status.degraded_reasons})
        if any_failed:
            logger.warning("Steve iniciado em modo DEGRADED: %s", ", ".join(self.status.degraded_reasons))
        else:
            logger.info("Steve pronto (READY).")
        return self.status

    def begin_shutdown(self) -> None:
        self.status.lifecycle_state = LifecycleState.SHUTTING_DOWN
        self._publish(SHUTDOWN_BEGIN)

    def finish_shutdown(self) -> None:
        self.status.lifecycle_state = LifecycleState.STOPPED
        self._publish(SHUTDOWN_COMPLETE)

    def _publish(self, event_name: str, data: dict | None = None) -> None:
        self.event_bus.publish(event_name, data or {})
