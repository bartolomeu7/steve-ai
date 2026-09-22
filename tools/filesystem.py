"""Safe, non-destructive filesystem tools: list, create directories, open files."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from security.permissions import PermissionLevel
from tools.base import Tool, ToolResult

_REQUIRED_PATH_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string", "description": "Caminho no sistema de arquivos"}},
    "required": ["path"],
}


class ListDirectoryTool(Tool):
    name = "list_directory"
    description = "Lista os arquivos e pastas de um diretório."
    permission_level = PermissionLevel.LOW
    parameters = {"path": "Caminho do diretório a listar"}
    parameters_schema = _REQUIRED_PATH_SCHEMA

    def validate(self, params: dict) -> tuple[bool, str]:
        path = params.get("path")
        if not path:
            return False, "Parâmetro 'path' é obrigatório."
        if not Path(path).exists():
            return False, f"Caminho não encontrado: {path}"
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        valid, error = self.validate(params)
        if not valid:
            return ToolResult(success=False, verified=False, message=error, error=error)
        path = Path(params["path"])
        entries = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
        return ToolResult(
            success=True,
            verified=True,
            message=f"{len(entries)} itens encontrados em {path}.",
            data={"path": str(path), "entries": entries},
        )


class CreateDirectoryTool(Tool):
    name = "create_directory"
    description = "Cria um novo diretório (não sobrescreve se já existir)."
    permission_level = PermissionLevel.LOW
    parameters = {"path": "Caminho do diretório a criar"}
    parameters_schema = _REQUIRED_PATH_SCHEMA

    def validate(self, params: dict) -> tuple[bool, str]:
        path = params.get("path")
        if not path:
            return False, "Parâmetro 'path' é obrigatório."
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        valid, error = self.validate(params)
        if not valid:
            return ToolResult(success=False, verified=False, message=error, error=error)
        path = Path(params["path"])
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return ToolResult(success=False, verified=False, message="Falha ao criar diretório.", error=str(exc))
        verified = path.exists() and path.is_dir()
        return ToolResult(
            success=True,
            verified=verified,
            message=f"Diretório criado: {path}." if verified else "Diretório não pôde ser confirmado.",
            data={"path": str(path)},
        )


class OpenFileTool(Tool):
    name = "open_file"
    description = "Abre um arquivo ou pasta com o aplicativo padrão do sistema (ex.: Explorer para pastas)."
    permission_level = PermissionLevel.LOW
    parameters = {"path": "Caminho do arquivo ou pasta a abrir"}
    parameters_schema = _REQUIRED_PATH_SCHEMA

    def validate(self, params: dict) -> tuple[bool, str]:
        path = params.get("path")
        if not path:
            return False, "Parâmetro 'path' é obrigatório."
        # PACK 3: originally file-only (is_file()); widened to accept directories too
        # (Pack 3's tools/open_path.py filled exactly this gap) so one tool covers both
        # instead of shipping a near-duplicate "open_path" alongside this one — both
        # end up calling the same os.startfile() either way.
        if not Path(path).exists():
            return False, f"Caminho não encontrado: {path}"
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        valid, error = self.validate(params)
        if not valid:
            return ToolResult(success=False, verified=False, message=error, error=error)
        path = Path(params["path"])
        try:
            if sys.platform == "win32":
                os.startfile(str(path))  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as exc:
            return ToolResult(success=False, verified=False, message="Falha ao abrir arquivo.", error=str(exc))
        kind = "pasta" if path.is_dir() else "arquivo"
        return ToolResult(
            success=True,
            verified=True,
            message=f"Abri a {kind}: {path}.",
            data={"path": str(path)},
        )


_WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Caminho do arquivo a escrever"},
        "content": {"type": "string", "description": "Conteudo de texto a gravar"},
        "append": {"type": "boolean", "description": "Se true, acrescenta; senao sobrescreve", "default": False},
    },
    "required": ["path", "content"],
}

_DELETE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Arquivo ou pasta vazia a apagar"},
    },
    "required": ["path"],
}


class WriteTextFileTool(Tool):
    name = "write_text_file"
    description = (
        "Escreve texto em um arquivo (cria se nao existir). "
        "Use so quando o usuario pedir explicitamente para criar/salvar um arquivo."
    )
    permission_level = PermissionLevel.HIGH
    parameters = {"path": "Caminho", "content": "Texto", "append": "Acrescentar?"}
    parameters_schema = _WRITE_SCHEMA

    def validate(self, params: dict) -> tuple[bool, str]:
        if not params.get("path"):
            return False, "Parametro 'path' e obrigatorio."
        if params.get("content") is None:
            return False, "Parametro 'content' e obrigatorio."
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        valid, error = self.validate(params)
        if not valid:
            return ToolResult(success=False, verified=False, message=error, error=error)
        path = Path(params["path"])
        content = str(params["content"])
        append = bool(params.get("append", False))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with path.open(mode, encoding="utf-8") as fh:
                fh.write(content)
        except OSError as exc:
            return ToolResult(success=False, verified=False, message="Falha ao escrever arquivo.", error=str(exc))
        return ToolResult(
            success=True,
            verified=path.is_file(),
            message=f"Arquivo gravado: {path}.",
            data={"path": str(path), "bytes": path.stat().st_size if path.exists() else 0},
        )


class DeletePathTool(Tool):
    name = "delete_path"
    description = (
        "Apaga um arquivo ou pasta vazia. NUNCA use sem o usuario pedir explicitamente. "
        "Pastas com conteudo sao recusadas (seguranca)."
    )
    permission_level = PermissionLevel.HIGH
    parameters = {"path": "Caminho a apagar"}
    parameters_schema = _DELETE_SCHEMA

    def validate(self, params: dict) -> tuple[bool, str]:
        path = params.get("path")
        if not path:
            return False, "Parametro 'path' e obrigatorio."
        p = Path(path)
        if not p.exists():
            return False, f"Caminho nao encontrado: {path}"
        if p.is_dir() and any(p.iterdir()):
            return False, "Pasta nao esta vazia; recusado por seguranca."
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        valid, error = self.validate(params)
        if not valid:
            return ToolResult(success=False, verified=False, message=error, error=error)
        path = Path(params["path"])
        try:
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
        except OSError as exc:
            return ToolResult(success=False, verified=False, message="Falha ao apagar.", error=str(exc))
        gone = not path.exists()
        return ToolResult(
            success=gone,
            verified=gone,
            message=f"Apagado: {path}." if gone else "Nao confirmei a exclusao.",
            data={"path": str(path)},
        )
