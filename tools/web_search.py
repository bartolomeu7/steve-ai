"""Web search — real, structured results handed back to the model, never a fabricated
"I searched" with nothing behind it.

Base: ddgs (MIT, https://pypi.org/project/ddgs/) — DuckDuckGo/multi-backend metasearch,
no API key required. Adapted from Pack 3's tools/web_search.py: the original returned a
single pre-formatted string via a standalone TOOL_SCHEMA+run() pair (its own registry,
parallel to ToolManager) — rewritten here as a real Tool so results flow through the
same ToolCall -> ToolManager -> ToolResult -> model loop every other tool uses, with
`data["results"]` as a real structured list the model can reason over, not just prose.
"""
from __future__ import annotations

import logging

from security.permissions import PermissionLevel
from tools.base import Tool, ToolResult

logger = logging.getLogger("steve.tools.web_search")

MAX_RESULTS_LIMIT = 10


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Pesquisa na internet (DuckDuckGo, sem API key) e retorna resultados estruturados "
        "(título, link e trecho de cada um). Use para fatos atuais, notícias, definições ou "
        "qualquer informação que você não tenha certeza — nunca invente que pesquisou algo."
    )
    permission_level = PermissionLevel.LOW
    parameters = {"query": "Termo ou pergunta a pesquisar", "max_results": "Quantidade de resultados (padrão 5, máx. 10)"}
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Termo ou pergunta a pesquisar"},
            "max_results": {"type": "integer", "description": "Quantidade de resultados (padrão 5, máx. 10)"},
        },
        "required": ["query"],
    }

    def validate(self, params: dict) -> tuple[bool, str]:
        query = params.get("query")
        if not query or not str(query).strip():
            return False, "Parâmetro 'query' é obrigatório."
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        valid, error = self.validate(params)
        if not valid:
            return ToolResult(success=False, verified=False, message=error, error=error)

        query = str(params["query"]).strip()
        max_results = min(MAX_RESULTS_LIMIT, max(1, int(params.get("max_results") or 5)))

        try:
            from ddgs import DDGS
        except ImportError:
            try:
                from duckduckgo_search import DDGS  # fallback if ddgs isn't installed under its new name
            except ImportError:
                return ToolResult(
                    success=False,
                    verified=False,
                    message="A pesquisa na web não está disponível.",
                    error="pacote 'ddgs' não instalado (pip install ddgs)",
                )

        try:
            with DDGS() as ddgs:
                raw_results = list(ddgs.text(query, max_results=max_results))
        except Exception as exc:
            logger.warning("Falha na pesquisa web para %r: %s", query, exc)
            return ToolResult(
                success=False,
                verified=False,
                message="Não consegui pesquisar agora — a busca falhou.",
                error=str(exc),
            )

        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("href") or r.get("link", ""),
                "snippet": r.get("body") or r.get("snippet", ""),
            }
            for r in raw_results
        ]

        if not results:
            return ToolResult(
                success=True,
                verified=True,
                message=f"Nenhum resultado encontrado para «{query}».",
                data={"query": query, "results": []},
            )

        return ToolResult(
            success=True,
            verified=True,
            message=f"{len(results)} resultado(s) encontrado(s) para «{query}».",
            data={"query": query, "results": results},
        )
