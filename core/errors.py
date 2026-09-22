"""Central error types so the orchestrator can degrade gracefully instead of crashing."""
from __future__ import annotations


class SteveError(Exception):
    """Base class for all Steve-specific errors."""


class AIProviderUnavailableError(SteveError):
    """Raised when the configured AI provider cannot be reached."""


class ToolExecutionError(SteveError):
    """Raised when a tool fails in a way that must interrupt the current turn."""
