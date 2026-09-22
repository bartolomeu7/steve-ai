"""Windows auto-start support (HKCU Run key). Mechanism only — nothing calls this
automatically except ui/desktop/app.py::SteveApp._apply_settings(), which enables/
disables it when the user explicitly toggles Settings.autostart_enabled."""
from __future__ import annotations

import sys
from pathlib import Path

APP_NAME = "SteveDesktopAssistant"


def _open_run_key(write: bool = False):
    import winreg

    access = winreg.KEY_SET_VALUE if write else winreg.KEY_READ
    return winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, access
    )


def enable_autostart(command: str) -> None:
    if sys.platform != "win32":
        raise NotImplementedError("Auto-start só é suportado no Windows.")
    import winreg

    with _open_run_key(write=True) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)


def disable_autostart() -> None:
    if sys.platform != "win32":
        raise NotImplementedError("Auto-start só é suportado no Windows.")
    import winreg

    try:
        with _open_run_key(write=True) as key:
            winreg.DeleteValue(key, APP_NAME)
    except FileNotFoundError:
        pass


def is_autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with _open_run_key(write=False) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        # FileNotFoundError (key/value never created) is the common case; a broader
        # OSError (e.g. a permission problem, or a corrupted registry entry the reader
        # chokes on) is treated the same way — a startup diagnostic must never crash the
        # app over the Run key being in an unexpected state, it should just report "not
        # enabled" and let the user re-enable it through Settings if that's wrong.
        return False


def default_launch_command() -> str:
    """The command written to the Run key by default: relaunches this same Python
    interpreter against main.py, quoted for paths containing spaces. A packaged .exe
    build would pass its own path to enable_autostart() instead of using this."""
    main_script = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{sys.executable}" "{main_script}"'
