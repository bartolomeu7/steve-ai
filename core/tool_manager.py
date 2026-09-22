"""Registry that exposes tools to the orchestrator and, via ToolSpec, to the AI provider's
native tool-calling mechanism."""
from __future__ import annotations

from ai.base import ToolSpec
from tools.base import Tool, ToolResult


class UnknownToolError(Exception):
    pass


class ToolManager:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise UnknownToolError(f"Ferramenta desconhecida: {name}")
        return self._tools[name]

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def describe_all(self) -> list[dict]:
        return [tool.describe() for tool in self._tools.values()]

    def to_tool_specs(self) -> list[ToolSpec]:
        """Provider-agnostic tool descriptions, ready to hand to AIProvider.chat(tools=...)."""
        return [
            ToolSpec(name=tool.name, description=tool.description, parameters_schema=tool.parameters_schema)
            for tool in self._tools.values()
        ]

    def execute(self, name: str, params: dict) -> ToolResult:
        try:
            tool = self.get(name)
        except UnknownToolError as exc:
            return ToolResult(success=False, verified=False, message=str(exc), error=str(exc))
        try:
            valid, error = tool.validate(params)
            if not valid:
                return ToolResult(success=False, verified=False, message=error, error=error)
            return tool.execute(params)
        except Exception as exc:  # noqa: BLE001 - never crash the orchestrator on a tool
            return ToolResult(
                success=False,
                verified=False,
                message=f"Falha ao executar {name}.",
                error=str(exc),
            )
