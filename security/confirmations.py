"""Confirmation prompts for actions that require explicit user approval."""
from __future__ import annotations

from abc import ABC, abstractmethod


class ConfirmationService(ABC):
    @abstractmethod
    def confirm(self, message: str) -> bool:
        """Ask the user to approve an action; return True if approved."""


class CLIConfirmationService(ConfirmationService):
    def confirm(self, message: str) -> bool:
        answer = input(f"{message} (s/N): ").strip().lower()
        return answer in ("s", "sim", "y", "yes")


class AutoDenyConfirmationService(ConfirmationService):
    """Safe default for non-interactive contexts: never approves sensitive actions."""

    def confirm(self, message: str) -> bool:
        return False


class GuiConfirmationService(ConfirmationService):
    """Runs `ask_fn(message) -> bool` on the caller's terms (UI thread marshal)."""

    def __init__(self, ask_fn):
        self._ask_fn = ask_fn

    def confirm(self, message: str) -> bool:
        try:
            return bool(self._ask_fn(message))
        except Exception:
            return False
