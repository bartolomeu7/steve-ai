"""Real (non-mocked) tests against the actual Windows mutex API — CreateMutex/GetLastError
behave identically whether the two 'instances' are two processes or two objects in this
same test process, so this validates the real mechanism without spawning a second
process. Each test uses a unique mutex name to avoid cross-test interference."""
from __future__ import annotations

import sys
import uuid

import pytest

from settings.single_instance import SingleInstanceLock, bring_existing_instance_to_front

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="single-instance guard only targets Windows")


def _unique_name() -> str:
    return f"Global\\SteveTest_{uuid.uuid4().hex}"


def test_first_lock_acquires_successfully():
    lock = SingleInstanceLock(name=_unique_name())
    try:
        assert lock.acquire() is True
    finally:
        lock.release()


def test_second_lock_with_same_name_fails_to_acquire():
    name = _unique_name()
    lock1 = SingleInstanceLock(name=name)
    lock2 = SingleInstanceLock(name=name)
    try:
        assert lock1.acquire() is True
        assert lock2.acquire() is False
    finally:
        lock1.release()
        lock2.release()


def test_releasing_the_first_lock_lets_a_new_one_acquire():
    name = _unique_name()
    lock1 = SingleInstanceLock(name=name)
    assert lock1.acquire() is True
    lock1.release()

    lock2 = SingleInstanceLock(name=name)
    try:
        assert lock2.acquire() is True
    finally:
        lock2.release()


def test_different_names_do_not_conflict():
    lock1 = SingleInstanceLock(name=_unique_name())
    lock2 = SingleInstanceLock(name=_unique_name())
    try:
        assert lock1.acquire() is True
        assert lock2.acquire() is True
    finally:
        lock1.release()
        lock2.release()


def test_release_is_safe_to_call_twice():
    lock = SingleInstanceLock(name=_unique_name())
    lock.acquire()
    lock.release()
    lock.release()  # must not raise


def test_release_before_acquire_is_a_safe_no_op():
    lock = SingleInstanceLock(name=_unique_name())
    lock.release()  # must not raise, never acquired anything


def test_bring_existing_instance_to_front_returns_false_when_no_such_window():
    assert bring_existing_instance_to_front(window_title=f"NoSuchSteveWindow_{uuid.uuid4().hex}") is False
