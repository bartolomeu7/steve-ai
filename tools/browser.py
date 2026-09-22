"""Open URLs and play music via Steve's internal player (WebView2)."""
from __future__ import annotations

import logging
import re
import webbrowser
from urllib.parse import quote_plus

from core.events import EventBus
from security.permissions import PermissionLevel
from tools.base import Tool, ToolResult
from tools.youtube_resolve import resolve_first_youtube_video

logger = logging.getLogger("steve.tools.browser")

_URL_PATTERN = re.compile(r"^https?://[^\s]+$")
MEDIA_PLAY_EVENT = "media.play"


class OpenURLTool(Tool):
    name = "open_url"
    description = (
        "Abre uma URL no navegador padrao do sistema. "
        "Para tocar musica/artista, prefira a tool play_music (player interno do Steve)."
    )
    permission_level = PermissionLevel.LOW
    parameters = {"url": "Endereco a abrir (deve comecar com http:// ou https://)"}
    parameters_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Endereco a abrir (deve comecar com http:// ou https://)",
            }
        },
        "required": ["url"],
    }

    def validate(self, params: dict) -> tuple[bool, str]:
        url = params.get("url")
        if not url:
            return False, "Parametro 'url' e obrigatorio."
        if not _URL_PATTERN.match(url):
            return False, "URL invalida: deve comecar com http:// ou https://."
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        valid, error = self.validate(params)
        if not valid:
            return ToolResult(success=False, verified=False, message=error, error=error)
        url = params["url"]
        opened = webbrowser.open(url)
        return ToolResult(
            success=bool(opened),
            verified=bool(opened),
            message=f"URL aberta: {url}." if opened else f"Nao foi possivel abrir a URL: {url}.",
            data={"url": url},
        )


class PlayMusicTool(Tool):
    """Resolve YouTube + publish media.play for Steve's internal WebView2 player."""

    name = "play_music"
    description = (
        "Toca musica no player interno do Steve (janela filha com YouTube). "
        "Use quando o usuario pedir para tocar, ouvir ou colocar uma musica, artista ou playlist. "
        "Passe a query exatamente como o usuario pediu (ex.: 'Hungria Hip Hop'), sem trocar o artista. "
        "NAO use open_url para musica."
    )
    permission_level = PermissionLevel.LOW
    parameters = {"query": "Nome do artista, musica ou playlist"}
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Nome do artista, musica ou playlist (ex.: Hungria Hip Hop)",
            }
        },
        "required": ["query"],
    }

    def __init__(self, event_bus: EventBus | None = None):
        self.event_bus = event_bus

    def validate(self, params: dict) -> tuple[bool, str]:
        query = (params.get("query") or "").strip()
        if not query:
            return False, "Parametro 'query' e obrigatorio."
        if len(query) > 200:
            return False, "Query muito longa (max. 200 caracteres)."
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        valid, error = self.validate(params)
        if not valid:
            return ToolResult(success=False, verified=False, message=error, error=error)
        query = params["query"].strip()
        try:
            hit = resolve_first_youtube_video(query)
        except Exception as exc:
            logger.exception("Falha ao resolver YouTube para %r", query)
            return ToolResult(
                success=False,
                verified=False,
                message=f"Nao encontrei um video para: {query}.",
                error=str(exc),
            )

        payload = {
            "query": query,
            "video_id": hit["video_id"],
            "title": hit["title"],
            "embed_url": hit["embed_url"],
            "watch_url": hit["watch_url"],
        }

        if self.event_bus is not None:
            self.event_bus.publish(MEDIA_PLAY_EVENT, payload)
            return ToolResult(
                success=True,
                verified=True,
                message=f"Tocando no player interno: {hit['title']}.",
                data=payload,
            )

        # CLI / sem GUI: fallback externo
        opened = webbrowser.open(hit["watch_url"])
        return ToolResult(
            success=bool(opened),
            verified=bool(opened),
            message=(
                f"GUI indisponivel — abri no navegador: {hit['title']}."
                if opened
                else f"Nao foi possivel abrir: {hit['title']}."
            ),
            data=payload,
        )


class StopMusicTool(Tool):
    """Publish media.stop so the UI closes the internal player."""

    name = "stop_music"
    description = "Para a musica / fecha o player interno do Steve."
    permission_level = PermissionLevel.LOW
    parameters = {}
    parameters_schema = {"type": "object", "properties": {}, "required": []}

    def __init__(self, event_bus=None):
        self._event_bus = event_bus

    def validate(self, params: dict) -> tuple[bool, str]:
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        if self._event_bus is not None:
            try:
                self._event_bus.publish("media.stop", {})
            except Exception as exc:
                return ToolResult(success=False, verified=False, message="Falha ao sinalizar stop.", error=str(exc))
        return ToolResult(success=True, verified=True, message="Pedi para parar o player interno.", data={})
