from __future__ import annotations

from memory.service import MemoryCategory
from security.permissions import PermissionLevel
from tools.memory import ForgetMemoryTool, RecallMemoriesTool, RememberFactTool


def test_memory_tools_are_low_permission():
    for tool_cls in (RememberFactTool, ForgetMemoryTool, RecallMemoriesTool):
        assert tool_cls.permission_level == PermissionLevel.LOW


def test_remember_fact_tool_creates_permanent_memory_by_default(memory_service):
    tool = RememberFactTool(memory_service=memory_service)

    result = tool.execute({"content": "O projeto principal é o Prime Ges.", "key": "projeto_atual"})

    assert result.success is True
    assert result.data["category"] == MemoryCategory.PERMANENT.value
    stored = memory_service.get_active_by_key("projeto_atual")
    assert stored.content == "O projeto principal é o Prime Ges."


def test_remember_fact_tool_temporary_flag_expires(memory_service):
    tool = RememberFactTool(memory_service=memory_service)

    result = tool.execute({"content": "Hoje trabalha na tela de login.", "key": "tarefa_hoje", "temporary": True})

    assert result.success is True
    stored = memory_service.get_active_by_key("tarefa_hoje")
    assert stored.category == MemoryCategory.TEMPORARY.value
    assert stored.expires_at is not None


def test_remember_fact_tool_same_key_updates_instead_of_duplicating(memory_service):
    tool = RememberFactTool(memory_service=memory_service)
    tool.execute({"content": "Prefere VS Code.", "key": "editor"})
    tool.execute({"content": "Prefere Neovim.", "key": "editor"})

    active = [m for m in memory_service.list_memories() if m.key == "editor"]
    assert len(active) == 1
    assert active[0].content == "Prefere Neovim."


def test_remember_fact_tool_rejects_empty_content(memory_service):
    tool = RememberFactTool(memory_service=memory_service)
    valid, error = tool.validate({"content": "   "})
    assert valid is False
    assert error


def test_remember_fact_tool_rejects_invalid_type(memory_service):
    tool = RememberFactTool(memory_service=memory_service)
    valid, error = tool.validate({"content": "x", "type": "not_a_real_type"})
    assert valid is False
    assert error


def test_forget_memory_tool_by_key(memory_service):
    memory_service.remember("Projeto antigo.", key="projeto_antigo")
    tool = ForgetMemoryTool(memory_service=memory_service)

    result = tool.execute({"key": "projeto_antigo"})

    assert result.success is True
    assert memory_service.get_active_by_key("projeto_antigo") is None


def test_forget_memory_tool_by_key_not_found(memory_service):
    tool = ForgetMemoryTool(memory_service=memory_service)
    result = tool.execute({"key": "nao_existe"})
    assert result.success is False


def test_forget_memory_tool_by_search_text(memory_service):
    memory_service.remember("Trabalha no Prime Ges.", key="a")
    memory_service.remember("Prime Ges usa React.", key="b")
    tool = ForgetMemoryTool(memory_service=memory_service)

    result = tool.execute({"search_text": "Prime Ges"})

    assert result.success is True
    assert result.data["count"] == 2


def test_forget_memory_tool_falls_back_to_search_text_when_key_does_not_match(memory_service):
    """The model doesn't always know the exact key a fact was stored under (session
    history only replays user/assistant text, not raw tool-call arguments) — a wrong
    guess at 'key' shouldn't block a working 'search_text' given alongside it."""
    memory_service.remember("O projeto principal é o Prime Ges.", key="projeto_principal")
    tool = ForgetMemoryTool(memory_service=memory_service)

    result = tool.execute({"key": "chave_errada", "search_text": "Prime Ges"})

    assert result.success is True
    assert memory_service.get_active_by_key("projeto_principal") is None


def test_forget_memory_tool_requires_key_or_search_text(memory_service):
    tool = ForgetMemoryTool(memory_service=memory_service)
    valid, error = tool.validate({})
    assert valid is False
    assert error


def test_recall_memories_tool_returns_relevant_memories(memory_service):
    memory_service.remember("O projeto principal é o Prime Ges.", key="projeto_atual")
    tool = RecallMemoriesTool(memory_service=memory_service)

    result = tool.execute({"query": "projeto"})

    assert result.success is True
    assert len(result.data["memories"]) == 1
    assert "Prime Ges" in result.data["memories"][0]["content"]


def test_recall_memories_tool_with_no_memories_still_succeeds(memory_service):
    tool = RecallMemoriesTool(memory_service=memory_service)
    result = tool.execute({})
    assert result.success is True
    assert result.data["memories"] == []


def test_memory_tools_registered_alongside_other_low_permission_tools(tool_manager, memory_service):
    for tool_cls in (RememberFactTool, ForgetMemoryTool, RecallMemoriesTool):
        tool_manager.register(tool_cls(memory_service=memory_service))

    assert tool_manager.has("remember_fact")
    assert tool_manager.has("forget_memory")
    assert tool_manager.has("recall_memories")
