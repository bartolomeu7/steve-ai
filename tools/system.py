"""Read-only system inspection tools: CPU, RAM and disk usage."""
from __future__ import annotations

import psutil

from security.permissions import PermissionLevel
from tools.base import Tool, ToolResult


class CheckCPUTool(Tool):
    name = "check_cpu"
    description = "Consulta o uso atual de CPU do computador."
    permission_level = PermissionLevel.LOW
    parameters = {}

    def validate(self, params: dict) -> tuple[bool, str]:
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        percent = psutil.cpu_percent(interval=0.5)
        return ToolResult(
            success=True,
            verified=True,
            message=f"Uso de CPU: {percent}%.",
            data={"cpu_percent": percent},
        )


class CheckRAMTool(Tool):
    name = "check_ram"
    description = "Consulta o uso atual de memória RAM."
    permission_level = PermissionLevel.LOW
    parameters = {}

    def validate(self, params: dict) -> tuple[bool, str]:
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        mem = psutil.virtual_memory()
        return ToolResult(
            success=True,
            verified=True,
            message=f"Uso de RAM: {mem.percent}% ({mem.used // (1024**2)} MB de {mem.total // (1024**2)} MB).",
            data={
                "percent": mem.percent,
                "used_mb": mem.used // (1024**2),
                "total_mb": mem.total // (1024**2),
            },
        )


class CheckDiskTool(Tool):
    name = "check_disk"
    description = "Consulta o uso de disco de uma unidade (padrão C:\\)."
    permission_level = PermissionLevel.LOW
    parameters = {"path": "Unidade ou caminho a verificar (opcional, padrão C:\\)"}
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Unidade ou caminho a verificar (opcional, padrão C:\\)"}
        },
        "required": [],
    }

    def validate(self, params: dict) -> tuple[bool, str]:
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        path = params.get("path") or "C:\\"
        try:
            usage = psutil.disk_usage(path)
        except OSError as exc:
            return ToolResult(success=False, verified=False, message="Falha ao consultar disco.", error=str(exc))
        return ToolResult(
            success=True,
            verified=True,
            message=f"Uso de disco em {path}: {usage.percent}% ({usage.used // (1024**3)} GB de {usage.total // (1024**3)} GB).",
            data={
                "path": path,
                "percent": usage.percent,
                "used_gb": usage.used // (1024**3),
                "total_gb": usage.total // (1024**3),
            },
        )
