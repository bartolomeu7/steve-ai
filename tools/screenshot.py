"""Screenshot — captures the primary monitor to a PNG file.

Base: mss (MIT, https://github.com/BoboTiG/python-mss) — fast, ctypes-based, no GDI
leaks. Adapted from Pack 3's tools/screenshot.py: same mss call, wrapped as a real Tool.

Permission: MEDIUM, not LOW — a screenshot can capture anything currently on screen
(a password field, a private conversation), and unlike the other read-only system
tools (CPU/RAM/disk) it leaves a persistent file behind, so it gets the same
confirmation gate as clipboard reads for the same privacy reason.
"""
from __future__ import annotations

import logging
import tempfile
from datetime import datetime
from pathlib import Path

from security.permissions import PermissionLevel
from tools.base import Tool, ToolResult

logger = logging.getLogger("steve.tools.screenshot")


class ScreenshotTool(Tool):
    name = "screenshot"
    description = (
        "Tira um print da tela principal do usuário e salva como PNG, devolvendo o caminho do arquivo. "
        "Não descreve o conteúdo visual da imagem (exigiria um modelo multimodal)."
    )
    permission_level = PermissionLevel.MEDIUM
    parameters = {"save_dir": "Pasta opcional para salvar (padrão: temp)"}
    parameters_schema = {
        "type": "object",
        "properties": {"save_dir": {"type": "string", "description": "Pasta opcional para salvar (padrão: temp)"}},
        "required": [],
    }

    def validate(self, params: dict) -> tuple[bool, str]:
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        try:
            from mss import mss
            from mss.tools import to_png
        except ImportError:
            return ToolResult(
                success=False, verified=False,
                message="A captura de tela não está disponível.",
                error="pacote 'mss' não instalado (pip install mss)",
            )

        save_dir = params.get("save_dir")
        out = Path(save_dir) if save_dir else Path(tempfile.gettempdir()) / "steve_screenshots"
        try:
            out.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return ToolResult(success=False, verified=False, message="Não consegui criar a pasta de destino.", error=str(exc))

        filepath = out / f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        try:
            with mss() as sct:
                monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                shot = sct.grab(monitor)
                to_png(shot.rgb, shot.size, output=str(filepath))
        except Exception as exc:
            logger.warning("Erro ao capturar screenshot: %s", exc)
            return ToolResult(success=False, verified=False, message="Não consegui capturar a tela.", error=str(exc))

        verified = filepath.exists() and filepath.stat().st_size > 0
        return ToolResult(
            success=True,
            verified=verified,
            message=f"Screenshot salvo em: {filepath}." if verified else "Screenshot gerado, mas não pude confirmar o arquivo.",
            data={"path": str(filepath)},
        )
