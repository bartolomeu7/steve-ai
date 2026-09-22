"""Standalone WebView2 host for Steve's internal music player.

Run as a child process so pywebview's event loop does not fight CustomTkinter.
"""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: media_player_host.py <embed_url> [title]", file=sys.stderr)
        return 2
    url = argv[0]
    title = argv[1] if len(argv) > 1 else "Steve · Musica"
    try:
        import webview
    except ImportError:
        print("pywebview nao instalado", file=sys.stderr)
        return 1
    webview.create_window(
        title,
        url,
        maximized=True,
        background_color="#0A0C10",
        text_select=False,
    )
    # Edge WebView2 on Windows — uses system Edge, no Chromium download.
    webview.start(gui="edgechromium")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
