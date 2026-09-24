"""
SteveLauncher.py - Minimal launcher for Steve Desktop Assistant.

Finds the Steve install and starts it. Does NOT bundle Steve code.
Works when placed in: project root, launcher/, or launcher/dist/.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from pathlib import Path


def show_error(title: str, message: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
    except Exception:
        print(f"\nERRO: {title}\n{message}\n", file=sys.stderr)


def show_info(title: str, message: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)
    except Exception:
        print(f"\n{title}\n{message}\n")


def looks_like_project_root(path: Path) -> bool:
    return (path / "main.py").is_file() and (path / ".venv" / "Scripts" / "python.exe").is_file()


def get_project_root() -> Path:
    """Resolve Steve root whether the exe lives in root, launcher/, or launcher/dist/."""
    if getattr(sys, "frozen", False):
        start = Path(sys.executable).resolve().parent
    else:
        start = Path(__file__).resolve().parent

    candidates = [
        start,                 # exe next to main.py (project root)
        start.parent,          # exe in launcher/
        start.parent.parent,   # exe in launcher/dist/
    ]
    # Also walk a few parents looking for main.py + .venv
    cur = start
    for _ in range(5):
        candidates.append(cur)
        cur = cur.parent

    seen: set[Path] = set()
    for cand in candidates:
        cand = cand.resolve()
        if cand in seen:
            continue
        seen.add(cand)
        if looks_like_project_root(cand):
            return cand

    # Fallback: old behavior (best-effort)
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if exe_dir.name.lower() == "dist":
            return exe_dir.parent.parent
        if exe_dir.name.lower() == "launcher":
            return exe_dir.parent
        return exe_dir
    return Path(__file__).resolve().parent.parent


def launch_steve(python_path: Path, main_path: Path, project_root: Path, cli_mode: bool) -> subprocess.Popen:
    cmd = [str(python_path), str(main_path)]
    if cli_mode:
        cmd.append("--cli")
    # Keep a console-less GUI child, but do not hide launch failures from the launcher itself.
    creation_flags = 0
    if sys.platform == "win32" and not cli_mode:
        creation_flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    launch_log = log_dir / "launcher.log"
    with launch_log.open("a", encoding="utf-8") as lf:
        lf.write(f"\n--- launch {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        lf.write(f"root={project_root}\npython={python_path}\nmain={main_path}\ncmd={cmd}\n")
    return subprocess.Popen(
        cmd,
        cwd=str(project_root),
        creationflags=creation_flags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def steve_already_running() -> bool:
    """Best-effort: if SingleInstanceLock is held, main.py exits quickly; detect by window title."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found = ctypes.c_bool(False)

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def enum_proc(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip().lower()
            # MainWindow title is "Steve"
            if title == "steve":
                found.value = True
                return False
            return True

        user32.EnumWindows(enum_proc, 0)
        return bool(found.value)
    except Exception:
        return False


def _check_for_update_worker(project_root: Path) -> None:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from launcher.updater import check_for_update

        result = check_for_update(project_root)
        logging.getLogger("steve.launcher").info(
            "update check: state=%s message=%s", result.state.value, result.message
        )
    except Exception as exc:  # noqa: BLE001 - must never break the launch
        logging.getLogger("steve.launcher").debug("update check skipped: %s", exc)


def check_for_update_best_effort(project_root: Path) -> None:
    """Fire-and-forget, non-blocking update check (FASE 2): runs on a daemon
    thread so a slow/hanging network can never delay launch_steve() by even
    its own timeout — 'blocking with a timeout' is still blocking. Never
    applies anything by itself, only logs to logs/launcher.log. Steve is
    local-first: no internet means no update check, never a failed start.
    See docs/reports/STEVE_PHASE2_SECURE_UPDATE_CLIENT.md for why
    download/apply is intentionally not auto-triggered from here yet."""
    thread = threading.Thread(target=_check_for_update_worker, args=(project_root,), daemon=True)
    thread.start()


def main() -> None:
    cli_mode = "--cli" in sys.argv
    project_root = get_project_root()

    if not project_root.exists():
        show_error(
            "Steve Launcher",
            f"Pasta do Steve nao encontrada:\n\n{project_root}",
        )
        sys.exit(1)

    main_path = project_root / "main.py"
    if not main_path.is_file():
        show_error(
            "Steve Launcher",
            "main.py nao encontrado.\n\n"
            f"Procurado em:\n{project_root}\n\n"
            "Coloque o SteveLauncher.exe na pasta do Steve "
            "(junto do main.py) ou em launcher\\ / launcher\\dist\\.",
        )
        sys.exit(1)

    python_path = project_root / ".venv" / "Scripts" / "python.exe"
    if not python_path.is_file():
        show_error(
            "Steve Launcher",
            "Python do .venv nao encontrado.\n\n"
            f"{python_path}\n\n"
            "Crie/ative o ambiente virtual do Steve antes de usar o launcher.",
        )
        sys.exit(1)

    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_dir / "launcher.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    check_for_update_best_effort(project_root)

    if steve_already_running() and not cli_mode:
        # Bring existing instance forward via the app's own single-instance path:
        # launching again makes main.py call bring_existing_instance_to_front().
        try:
            launch_steve(python_path, main_path, project_root, cli_mode=False)
        except Exception:
            pass
        show_info("Steve", "O Steve ja esta aberto — trouxe a janela para frente.")
        sys.exit(0)

    try:
        proc = launch_steve(python_path, main_path, project_root, cli_mode)
    except Exception as exc:
        show_error("Steve Launcher", f"Falha ao iniciar o Steve:\n\n{exc}")
        sys.exit(1)

    # Give the process a moment; if it dies instantly, report instead of silent fail.
    time.sleep(1.5)
    if proc.poll() is not None and proc.returncode not in (0, None):
        show_error(
            "Steve Launcher",
            "O Steve iniciou e fechou imediatamente.\n\n"
            f"Codigo de saida: {proc.returncode}\n"
            f"Veja logs\\steve.log e logs\\launcher.log em:\n{project_root}",
        )
        sys.exit(proc.returncode or 1)

    # Single-instance exit (already running) returns 0 quickly — that's OK.
    sys.exit(0)


if __name__ == "__main__":
    main()
