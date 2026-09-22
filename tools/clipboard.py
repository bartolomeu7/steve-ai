"""Clipboard — read, summarize or write the Windows clipboard.

Base: pyperclip (BSD-3-Clause, https://github.com/asweigart/pyperclip) — mature,
cross-platform, tkinter-clipboard fallback built in. Adapted from Pack 3's
tools/clipboard.py (same underlying pyperclip calls) into a real Tool.

Permission: MEDIUM, not LOW like most read-only system tools — unlike CPU/RAM/disk,
the clipboard can hold whatever the user last copied (a password, a personal message,
anything), so reading it without asking first would be a real privacy regression, not
just a style choice.
"""
from __future__ import annotations

import logging

from security.permissions import PermissionLevel
from tools.base import Tool, ToolResult

logger = logging.getLogger("steve.tools.clipboard")

MAX_SUMMARY_CHARS = 500


def _get_clipboard_text() -> str:
    try:
        import pyperclip

        return pyperclip.paste() or ""
    except Exception:
        try:
            from tkinter import Tk

            root = Tk()
            root.withdraw()
            text = root.clipboard_get()
            root.destroy()
            return text or ""
        except Exception as exc:
            logger.warning("Não foi possível ler o clipboard: %s", exc)
            return ""


def _set_clipboard_text(text: str) -> bool:
    try:
        import pyperclip

        pyperclip.copy(text)
        return True
    except Exception as exc:
        logger.warning("Não foi possível escrever no clipboard: %s", exc)
        return False


class ClipboardTool(Tool):
    name = "clipboard"
    description = (
        "Lê, resume ou escreve o conteúdo da área de transferência (clipboard) do Windows. "
        "Use para saber o que o usuário copiou recentemente, ou para colocar um texto no clipboard dele."
    )
    permission_level = PermissionLevel.MEDIUM
    parameters = {"action": "read | summarize | copy", "text": "Texto a copiar (só para action=copy)"}
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "summarize", "copy"],
                "description": "read = ler o texto todo, summarize = resumo curto, copy = escrever um texto",
            },
            "text": {"type": "string", "description": "Texto a copiar (obrigatório só para action=copy)"},
        },
        "required": ["action"],
    }

    def validate(self, params: dict) -> tuple[bool, str]:
        action = params.get("action")
        if action not in ("read", "summarize", "copy"):
            return False, "Parâmetro 'action' deve ser 'read', 'summarize' ou 'copy'."
        if action == "copy" and not params.get("text"):
            return False, "Parâmetro 'text' é obrigatório para action=copy."
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        valid, error = self.validate(params)
        if not valid:
            return ToolResult(success=False, verified=False, message=error, error=error)

        action = params["action"]

        if action == "copy":
            ok = _set_clipboard_text(params["text"])
            if not ok:
                return ToolResult(success=False, verified=False, message="Falha ao copiar para o clipboard.", error="pyperclip indisponível")
            return ToolResult(success=True, verified=True, message="Texto copiado para o clipboard.", data={"copied": params["text"]})

        text = _get_clipboard_text()
        if not text.strip():
            return ToolResult(success=True, verified=True, message="A área de transferência está vazia.", data={"text": ""})

        if action == "read":
            return ToolResult(success=True, verified=True, message="Conteúdo do clipboard lido.", data={"text": text})

        # summarize
        if len(text) <= MAX_SUMMARY_CHARS:
            return ToolResult(success=True, verified=True, message="Clipboard é curto, texto completo abaixo.", data={"text": text, "truncated": False})
        preview = text[:MAX_SUMMARY_CHARS].rsplit(" ", 1)[0]
        return ToolResult(
            success=True,
            verified=True,
            message=f"Clipboard tem {len(text)} caracteres; prévia incluída.",
            data={"text": preview, "truncated": True, "total_chars": len(text)},
        )
