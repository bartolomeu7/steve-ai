"""System volume — get/set/mute the Windows master audio endpoint.

Base: pycaw (MIT, https://github.com/AndreMiras/pycaw) — the standard Python wrapper
around Windows Core Audio; requires comtypes (MIT), also added to requirements.txt.
Adapted from Pack 3's tools/volume.py: same pycaw calls, wrapped as a real Tool instead
of the package's standalone TOOL_SCHEMA+run() pair.
"""
from __future__ import annotations

import logging

from security.permissions import PermissionLevel
from tools.base import Tool, ToolResult

logger = logging.getLogger("steve.tools.volume")


def _get_endpoint():
    from comtypes import CLSCTX_ALL
    from ctypes import cast, POINTER
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


class VolumeTool(Tool):
    name = "volume"
    description = "Consulta ou controla o volume/mute do sistema Windows."
    permission_level = PermissionLevel.LOW
    parameters = {"action": "get | set | mute | unmute | toggle_mute", "percent": "Volume 0-100 (só para action=set)"}
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["get", "set", "mute", "unmute", "toggle_mute"],
                "description": "Ação a executar",
            },
            "percent": {"type": "integer", "description": "Volume desejado 0-100 (só para action=set)"},
        },
        "required": ["action"],
    }

    def validate(self, params: dict) -> tuple[bool, str]:
        action = params.get("action")
        if action not in ("get", "set", "mute", "unmute", "toggle_mute"):
            return False, "Parâmetro 'action' inválido."
        if action == "set" and params.get("percent") is None:
            return False, "Parâmetro 'percent' é obrigatório para action=set."
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        valid, error = self.validate(params)
        if not valid:
            return ToolResult(success=False, verified=False, message=error, error=error)

        action = params["action"]
        try:
            endpoint = _get_endpoint()
        except Exception as exc:
            logger.warning("Falha ao acessar o endpoint de áudio: %s", exc)
            return ToolResult(success=False, verified=False, message="Não consegui acessar o controle de volume.", error=str(exc))

        try:
            if action == "get":
                percent = int(round(endpoint.GetMasterVolumeLevelScalar() * 100))
                muted = bool(endpoint.GetMute())
                return ToolResult(
                    success=True, verified=True,
                    message=f"Volume atual: {percent}%{' (mutado)' if muted else ''}.",
                    data={"percent": percent, "muted": muted},
                )
            if action == "set":
                target = max(0, min(100, int(params["percent"])))
                endpoint.SetMasterVolumeLevelScalar(target / 100.0, None)
                applied = int(round(endpoint.GetMasterVolumeLevelScalar() * 100))
                return ToolResult(
                    success=True, verified=(applied == target),
                    message=f"Volume definido para {applied}%.",
                    data={"percent": applied},
                )
            if action in ("mute", "unmute", "toggle_mute"):
                if action == "toggle_mute":
                    new_state = not bool(endpoint.GetMute())
                else:
                    new_state = action == "mute"
                endpoint.SetMute(1 if new_state else 0, None)
                verified = bool(endpoint.GetMute()) == new_state
                return ToolResult(
                    success=True, verified=verified,
                    message="Áudio mutado." if new_state else "Áudio desmutado.",
                    data={"muted": new_state},
                )
        except Exception as exc:
            logger.warning("Falha ao executar ação de volume '%s': %s", action, exc)
            return ToolResult(success=False, verified=False, message="Falha ao controlar o volume.", error=str(exc))
