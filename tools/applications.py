"""Open known applications and list running processes."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time

import psutil

from security.permissions import PermissionLevel
from tools.base import Tool, ToolResult

_SAFE_NAME = re.compile(r"^[A-Za-z0-9 _.\-]{1,80}$")

_APP_ALIASES = {
    "chrome": "chrome",
    "google chrome": "chrome",
    "notepad": "notepad",
    "bloco de notas": "notepad",
    "calculator": "calc",
    "calculadora": "calc",
    "explorer": "explorer",
    "explorador de arquivos": "explorer",
    "edge": "msedge",
    "word": "winword",
    "excel": "excel",
    "vscode": "code",
    "visual studio code": "code",
    "terminal": "wt",
    "spotify": "spotify",
}


class OpenApplicationTool(Tool):
    name = "open_application"
    description = "Abre um aplicativo conhecido pelo nome (ex.: chrome, notepad, calculator)."
    permission_level = PermissionLevel.LOW
    parameters = {"app_name": "Nome do aplicativo a abrir"}
    parameters_schema = {
        "type": "object",
        "properties": {
            "app_name": {
                "type": "string",
                "description": "Nome do aplicativo a abrir (ex.: chrome, notepad, calculator)",
            }
        },
        "required": ["app_name"],
    }

    def validate(self, params: dict) -> tuple[bool, str]:
        app_name = params.get("app_name")
        if not app_name:
            return False, "Parâmetro 'app_name' é obrigatório."
        if not _SAFE_NAME.match(app_name):
            return False, "Nome de aplicativo contém caracteres não permitidos."
        return True, ""

    def _resolve(self, app_name: str) -> str:
        return _APP_ALIASES.get(app_name.strip().lower(), app_name.strip())

    @staticmethod
    def _running_process_names() -> set[str]:
        names = set()
        for proc in psutil.process_iter(["name"]):
            proc_name = proc.info.get("name")
            if proc_name:
                names.add(proc_name.lower())
        return names

    def execute(self, params: dict) -> ToolResult:
        valid, error = self.validate(params)
        if not valid:
            return ToolResult(success=False, verified=False, message=error, error=error)
        command = self._resolve(params["app_name"])
        before = self._running_process_names()
        try:
            if sys.platform == "win32":
                # os.startfile resolves bare app names via the Windows "App Paths"
                # registry (same mechanism as Win+R), unlike subprocess.Popen which
                # only searches PATH and would fail to launch e.g. Chrome or Word.
                os.startfile(command)  # noqa: S606
            else:
                subprocess.Popen([command])
        except OSError as exc:
            return ToolResult(
                success=False,
                verified=False,
                message=f"Não foi possível abrir '{command}'.",
                error=str(exc),
            )
        time.sleep(0.8)
        after = self._running_process_names()
        verified = len(after - before) > 0
        return ToolResult(
            success=True,
            verified=verified,
            message=(
                f"'{command}' foi iniciado." if verified else f"Enviei o comando para abrir '{command}', mas não consegui confirmar que ele abriu."
            ),
            data={"command": command},
        )


class ListProcessesTool(Tool):
    name = "list_processes"
    description = "Lista os processos em execução no sistema."
    permission_level = PermissionLevel.LOW
    parameters = {"limit": "Número máximo de processos a retornar (padrão 20)"}
    parameters_schema = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Número máximo de processos a retornar (padrão 20)",
            }
        },
        "required": [],
    }

    def validate(self, params: dict) -> tuple[bool, str]:
        limit = params.get("limit", 20)
        if not isinstance(limit, int) or limit <= 0:
            return False, "Parâmetro 'limit' deve ser um inteiro positivo."
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        valid, error = self.validate(params)
        if not valid:
            return ToolResult(success=False, verified=False, message=error, error=error)
        limit = params.get("limit", 20)
        processes = []
        for proc in psutil.process_iter(["pid", "name"]):
            processes.append({"pid": proc.info["pid"], "name": proc.info["name"]})
            if len(processes) >= limit:
                break
        return ToolResult(
            success=True,
            verified=True,
            message=f"{len(processes)} processos listados.",
            data={"processes": processes},
        )
