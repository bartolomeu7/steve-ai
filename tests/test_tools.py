from __future__ import annotations

from core.tool_manager import ToolManager, UnknownToolError
from security.permissions import PermissionLevel
from tools.applications import ListProcessesTool, OpenApplicationTool
from tools.browser import OpenURLTool
from tools.filesystem import CreateDirectoryTool, ListDirectoryTool, OpenFileTool
from tools.system import CheckCPUTool, CheckDiskTool, CheckRAMTool


def _process_name_sequence(before: set[str], after: set[str]):
    calls = iter([before, after])
    return lambda: next(calls)


def test_check_cpu_tool_returns_verified_result():
    result = CheckCPUTool().execute({})
    assert result.success is True
    assert result.verified is True
    assert "cpu_percent" in result.data


def test_check_ram_tool_returns_verified_result():
    result = CheckRAMTool().execute({})
    assert result.success is True
    assert result.verified is True


def test_check_disk_tool_defaults_to_c_drive():
    result = CheckDiskTool().execute({})
    assert result.success is True
    assert result.data["path"] == "C:\\"


def test_list_directory_tool(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()

    result = ListDirectoryTool().execute({"path": str(tmp_path)})

    assert result.success is True
    assert "a.txt" in result.data["entries"]
    assert "sub/" in result.data["entries"]


def test_list_directory_tool_rejects_missing_path(tmp_path):
    result = ListDirectoryTool().execute({"path": str(tmp_path / "missing")})
    assert result.success is False


def test_create_directory_tool_creates_and_verifies(tmp_path):
    target = tmp_path / "novo_projeto"
    result = CreateDirectoryTool().execute({"path": str(target)})

    assert result.success is True
    assert result.verified is True
    assert target.exists()


def test_open_application_tool_launches_and_verifies(monkeypatch):
    captured = {}

    monkeypatch.setattr("tools.applications.sys.platform", "win32")
    monkeypatch.setattr(
        "tools.applications.os.startfile", lambda cmd: captured.setdefault("command", cmd), raising=False
    )
    monkeypatch.setattr("tools.applications.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        OpenApplicationTool,
        "_running_process_names",
        staticmethod(_process_name_sequence(set(), {"notepad.exe"})),
    )

    result = OpenApplicationTool().execute({"app_name": "notepad"})

    assert result.success is True
    assert result.verified is True
    assert captured["command"] == "notepad"


def test_open_application_tool_reports_unverified_when_no_new_process(monkeypatch):
    monkeypatch.setattr("tools.applications.sys.platform", "win32")
    monkeypatch.setattr("tools.applications.os.startfile", lambda cmd: None, raising=False)
    monkeypatch.setattr("tools.applications.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        OpenApplicationTool,
        "_running_process_names",
        staticmethod(_process_name_sequence({"a.exe"}, {"a.exe"})),
    )

    result = OpenApplicationTool().execute({"app_name": "notepad"})

    assert result.success is True
    assert result.verified is False


def test_open_application_tool_rejects_unsafe_names():
    result = OpenApplicationTool().execute({"app_name": "app && rm -rf /"})
    assert result.success is False


def test_open_file_tool_uses_start_file(monkeypatch, tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("olá")
    called = {}

    monkeypatch.setattr("tools.filesystem.os.startfile", lambda path: called.setdefault("path", path))

    result = OpenFileTool().execute({"path": str(target)})

    assert result.success is True
    assert called["path"] == str(target)


def test_open_url_tool_validates_scheme():
    result = OpenURLTool().execute({"url": "not-a-url"})
    assert result.success is False


def test_open_url_tool_opens_valid_url(monkeypatch):
    monkeypatch.setattr("tools.browser.webbrowser.open", lambda url: True)
    result = OpenURLTool().execute({"url": "https://example.com"})
    assert result.success is True


def test_list_processes_tool_respects_limit():
    result = ListProcessesTool().execute({"limit": 3})
    assert result.success is True
    assert len(result.data["processes"]) <= 3


def test_tool_manager_registers_and_executes():
    manager = ToolManager()
    manager.register(CheckCPUTool())

    assert manager.has("check_cpu") is True
    result = manager.execute("check_cpu", {})
    assert result.success is True


def test_tool_manager_raises_for_unknown_tool():
    manager = ToolManager()
    try:
        manager.get("does_not_exist")
        assert False, "should have raised"
    except UnknownToolError:
        pass


def test_all_v1_tools_are_low_permission():
    for tool_cls in (
        OpenApplicationTool,
        ListProcessesTool,
        CheckCPUTool,
        CheckRAMTool,
        CheckDiskTool,
        ListDirectoryTool,
        CreateDirectoryTool,
        OpenFileTool,
        OpenURLTool,
    ):
        assert tool_cls().permission_level == PermissionLevel.LOW
