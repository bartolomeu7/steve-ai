"""Tests for the Pack 3 desktop tools: web_search, active_window, clipboard, volume,
screenshot — each adapted from the Pack 3 zip into a real tools.base.Tool (not the
package's own standalone TOOL_SCHEMA+run() pair)."""
from __future__ import annotations

from security.permissions import PermissionLevel
from tools.active_window import ActiveWindowTool
from tools.clipboard import ClipboardTool
from tools.filesystem import OpenFileTool
from tools.screenshot import ScreenshotTool
from tools.volume import VolumeTool
from tools.web_search import WebSearchTool

# --- web_search ------------------------------------------------------------------


def test_web_search_requires_a_query():
    result = WebSearchTool().execute({"query": "   "})
    assert result.success is False


def test_web_search_returns_structured_results(monkeypatch):
    class _FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, max_results):
            return [
                {"title": "RAG explained", "href": "https://example.com/rag", "body": "Retrieval-augmented generation..."},
                {"title": "RAG paper", "href": "https://example.com/paper", "body": "Original RAG paper..."},
            ]

    fake_module = type("FakeDDGSModule", (), {"DDGS": _FakeDDGS})
    import sys

    monkeypatch.setitem(sys.modules, "ddgs", fake_module)

    result = WebSearchTool().execute({"query": "o que é RAG"})

    assert result.success is True
    assert result.verified is True
    assert len(result.data["results"]) == 2
    assert result.data["results"][0]["title"] == "RAG explained"
    assert result.data["results"][0]["url"] == "https://example.com/rag"


def test_web_search_reports_failure_not_a_fabricated_success(monkeypatch):
    """The model must never be told a search succeeded when it didn't — ToolResult
    must come back success=False with a real error, per the Pack 3 spec's explicit
    requirement."""
    import sys

    class _FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, max_results):
            raise ConnectionError("rede indisponível")

    fake_module = type("FakeDDGSModule", (), {"DDGS": _FakeDDGS})
    monkeypatch.setitem(sys.modules, "ddgs", fake_module)

    result = WebSearchTool().execute({"query": "algo"})

    assert result.success is False
    assert result.error is not None


def test_web_search_handles_zero_results_as_success_with_empty_list(monkeypatch):
    import sys

    class _FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, max_results):
            return []

    fake_module = type("FakeDDGSModule", (), {"DDGS": _FakeDDGS})
    monkeypatch.setitem(sys.modules, "ddgs", fake_module)

    result = WebSearchTool().execute({"query": "asdkjaslkdjaslkdj"})

    assert result.success is True
    assert result.data["results"] == []


def test_web_search_caps_max_results(monkeypatch):
    import sys

    captured = {}

    class _FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, max_results):
            captured["max_results"] = max_results
            return []

    fake_module = type("FakeDDGSModule", (), {"DDGS": _FakeDDGS})
    monkeypatch.setitem(sys.modules, "ddgs", fake_module)

    WebSearchTool().execute({"query": "x", "max_results": 999})

    assert captured["max_results"] == 10  # MAX_RESULTS_LIMIT


def test_web_search_is_low_permission_and_registered_via_tool_manager():
    from core.tool_manager import ToolManager

    manager = ToolManager()
    manager.register(WebSearchTool())
    assert manager.has("web_search") is True
    assert WebSearchTool().permission_level == PermissionLevel.LOW


# --- active_window -----------------------------------------------------------------


def test_active_window_tool_returns_title(monkeypatch):
    monkeypatch.setattr("tools.active_window.get_active_window_title", lambda: "Bloco de Notas")
    result = ActiveWindowTool().execute({})
    assert result.success is True
    assert result.data["title"] == "Bloco de Notas"


def test_active_window_tool_fails_cleanly_when_unavailable(monkeypatch):
    monkeypatch.setattr("tools.active_window.get_active_window_title", lambda: "")
    result = ActiveWindowTool().execute({})
    assert result.success is False


# --- clipboard -----------------------------------------------------------------------


def test_clipboard_read_returns_text(monkeypatch):
    monkeypatch.setattr("tools.clipboard._get_clipboard_text", lambda: "texto copiado")
    result = ClipboardTool().execute({"action": "read"})
    assert result.success is True
    assert result.data["text"] == "texto copiado"


def test_clipboard_read_empty_is_still_success(monkeypatch):
    monkeypatch.setattr("tools.clipboard._get_clipboard_text", lambda: "")
    result = ClipboardTool().execute({"action": "read"})
    assert result.success is True
    assert result.data["text"] == ""


def test_clipboard_summarize_truncates_long_text(monkeypatch):
    long_text = "palavra " * 200
    monkeypatch.setattr("tools.clipboard._get_clipboard_text", lambda: long_text)
    result = ClipboardTool().execute({"action": "summarize"})
    assert result.success is True
    assert result.data["truncated"] is True
    assert len(result.data["text"]) < len(long_text)


def test_clipboard_copy_requires_text():
    result = ClipboardTool().execute({"action": "copy"})
    assert result.success is False


def test_clipboard_copy_writes_text(monkeypatch):
    written = {}
    monkeypatch.setattr("tools.clipboard._set_clipboard_text", lambda t: written.setdefault("t", t) or True)
    result = ClipboardTool().execute({"action": "copy", "text": "novo texto"})
    assert result.success is True
    assert written["t"] == "novo texto"


def test_clipboard_is_medium_permission_not_low():
    """Unlike most read-only tools, clipboard can expose whatever the user last
    copied (a password, a private message) — it must require confirmation."""
    assert ClipboardTool().permission_level == PermissionLevel.MEDIUM


# --- volume ----------------------------------------------------------------------


class _FakeAudioEndpoint:
    def __init__(self):
        self._volume = 0.5
        self._muted = False

    def GetMasterVolumeLevelScalar(self):
        return self._volume

    def SetMasterVolumeLevelScalar(self, value, _):
        self._volume = value

    def GetMute(self):
        return self._muted

    def SetMute(self, value, _):
        self._muted = bool(value)


def test_volume_get(monkeypatch):
    endpoint = _FakeAudioEndpoint()
    monkeypatch.setattr("tools.volume._get_endpoint", lambda: endpoint)
    result = VolumeTool().execute({"action": "get"})
    assert result.success is True
    assert result.data["percent"] == 50


def test_volume_set_clamps_to_0_100(monkeypatch):
    endpoint = _FakeAudioEndpoint()
    monkeypatch.setattr("tools.volume._get_endpoint", lambda: endpoint)
    result = VolumeTool().execute({"action": "set", "percent": 150})
    assert result.success is True
    assert result.data["percent"] == 100


def test_volume_toggle_mute(monkeypatch):
    endpoint = _FakeAudioEndpoint()
    monkeypatch.setattr("tools.volume._get_endpoint", lambda: endpoint)
    result = VolumeTool().execute({"action": "toggle_mute"})
    assert result.success is True
    assert result.data["muted"] is True


def test_volume_set_requires_percent():
    result = VolumeTool().execute({"action": "set"})
    assert result.success is False


def test_volume_reports_failure_when_endpoint_unavailable(monkeypatch):
    def _boom():
        raise OSError("sem dispositivo de áudio")

    monkeypatch.setattr("tools.volume._get_endpoint", _boom)
    result = VolumeTool().execute({"action": "get"})
    assert result.success is False


# --- screenshot ----------------------------------------------------------------------


def test_screenshot_reports_failure_when_mss_not_installed(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "mss" or name.startswith("mss."):
            raise ImportError("no mss")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = ScreenshotTool().execute({})

    assert result.success is False


def test_screenshot_is_medium_permission():
    """A screenshot can capture anything on screen and leaves a persistent file —
    same privacy bar as clipboard, higher than a plain CPU/RAM read."""
    assert ScreenshotTool().permission_level == PermissionLevel.MEDIUM


# --- open_file now also opens directories (replaces Pack 3's separate open_path) -----


def test_open_file_tool_now_also_accepts_directories(monkeypatch, tmp_path):
    called = {}
    monkeypatch.setattr("tools.filesystem.os.startfile", lambda path: called.setdefault("path", path))

    result = OpenFileTool().execute({"path": str(tmp_path)})

    assert result.success is True
    assert "pasta" in result.message.lower()
    assert called["path"] == str(tmp_path)


def test_open_file_tool_rejects_nonexistent_path():
    result = OpenFileTool().execute({"path": "C:\\isso\\nao\\existe\\de\\jeito\\nenhum"})
    assert result.success is False
