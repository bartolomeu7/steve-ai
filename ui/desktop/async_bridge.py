"""Runs blocking work (Orchestrator/VoiceService calls) off the Tk main thread, and
delivers results/progress back on the main thread — the only thread allowed to touch
widgets. Tkinter itself is not thread-safe, so every callback here is dispatched through
a queue drained by `widget.after(...)`, never called directly from a worker thread.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable

logger = logging.getLogger("steve.ui.desktop.async_bridge")

POLL_INTERVAL_MS = 40


class BackgroundRunner:
    def __init__(self, tk_widget):
        self._widget = tk_widget
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._closed = False
        self._schedule_poll()

    def run(
        self,
        fn: Callable[..., Any],
        *args,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        **kwargs,
    ) -> None:
        """Runs fn(*args, **kwargs) in a daemon thread; on_done/on_error are invoked on
        the main thread with the result/exception once it finishes."""

        def worker():
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - genuinely want to catch anything here
                logger.exception("Falha em tarefa de segundo plano.")
                if on_error:
                    self.post(on_error, exc)
            else:
                if on_done:
                    self.post(on_done, result)

        threading.Thread(target=worker, daemon=True).start()

    def post(self, callback: Callable[..., None], *args) -> None:
        """Thread-safe: queues `callback(*args)` to run on the main thread. Safe to call
        from a worker thread (e.g. a progress callback passed into VoiceService)."""
        if self._closed:
            return
        self._queue.put(lambda: callback(*args))

    def close(self) -> None:
        self._closed = True

    def _schedule_poll(self) -> None:
        if self._closed:
            return
        self._drain_queue()
        try:
            self._widget.after(POLL_INTERVAL_MS, self._schedule_poll)
        except Exception:  # pragma: no cover - widget already destroyed
            self._closed = True

    def _drain_queue(self) -> None:
        while True:
            try:
                callback = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                callback()
            except Exception:  # pragma: no cover - defensive: a UI callback must not kill the poll loop
                logger.exception("Falha ao processar callback da fila de UI.")
