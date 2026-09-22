from __future__ import annotations

import pytest

from core.context import ContextManager
from core.session import SessionManager
from core.tool_manager import ToolManager
from memory.database import Database
from memory.service import MemoryService
from security.audit import AuditLogger
from security.confirmations import ConfirmationService
from security.permissions import PermissionLevel, PermissionManager
from settings.config import Settings
from tools.system import CheckCPUTool


class FakeConfirmationService(ConfirmationService):
    def __init__(self, approve: bool = True):
        self.approve = approve
        self.asked: list[str] = []

    def confirm(self, message: str) -> bool:
        self.asked.append(message)
        return self.approve


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "steve.db")
    yield db
    db.close()


@pytest.fixture
def memory_service(database):
    return MemoryService(database)


@pytest.fixture
def settings():
    return Settings(user_name="Junior", first_run_completed=True)


@pytest.fixture
def tool_manager():
    manager = ToolManager()
    manager.register(CheckCPUTool())
    return manager


@pytest.fixture
def context_manager(memory_service):
    return ContextManager(memory_service=memory_service)


@pytest.fixture
def session():
    return SessionManager(session_id="test-session")


@pytest.fixture
def permission_manager():
    return PermissionManager(auto_approve_up_to=PermissionLevel.LOW)


@pytest.fixture
def audit_logger(tmp_path):
    return AuditLogger(tmp_path / "audit.log")
