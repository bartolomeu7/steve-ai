from __future__ import annotations

from ai.prompts.system_prompt import CONVERSATION_POLICY, IDENTITY, MEMORY_POLICY, TOOL_POLICY
from memory.service import MemoryCategory, MemoryType


def test_build_includes_system_identity_even_with_no_memories(context_manager, session, settings):
    messages = context_manager.build(session, settings)

    assert messages[0].role == "system"
    assert IDENTITY in messages[0].content
    assert TOOL_POLICY in messages[0].content
    assert MEMORY_POLICY in messages[0].content
    assert CONVERSATION_POLICY in messages[0].content


def test_build_includes_recent_conversation_turns(context_manager, session, settings):
    session.add_turn("user", "Oi, Steve.")
    session.add_turn("assistant", "Olá! Como posso ajudar?")

    messages = context_manager.build(session, settings)

    assert [m.role for m in messages[1:]] == ["user", "assistant"]
    assert messages[1].content == "Oi, Steve."


def test_build_respects_history_limit(context_manager, session, settings):
    for i in range(20):
        session.add_turn("user", f"mensagem {i}")

    messages = context_manager.build(session, settings, history_limit=3)

    assert len(messages) == 1 + 3  # system + last 3 turns


def test_build_includes_relevant_memories_in_system_prompt(context_manager, session, settings, memory_service):
    memory_service.remember("O projeto principal é o Prime Ges.", key="projeto_atual")

    messages = context_manager.build(session, settings)

    assert "Prime Ges" in messages[0].content


def test_build_prioritizes_memories_relevant_to_current_user_text(context_manager, session, settings, memory_service):
    memory_service.remember("Prefere café pela manhã.", key="habito_cafe")
    memory_service.remember("O projeto principal é o Prime Ges.", key="projeto_atual")

    messages = context_manager.build(session, settings, user_text="Qual é o meu projeto principal?")

    system_content = messages[0].content
    assert system_content.index("Prime Ges") < system_content.index("café")


def test_build_does_not_dump_every_memory_into_the_prompt(context_manager, session, settings, memory_service):
    for i in range(30):
        memory_service.create_memory(MemoryType.FACT, f"Fato número {i} sobre o usuário.")

    messages = context_manager.build(session, settings)

    memory_lines = [line for line in messages[0].content.splitlines() if line.startswith("- (")]
    assert len(memory_lines) <= 8


def test_build_excludes_temporary_memories_after_expiry(context_manager, session, settings, memory_service):
    memory_service.remember(
        "Informação de hoje.", category=MemoryCategory.TEMPORARY, key="hoje", expires_in_hours=-1
    )

    messages = context_manager.build(session, settings)

    assert "Informação de hoje" not in messages[0].content


def test_build_still_returns_plain_ai_message_list_for_native_tool_calling(context_manager, session, settings):
    """Context must stay a list[AIMessage] the AIProvider.chat(messages, tools=...)
    contract expects — no JSON-in-prompt protocol reintroduced."""
    from ai.base import AIMessage

    messages = context_manager.build(session, settings)

    assert all(isinstance(m, AIMessage) for m in messages)
    assert all(m.tool_calls is None for m in messages)
