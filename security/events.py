"""Security Center event names — published on the SAME shared core.events.EventBus every
other Steve component uses (core/lifecycle.py, system_monitor/service.py,
core/orchestrator.py). No second EventBus is created for this module."""
from __future__ import annotations

SECURITY_ENGINE_STARTED = "security.engine_started"
SECURITY_ENGINE_STOPPED = "security.engine_stopped"
SECURITY_ENGINE_ERROR = "security.engine_error"
SECURITY_FINDING_CREATED = "security.finding_created"
SECURITY_SCAN_STARTED = "security.scan_started"
SECURITY_SCAN_PROGRESS = "security.scan_progress"
SECURITY_SCAN_COMPLETED = "security.scan_completed"
SECURITY_SCAN_CANCELLED = "security.scan_cancelled"
