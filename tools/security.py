"""Read-only Security Center tools for the AI Router's native tool-calling. These only
ever expose evidence the SecurityEngine already gathered — the AI can explain/
contextualize a finding in its reply, but it never creates one and never triggers any
action beyond looking (see security/engine.py's module docstring and the V1.5 spec's
"IA não deve ser a única fonte da detecção").
"""
from __future__ import annotations

from security.engine import SecurityEngine
from security.permissions import PermissionLevel
from tools.base import Tool, ToolResult


class CheckSecurityStatusTool(Tool):
    name = "check_security_status"
    description = "Consulta o estado atual do Security Center (proteção ativa, achados recentes)."
    permission_level = PermissionLevel.LOW
    parameters = {}

    def __init__(self, security_engine: SecurityEngine):
        self._engine = security_engine

    def validate(self, params: dict) -> tuple[bool, str]:
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        findings = self._engine.recent_findings(limit=200)
        counts_by_severity = {}
        for finding in findings:
            counts_by_severity[finding.severity.name] = counts_by_severity.get(finding.severity.name, 0) + 1
        return ToolResult(
            success=True,
            verified=True,
            message=f"Security Center: {self._engine.state.value}. {len(findings)} achado(s) registrado(s).",
            data={
                "state": self._engine.state.value,
                "total_findings": len(findings),
                "findings_by_severity": counts_by_severity,
            },
        )


class ListSecurityFindingsTool(Tool):
    name = "list_security_findings"
    description = "Lista os achados de segurança mais recentes detectados pelo Security Center, com evidências."
    permission_level = PermissionLevel.LOW
    parameters = {"limit": "Número máximo de achados a retornar (padrão 10)"}
    parameters_schema = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Número máximo de achados a retornar (padrão 10)"}
        },
        "required": [],
    }

    def __init__(self, security_engine: SecurityEngine):
        self._engine = security_engine

    def validate(self, params: dict) -> tuple[bool, str]:
        limit = params.get("limit", 10)
        if not isinstance(limit, int) or limit <= 0:
            return False, "Parâmetro 'limit' deve ser um inteiro positivo."
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        valid, error = self.validate(params)
        if not valid:
            return ToolResult(success=False, verified=False, message=error, error=error)
        limit = params.get("limit", 10)
        findings = self._engine.recent_findings(limit=limit)
        return ToolResult(
            success=True,
            verified=True,
            message=f"{len(findings)} achado(s) de segurança encontrados.",
            data={
                "findings": [
                    {
                        "id": f.id,
                        "severity": f.severity.name,
                        "category": f.category.value,
                        "title": f.title,
                        "description": f.description,
                        "evidence": list(f.evidence),
                        "confidence": f.confidence,
                        "process_name": f.process_name,
                        "pid": f.pid,
                        "status": f.status.value,
                    }
                    for f in findings
                ]
            },
        )


class ScanFileTool(Tool):
    """The only Security Center tool that DOES something beyond reading state — it
    reads and hashes a file (never executes it, see security/file_analysis.py) at the
    user's explicit request via a voice/chat command ("Steve, verifique esse
    arquivo."), matching the V1.5 spec's section 20. Still LOW permission: reading and
    hashing a file the user already named is not a sensitive action by this project's
    existing permission model (compare to OpenFileTool, also LOW)."""

    name = "scan_file"
    description = "Analisa um arquivo específico em busca de sinais de risco (não executa o arquivo)."
    permission_level = PermissionLevel.LOW
    parameters = {"path": "Caminho do arquivo a analisar"}
    parameters_schema = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Caminho do arquivo a analisar"}},
        "required": ["path"],
    }

    def __init__(self, security_engine: SecurityEngine):
        self._engine = security_engine

    def validate(self, params: dict) -> tuple[bool, str]:
        if not params.get("path"):
            return False, "Parâmetro 'path' é obrigatório."
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        valid, error = self.validate(params)
        if not valid:
            return ToolResult(success=False, verified=False, message=error, error=error)
        summary = self._engine.scanner.scan_file_path(params["path"])
        if summary.error:
            return ToolResult(success=False, verified=False, message=summary.error, error=summary.error)
        if summary.findings:
            finding = summary.findings[0]
            return ToolResult(
                success=True,
                verified=True,
                message=f"Arquivo suspeito: {finding.title}. Motivos: {'; '.join(finding.evidence)}.",
                data={"status": "suspicious", "evidence": list(finding.evidence)},
            )
        return ToolResult(
            success=True,
            verified=True,
            message="Nenhum indicador de risco encontrado neste arquivo (não é uma garantia absoluta).",
            data={"status": "safe"},
        )
