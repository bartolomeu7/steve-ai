"""Windows single-instance guard, mirroring settings/autostart.py's style: a thin,
Windows-only wrapper around a native OS mechanism (a named mutex, via pywin32 — already
an installed dependency, see voice/tts.py's SAPI5 use), degrading to a no-op on any other
platform rather than raising, since this project targets Windows only.

A named mutex is the standard, stable way to detect "is another copy of this process
already running" on Windows: the OS itself guarantees only one process can hold it, so
there's no race condition or stale-lock-file cleanup to get wrong. It's released
automatically by Windows even if the process crashes, which a lock *file* would not do
on its own.
"""
from __future__ import annotations

import sys

MUTEX_NAME = "Global\\SteveDesktopAssistant_SingleInstance"
WINDOW_TITLE = "Steve"


class SingleInstanceLock:
    """Call `acquire()` once at startup, before building any services. If it returns
    False, another instance is already running — the caller must not build any services
    (no second Ollama session, no second SQLite connection) and should exit after
    optionally calling `bring_existing_instance_to_front()`."""

    def __init__(self, name: str = MUTEX_NAME):
        self.name = name
        self._handle = None

    def acquire(self) -> bool:
        if sys.platform != "win32":
            return True  # no-op elsewhere — nothing else in this project supports non-Windows either
        import win32api
        import win32event
        import winerror

        self._handle = win32event.CreateMutex(None, False, self.name)
        already_running = win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS
        if already_running:
            self.release()
            return False
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        import win32api

        win32api.CloseHandle(self._handle)
        self._handle = None


def bring_existing_instance_to_front(window_title: str = WINDOW_TITLE) -> bool:
    """Best-effort: finds the existing Steve window by title and asks Windows to restore
    and focus it — this is also how a hidden ("minimized to tray") window comes back,
    since there's no literal system tray icon in this version (see
    docs/ARCHITECTURE.md). Returns whether a window was found; the caller must exit
    either way, a second instance never runs services regardless of whether this
    succeeds."""
    if sys.platform != "win32":
        return False
    import win32con
    import win32gui

    hwnd = win32gui.FindWindow(None, window_title)
    if not hwnd:
        return False
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(hwnd)
    return True
