"""Resolve a YouTube search query to the first watchable video id (no API key)."""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote_plus

import requests

logger = logging.getLogger("steve.tools.youtube_resolve")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_WATCH_ID = re.compile(r"(?:watch\?v=|/shorts/)([a-zA-Z0-9_-]{11})")


def resolve_first_youtube_video(query: str, timeout: float = 15.0) -> dict:
    """Return {video_id, title, watch_url, embed_url} for the first search hit.

    Raises ValueError if nothing usable is found.
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("Query vazia.")

    search_url = f"https://www.youtube.com/results?search_query={quote_plus(q)}"
    resp = requests.get(search_url, headers={"User-Agent": _UA}, timeout=timeout)
    resp.raise_for_status()
    html = resp.text

    video_id, title = _from_initial_data(html)
    if not video_id:
        ids = _WATCH_ID.findall(html)
        # Prefer unique order-preserving watch ids (skip obvious non-videos later)
        seen: list[str] = []
        for vid in ids:
            if vid not in seen:
                seen.append(vid)
        if not seen:
            raise ValueError(f"Nenhum video encontrado para: {q}")
        video_id = seen[0]
        title = q

    embed_url = (
        f"https://www.youtube.com/embed/{video_id}"
        f"?autoplay=1&rel=0&modestbranding=1&playsinline=1"
    )
    return {
        "video_id": video_id,
        "title": title or q,
        "query": q,
        "watch_url": f"https://www.youtube.com/watch?v={video_id}",
        "embed_url": embed_url,
        "search_url": search_url,
    }


def _from_initial_data(html: str) -> tuple[str | None, str | None]:
    m = re.search(r"ytInitialData\s*=\s*(\{.+?\});\s*</script>", html, re.DOTALL)
    if not m:
        return None, None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None, None

    # Walk for videoRenderer nodes
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if "videoRenderer" in node:
                vr = node["videoRenderer"]
                vid = vr.get("videoId")
                title = None
                title_runs = (vr.get("title") or {}).get("runs") or []
                if title_runs:
                    title = "".join(r.get("text", "") for r in title_runs)
                if vid and len(vid) == 11:
                    return vid, title
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None, None
