"""BackgroundRunner tests. Uses a real (withdrawn) Tk root — needed because `.after()`
only exists on a real widget — but never calls mainloop(); the event loop is pumped
manually via repeated `root.update()`, so these stay fast and fully automated."""
from __future__ import annotations

import threading
import time
import tkinter as tk

import pytest

from ui.desktop.async_bridge import BackgroundRunner


@pytest.fixture
def root():
    r = tk.Tk()
    r.withdraw()
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


def _pump_until(root, predicate, timeout=5.0):
    start = time.perf_counter()
    while not predicate():
        root.update()
        time.sleep(0.01)
        if time.perf_counter() - start > timeout:
            raise TimeoutError("condition not met in time")


def test_run_executes_and_calls_on_done(root):
    runner = BackgroundRunner(root)
    results = []

    runner.run(lambda: 1 + 1, on_done=results.append)
    _pump_until(root, lambda: len(results) == 1)

    assert results == [2]


def test_run_calls_on_error_when_fn_raises(root):
    runner = BackgroundRunner(root)
    errors = []

    def boom():
        raise ValueError("nope")

    runner.run(boom, on_error=errors.append)
    _pump_until(root, lambda: len(errors) == 1)

    assert isinstance(errors[0], ValueError)


def test_run_executes_off_the_main_thread(root):
    runner = BackgroundRunner(root)
    main_thread = threading.current_thread()
    seen = []
    done = []

    def work():
        seen.append(threading.current_thread())

    runner.run(work, on_done=lambda _: done.append(True))
    _pump_until(root, lambda: len(done) == 1)

    assert seen[0] is not main_thread


def test_post_is_thread_safe_and_dispatches_on_main_thread(root):
    runner = BackgroundRunner(root)
    main_thread = threading.current_thread()
    received = []

    def worker():
        runner.post(lambda value: received.append((value, threading.current_thread())), "hello")

    threading.Thread(target=worker, daemon=True).start()
    _pump_until(root, lambda: len(received) == 1)

    value, thread = received[0]
    assert value == "hello"
    assert thread is main_thread


def test_on_error_not_called_when_fn_succeeds(root):
    runner = BackgroundRunner(root)
    done, errors = [], []

    runner.run(lambda: "ok", on_done=done.append, on_error=errors.append)
    _pump_until(root, lambda: len(done) == 1)

    assert done == ["ok"]
    assert errors == []


def test_close_prevents_further_posting(root):
    runner = BackgroundRunner(root)
    runner.close()

    calls = []
    runner.post(calls.append, "x")
    root.update()
    time.sleep(0.05)
    root.update()

    assert calls == []
