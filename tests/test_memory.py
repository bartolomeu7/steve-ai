from __future__ import annotations

from datetime import datetime, timedelta, timezone

from memory.service import MemoryCategory, MemorySource, MemoryType


def test_create_and_get_memory(memory_service):
    memory_id = memory_service.create_memory(MemoryType.PREFERENCE, "Prefere respostas curtas.")
    memory = memory_service.get_memory(memory_id)

    assert memory is not None
    assert memory.content == "Prefere respostas curtas."
    assert memory.important is False


def test_update_and_mark_important(memory_service):
    memory_id = memory_service.create_memory(MemoryType.GOAL, "Terminar o projeto Steve.")
    memory_service.mark_important(memory_id, True)
    memory_service.update_memory(memory_id, content="Terminar o projeto Steve este mês.")

    memory = memory_service.get_memory(memory_id)
    assert memory.important is True
    assert memory.content == "Terminar o projeto Steve este mês."


def test_forget_deletes_memory(memory_service):
    memory_id = memory_service.create_memory(MemoryType.FACT, "Usa Windows 10.")
    assert memory_service.forget(memory_id) is True
    assert memory_service.get_memory(memory_id) is None


def test_search_memories(memory_service):
    memory_service.create_memory(MemoryType.HABIT, "Trabalha melhor de manhã.")
    memory_service.create_memory(MemoryType.HABIT, "Gosta de café à tarde.")

    results = memory_service.search_memories("manhã")
    assert len(results) == 1
    assert "manhã" in results[0].content


def test_summarize_for_prompt_prioritizes_important(memory_service):
    memory_service.create_memory(MemoryType.FACT, "Memória comum.")
    memory_service.create_memory(MemoryType.FACT, "Memória importante.", important=True)

    summary = memory_service.summarize_for_prompt()
    lines = summary.splitlines()
    assert "Memória importante." in lines[0]


def test_conversation_history_round_trip(memory_service):
    memory_service.add_history("session-1", "user", "Olá, Steve.")
    memory_service.add_history("session-1", "assistant", "Olá! Como posso ajudar?")

    history = memory_service.get_recent_history("session-1")
    assert [h.role for h in history] == ["user", "assistant"]


# --- V1.2: memory/identity/context architecture --------------------------------------


def test_remember_creates_permanent_memory_by_default(memory_service):
    record = memory_service.remember("Prefere respostas curtas e diretas.", key="estilo_resposta")

    assert record.category == MemoryCategory.PERMANENT.value
    assert record.key == "estilo_resposta"
    assert record.active is True
    assert record.expires_at is None


def test_remember_with_expiry_creates_temporary_memory(memory_service):
    record = memory_service.remember(
        "Hoje está trabalhando na tela de login.",
        category=MemoryCategory.TEMPORARY,
        key="tarefa_atual",
        expires_in_hours=24,
    )

    assert record.category == MemoryCategory.TEMPORARY.value
    assert record.expires_at is not None
    assert record.is_expired() is False


def test_expired_memory_is_excluded_from_active_queries(memory_service):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    memory_id = memory_service.create_memory(
        MemoryType.FACT, "Informação vencida.", category=MemoryCategory.TEMPORARY, expires_at=past
    )

    assert memory_service.get_memory(memory_id).is_expired() is True
    assert memory_service.get_memory(memory_id) not in memory_service.list_memories()
    assert memory_service.get_relevant_memories() == []


def test_purge_expired_hard_deletes_only_expired_rows(memory_service):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    expired_id = memory_service.create_memory(MemoryType.FACT, "Vencida.", expires_at=past)
    live_id = memory_service.create_memory(MemoryType.FACT, "Ainda válida.", expires_at=future)

    purged = memory_service.purge_expired()

    assert purged == 1
    assert memory_service.get_memory(expired_id) is None
    assert memory_service.get_memory(live_id) is not None


def test_remember_with_same_key_supersedes_previous_instead_of_duplicating(memory_service):
    """'usuário prefere X' depois 'na verdade agora prefiro Y' -> uma correção, não duas
    memórias conflitantes ativas ao mesmo tempo."""
    memory_service.remember("Prefere o editor VS Code.", key="editor_preferido")
    updated = memory_service.remember("Prefere o editor Neovim.", key="editor_preferido")

    active = memory_service.list_memories()
    matching = [m for m in active if m.key == "editor_preferido"]

    assert len(matching) == 1
    assert matching[0].content == "Prefere o editor Neovim."
    assert matching[0].id == updated.id

    # the superseded row is still there (soft-deleted) for audit, just not active.
    all_rows = memory_service.list_memories(active_only=False)
    assert len([m for m in all_rows if m.key == "editor_preferido"]) == 2


def test_forget_by_key_soft_deletes_active_memory(memory_service):
    memory_service.remember("Projeto atual é o Steve.", key="projeto_atual")

    assert memory_service.forget_by_key("projeto_atual") is True
    assert memory_service.get_active_by_key("projeto_atual") is None
    # still present, just inactive — not a hard delete.
    assert any(m.key == "projeto_atual" for m in memory_service.list_memories(active_only=False))


def test_forget_by_key_returns_false_when_nothing_active(memory_service):
    assert memory_service.forget_by_key("chave_inexistente") is False


def test_forget_matching_removes_every_memory_containing_text(memory_service):
    memory_service.remember("Trabalha no Prime Ges.", key="projeto_a")
    memory_service.remember("Prime Ges usa React.", key="projeto_a_detalhe")
    memory_service.remember("Gosta de café.", key="habito_cafe")

    count = memory_service.forget_matching("Prime Ges")

    assert count == 2
    assert memory_service.get_active_by_key("habito_cafe") is not None
    assert memory_service.get_active_by_key("projeto_a") is None


def test_temporary_memory_is_kept_separate_from_permanent(memory_service):
    memory_service.remember("Fato permanente.", category=MemoryCategory.PERMANENT, key="permanente")
    memory_service.remember("Fato temporário de hoje.", category=MemoryCategory.TEMPORARY, key="temp", expires_in_hours=1)

    permanent_only = memory_service.list_memories(category=MemoryCategory.PERMANENT)
    temporary_only = memory_service.list_memories(category=MemoryCategory.TEMPORARY)

    assert [m.key for m in permanent_only] == ["permanente"]
    assert [m.key for m in temporary_only] == ["temp"]


def test_get_relevant_memories_scores_by_word_overlap_with_query(memory_service):
    memory_service.remember("Prefere café pela manhã.", key="habito_cafe")
    memory_service.remember("O projeto principal é o Prime Ges.", key="projeto_atual")

    results = memory_service.get_relevant_memories(query="Qual é o meu projeto principal?")

    assert results[0].key == "projeto_atual"


def test_get_relevant_memories_excludes_an_old_unimportant_unrelated_memory(memory_service):
    """Pack 2 (Smart Recall): unlike the plain word-overlap ranking this replaced,
    get_relevant_memories can now exclude a candidate outright (SmartRecall.min_score),
    not just rank it last — but only once it's both old (recency decays to ~0) AND
    not marked important; a fresh memory still clears the threshold on recency alone
    regardless of relevance, see memory/service.py::_to_memory_item's docstring."""
    memory_service.remember("O projeto principal é o Prime Ges.", key="projeto_atual")
    old_id = memory_service.create_memory(MemoryType.FACT, "Prefere pizza de calabresa aos sábados.")
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    memory_service.db.execute("UPDATE memories SET updated_at = ? WHERE id = ?", (old_timestamp, old_id))

    results = memory_service.get_relevant_memories(query="Qual é o meu projeto principal?")

    contents = [r.content for r in results]
    assert "O projeto principal é o Prime Ges." in contents
    assert "Prefere pizza de calabresa aos sábados." not in contents


def test_get_relevant_memories_keeps_a_fresh_unrelated_memory_despite_no_overlap(memory_service):
    """The other half of the same behavior: min_score is not a relevance gate on its
    own — a just-created memory clears it on recency alone even with zero term
    overlap, so a brand-new fact isn't silently dropped the first time it's not what
    the user happens to be asking about right now."""
    memory_service.remember("O projeto principal é o Prime Ges.", key="projeto_atual")
    memory_service.create_memory(MemoryType.FACT, "Prefere pizza de calabresa aos sábados.")

    results = memory_service.get_relevant_memories(query="Qual é o meu projeto principal?")

    contents = [r.content for r in results]
    assert "Prefere pizza de calabresa aos sábados." in contents


def test_get_relevant_memories_without_query_prioritizes_important_and_recent(memory_service):
    memory_service.create_memory(MemoryType.FACT, "Memória comum antiga.")
    memory_service.create_memory(MemoryType.FACT, "Memória importante.", important=True)

    results = memory_service.get_relevant_memories()

    assert results[0].content == "Memória importante."


def test_summarize_for_prompt_truncates_long_content(memory_service):
    long_content = "x" * 500
    memory_service.create_memory(MemoryType.FACT, long_content)

    summary = memory_service.summarize_for_prompt()

    assert len(summary) < 500
    assert summary.endswith("...")


def test_create_memory_records_source_and_confidence(memory_service):
    memory_id = memory_service.create_memory(
        MemoryType.FACT, "Inferido pelo modelo.", source=MemorySource.INFERRED, confidence=0.6
    )
    memory = memory_service.get_memory(memory_id)

    assert memory.source == MemorySource.INFERRED.value
    assert memory.confidence == 0.6


def test_memory_persists_across_database_reconnect(tmp_path):
    """Simulates an app restart: closes the DB connection and reopens the same file,
    persistent memories must still be there."""
    from memory.database import Database
    from memory.service import MemoryService

    db_path = tmp_path / "steve.db"
    db = Database(db_path)
    service = MemoryService(db)
    service.remember("Projeto principal é o Prime Ges.", key="projeto_atual")
    db.close()

    reopened = Database(db_path)
    reloaded_service = MemoryService(reopened)

    record = reloaded_service.get_active_by_key("projeto_atual")
    assert record is not None
    assert record.content == "Projeto principal é o Prime Ges."
    reopened.close()
