"""Active window — which app/window is currently focused. Read-only, stdlib only.

Adapted from Pack 3's tools/active_window.py: the pure ctypes lookup logic is kept as
plain module-level functions (also reused directly by voice/action_narrator.py for
"you're on Chrome, I'll search in the background" phrasing, without going through
ToolManager — that's a narration detail, not an action needing permission-gating), and
wrapped in a real Tool for when the model itself wants to know the active window.
"""
from __future__ import annotations

import logging
import sys

from security.permissions import PermissionLevel
from tools.base import Tool, ToolResult

logger = logging.getLogger("steve.tools.active_window")


def get_active_window_title() -> str:
    """Returns the foreground window's title, or "" if unavailable (non-Windows, or
    the lookup failed) — never raises."""
    if sys.platform != "win32":
        return ""
    try:
        from ctypes import create_unicode_buffer, windll

        hwnd = windll.user32.GetForegroundWindow()
        length = windll.user32.GetWindowTextLengthW(hwnd)
        buf = create_unicode_buffer(length + 1)
        windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or ""
    except Exception as exc:
        logger.warning("Erro ao obter título da janela ativa: %s", exc)
        return ""


class ActiveWindowTool(Tool):
    name = "active_window"
    description = "Descobre o título da janela/aplicativo que o usuário está usando agora, em primeiro plano."
    permission_level = PermissionLevel.LOW
    parameters = {}
    parameters_schema = {"type": "object", "properties": {}, "required": []}

    def validate(self, params: dict) -> tuple[bool, str]:
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        title = get_active_window_title()
        if not title:
            return ToolResult(
                success=False,
                verified=False,
                message="Não consegui identificar a janela ativa.",
                error="janela ativa indisponível ou plataforma não suportada",
            )
        return ToolResult(
            success=True,
            verified=True,
            message=f"Janela ativa: «{title}».",
            data={"title": title},
        )
