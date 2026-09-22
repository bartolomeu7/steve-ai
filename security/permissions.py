"""Permission levels for tools and the policy that decides what needs confirmation."""
from __future__ import annotations

from enum import IntEnum


class PermissionLevel(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3

    @classmethod
    def from_str(cls, value: str) -> "PermissionLevel":
        return cls[value.upper()]


class PermissionManager:
    """Decides whether a tool at a given permission level may run without asking."""

    def __init__(self, auto_approve_up_to: PermissionLevel = PermissionLevel.LOW):
        self.auto_approve_up_to = auto_approve_up_to

    def requires_confirmation(self, level: PermissionLevel) -> bool:
        return level > self.auto_approve_up_to

    def is_allowed_level(self, level: PermissionLevel, max_allowed: PermissionLevel) -> bool:
        return level <= max_allowed
