"""Builds the message list sent to the AI provider from session, memory and settings."""
from __future__ import annotations

from ai.base import AIMessage
from ai.prompts.system_prompt import build_system_prompt
from core.session import SessionManager
from memory.service import MemoryService
from settings.config import Settings


class ContextManager:
    def __init__(self, memory_service: MemoryService):
        self.memory_service = memory_service

    def build(
        self,
        session: SessionManager,
        settings: Settings,
        history_limit: int = 10,
        user_text: str | None = None,
    ) -> list[AIMessage]:
        """`user_text` (the current user message, when known) drives relevance scoring so
        the memories included are the ones that actually matter to this message instead of
        just the most-recently-updated ones — see MemoryService.get_relevant_memories."""
        memories_summary = self.memory_service.summarize_for_prompt(query=user_text)
        system_prompt = build_system_prompt(
            user_name=settings.user_name,
            proactivity=settings.proactivity.value,
            memories_summary=memories_summary or None,
        )
        messages = [AIMessage(role="system", content=system_prompt)]
        for turn in session.recent_turns(limit=history_limit):
            messages.append(AIMessage(role=turn.role, content=turn.content))
        return messages
