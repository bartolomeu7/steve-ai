"""Windows balloon notification tool."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from security.permissions import PermissionLevel
from tools.base import Tool, ToolResult

logger = logging.getLogger("steve.tools.notify")

_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Titulo curto"},
        "message": {"type": "string", "description": "Corpo da notificacao"},
    },
    "required": ["message"],
}


class NotifyTool(Tool):
    name = "notify"
    description = "Mostra uma notificacao no Windows (balloon) para o usuario."
    permission_level = PermissionLevel.LOW
    parameters = {"title": "Titulo", "message": "Mensagem"}
    parameters_schema = _SCHEMA

    def validate(self, params: dict) -> tuple[bool, str]:
        if not params.get("message"):
            return False, "Parametro 'message' e obrigatorio."
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        valid, error = self.validate(params)
        if not valid:
            return ToolResult(success=False, verified=False, message=error, error=error)
        title = str(params.get("title") or "Steve")[:80]
        message = str(params["message"])[:240]
        if sys.platform != "win32":
            return ToolResult(success=False, verified=False, message="Notify so no Windows.", error="unsupported")
        script = Path(__file__).resolve().parent / "_notify_balloon.ps1"
        # inline tiny script via -EncodedCommand avoidance: write temp args file
        import tempfile, json
        payload = {"title": title, "message": message}
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
                json.dump(payload, fh)
                payload_path = fh.name
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "Add-Type -AssemblyName System.Drawing; "
                f"$j = Get-Content -Raw -Encoding UTF8 '{payload_path}' | ConvertFrom-Json; "
                "$n = New-Object System.Windows.Forms.NotifyIcon; "
                "$n.Icon = [System.Drawing.SystemIcons]::Information; "
                "$n.Visible = $true; "
                "$n.BalloonTipTitle = $j.title; "
                "$n.BalloonTipText = $j.message; "
                "$n.ShowBalloonTip(4000); "
                "Start-Sleep -Milliseconds 4500; "
                "$n.Dispose(); "
                f"Remove-Item -Force '{payload_path}' -ErrorAction SilentlyContinue"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                check=False,
                timeout=20,
                capture_output=True,
            )
            return ToolResult(
                success=True,
                verified=True,
                message=f"Notificacao enviada: {title}",
                data={"title": title},
            )
        except Exception as exc:
            logger.exception("notify failed")
            return ToolResult(success=False, verified=False, message="Falha na notificacao.", error=str(exc))
