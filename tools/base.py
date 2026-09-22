"""Base contract every Steve tool must implement."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from security.permissions import PermissionLevel


@dataclass
class ToolResult:
    success: bool
    verified: bool
    message: str
    data: Any = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "verified": self.verified,
            "message": self.message,
            "data": self.data,
            "error": self.error,
        }


class Tool(ABC):
    name: str
    description: str
    permission_level: PermissionLevel
    parameters: dict = {}
    # JSON Schema for the tool's arguments, advertised to the AI provider's native
    # tool-calling mechanism. Defaults to "no parameters"; override in subclasses that take any.
    parameters_schema: dict = {"type": "object", "properties": {}, "required": []}

    @abstractmethod
    def validate(self, params: dict) -> tuple[bool, str]:
        """Return (is_valid, error_message)."""

    @abstractmethod
    def execute(self, params: dict) -> ToolResult:
        """Perform the action and return a verifiable result."""

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "permission_level": self.permission_level.name,
            "parameters": self.parameters,
        }
