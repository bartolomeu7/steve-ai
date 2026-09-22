"""Launch Steve's internal YouTube player (child WebView2 window)."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("steve.ui.desktop.media_player")

# pywebview is imported only inside media_player_host.py (child process) —
# the main Steve process never loads WebView2 until play is requested.

_HOST = Path(__file__).resolve().parent / "media_player_host.py"
_active: subprocess.Popen | None = None


def open_internal_player(embed_url: str, title: str = "Steve · Musica") -> bool:
    """Open (or replace) the child WebView2 window with the given embed URL."""
    global _active
    if not embed_url:
        return False
    if not _HOST.is_file():
        logger.error("media_player_host.py ausente: %s", _HOST)
        return False

    # Close previous player instance if still running
    if _active is not None and _active.poll() is None:
        try:
            _active.terminate()
        except Exception:
            logger.debug("Nao foi possivel encerrar player anterior", exc_info=True)

    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )

    try:
        _active = subprocess.Popen(
            [sys.executable, str(_HOST), embed_url, title],
            cwd=str(Path(__file__).resolve().parents[2]),
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        logger.info("Player interno iniciado pid=%s title=%r", _active.pid, title)
        return True
    except Exception:
        logger.exception("Falha ao iniciar player interno")
        _active = None
        return False


def stop_internal_player() -> bool:
    """Terminate the child WebView2 player if running."""
    global _active
    if _active is None:
        return False
    if _active.poll() is not None:
        _active = None
        return False
    try:
        _active.terminate()
        _active = None
        logger.info("Player interno encerrado")
        return True
    except Exception:
        logger.debug("Falha ao encerrar player", exc_info=True)
        _active = None
        return False
