"""Memory tools — how the model actually saves/recalls/forgets facts.

Deliberately implemented as native tools (like check_cpu, open_application, ...)
instead of a regex/keyword command parser: the model already decides when a tool
call is warranted via TOOL_POLICY/MEMORY_POLICY in the system prompt, so "lembre
que...", "guarde isso...", "esqueça X" and their many phrasings all flow through
the same battle-tested tool-calling loop instead of a second, fragile text-matching
mechanism that would need to keep up with every way a user might phrase a request.
"""
from __future__ import annotations

from memory.service import MemoryCategory, MemoryService, MemorySource, MemoryType
from security.permissions import PermissionLevel
from tools.base import Tool, ToolResult

_VALID_TYPES = [t.value for t in MemoryType]
_VALID_CATEGORIES = [c.value for c in MemoryCategory if c != MemoryCategory.IDENTITY]


class RememberFactTool(Tool):
    name = "remember_fact"
    description = (
        "Salva ou atualiza um fato permanente ou temporário sobre o usuário (preferência, "
        "projeto, objetivo, hábito ou fato). Use quando o usuário pedir explicitamente para "
        "lembrar/guardar algo, ou quando ele compartilhar uma informação claramente "
        "duradoura e relevante para o futuro (ex.: 'meu projeto principal é X'). NÃO use "
        "para comandos comuns, perguntas do dia a dia ou conversa casual sem valor futuro."
    )
    permission_level = PermissionLevel.LOW
    parameters = {
        "content": (
            "A frase COMPLETA do fato, sempre incluindo o valor específico mencionado pelo "
            "usuário — nunca só a categoria da informação. Ex.: se o usuário disse 'meu "
            "projeto principal é o Prime Ges', content deve ser 'O projeto principal do "
            "usuário é o Prime Ges' (não apenas 'projeto principal'). Outro exemplo: "
            "'Prefere respostas curtas e diretas' (não apenas 'preferência de resposta')."
        ),
        "type": f"Tipo do fato: {', '.join(_VALID_TYPES)}. Padrão: fact.",
        "key": (
            "Chave curta e estável que identifica ESTE fato (ex.: 'editor_preferido'). "
            "Se um fato com a mesma chave já existir, ele é atualizado em vez de duplicado — "
            "use isso sempre que o novo fato substitui/corrige um anterior."
        ),
        "temporary": "true se a informação vale só para agora/sessão atual (ex.: 'hoje estou em X'); false se é duradoura.",
    }
    parameters_schema = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": parameters["content"]},
            "type": {"type": "string", "enum": _VALID_TYPES, "description": parameters["type"]},
            "key": {"type": "string", "description": parameters["key"]},
            "temporary": {"type": "boolean", "description": parameters["temporary"]},
        },
        "required": ["content"],
    }

    def __init__(self, memory_service: MemoryService):
        self.memory_service = memory_service

    def validate(self, params: dict) -> tuple[bool, str]:
        content = params.get("content")
        if not content or not str(content).strip():
            return False, "O parâmetro 'content' é obrigatório e não pode ser vazio."
        memory_type = params.get("type", MemoryType.FACT.value)
        if memory_type not in _VALID_TYPES:
            return False, f"Tipo inválido: {memory_type}. Use um de: {', '.join(_VALID_TYPES)}."
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        content = str(params["content"]).strip()
        memory_type = MemoryType(params.get("type", MemoryType.FACT.value))
        key = params.get("key")
        temporary = bool(params.get("temporary", False))

        category = MemoryCategory.TEMPORARY if temporary else MemoryCategory.PERMANENT
        expires_in_hours = 24.0 if temporary else None

        record = self.memory_service.remember(
            content=content,
            type=memory_type,
            category=category,
            key=key,
            source=MemorySource.EXPLICIT_COMMAND,
            expires_in_hours=expires_in_hours,
        )
        return ToolResult(
            success=True,
            verified=True,
            message=f"Lembrado: {content}",
            data={"id": record.id, "category": record.category, "key": record.key},
        )


class ForgetMemoryTool(Tool):
    name = "forget_memory"
    description = (
        "Apaga (esquece) uma ou mais memórias salvas anteriormente. Use quando o usuário "
        "pedir para esquecer algo específico ('esqueça o que sabe sobre X') ou corrigir uma "
        "informação removendo a antiga por chave."
    )
    permission_level = PermissionLevel.LOW
    parameters = {
        "key": "Chave exata de uma memória específica a esquecer (uso preferencial quando conhecida).",
        "search_text": "Texto para localizar memórias a esquecer por conteúdo (ex.: 'projeto antigo'). Apaga todas as que combinarem.",
    }
    parameters_schema = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": parameters["key"]},
            "search_text": {"type": "string", "description": parameters["search_text"]},
        },
        "required": [],
    }

    def __init__(self, memory_service: MemoryService):
        self.memory_service = memory_service

    def validate(self, params: dict) -> tuple[bool, str]:
        if not params.get("key") and not params.get("search_text"):
            return False, "Informe 'key' ou 'search_text' para saber o que esquecer."
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        key = params.get("key")
        search_text = params.get("search_text")

        if key and self.memory_service.forget_by_key(key):
            return ToolResult(success=True, verified=True, message=f"Esqueci a memória '{key}'.", data={"key": key})

        # The model doesn't always know the exact key a fact was stored under (it isn't
        # replayed in later conversation turns — see ContextManager). If 'key' didn't match
        # anything but 'search_text' was also given, fall back to a content search instead
        # of reporting failure when a broader match is right there.
        if search_text:
            count = self.memory_service.forget_matching(search_text)
            if count:
                return ToolResult(success=True, verified=True, message=f"Esqueci {count} memória(s) relacionada(s) a '{search_text}'.", data={"count": count})
            return ToolResult(success=False, verified=True, message=f"Não encontrei memórias relacionadas a '{search_text}'.")

        return ToolResult(success=False, verified=True, message=f"Não encontrei memória ativa com a chave '{key}'.")


class RecallMemoriesTool(Tool):
    name = "recall_memories"
    description = (
        "Consulta o que Steve sabe/lembra sobre o usuário ou um assunto específico. Use "
        "quando o usuário perguntar 'o que você lembra sobre mim?', 'você lembra de X?' ou "
        "similar."
    )
    permission_level = PermissionLevel.LOW
    parameters = {
        "query": "Assunto a buscar (opcional). Sem query, retorna as memórias mais relevantes/recentes.",
    }
    parameters_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": parameters["query"]}},
        "required": [],
    }

    def __init__(self, memory_service: MemoryService):
        self.memory_service = memory_service

    def validate(self, params: dict) -> tuple[bool, str]:
        return True, ""

    def execute(self, params: dict) -> ToolResult:
        query = params.get("query")
        memories = self.memory_service.get_relevant_memories(query=query, limit=10)
        if not memories:
            return ToolResult(success=True, verified=True, message="Não tenho nenhuma memória salva sobre isso.", data={"memories": []})
        summary = "\n".join(f"- {m.content}" for m in memories)
        return ToolResult(
            success=True,
            verified=True,
            message=summary,
            data={"memories": [{"id": m.id, "content": m.content, "category": m.category, "key": m.key} for m in memories]},
        )
