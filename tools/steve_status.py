"""Report Steve runtime status for the model / user."""
from __future__ import annotations

from security.permissions import PermissionLevel
from tools.base import Tool, ToolResult

_SCHEMA = {"type": "object", "properties": {}, "required": []}


class SteveStatusTool(Tool):
    """Injected with a callable that returns a status dict (wired in main)."""

    name = "steve_status"
    description = (
        "Consulta o estado interno do Steve: modo de UI, modelo de IA, "
        "quantas tools estao ativas, learning root, etc."
    )
    permission_level = PermissionLevel.LOW
    parameters = {}
    parameters_schema = _SCHEMA

    def __init__(self, status_fn=None):
        self._status_fn = status_fn

    def validate(self, params: dict) -> tuple[bool, str]:
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        if self._status_fn is None:
            return ToolResult(success=False, verified=False, message="Status indisponivel.", error="no_fn")
        try:
            data = dict(self._status_fn() or {})
        except Exception as exc:
            return ToolResult(success=False, verified=False, message="Falha ao ler status.", error=str(exc))
        summary = ", ".join(f"{k}={v}" for k, v in list(data.items())[:12])
        return ToolResult(success=True, verified=True, message=f"Status Steve: {summary}", data=data)
