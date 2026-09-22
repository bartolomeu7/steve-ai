"""Tests settings.autostart against a fake winreg module — never touches the real registry."""
from __future__ import annotations

import sys
import types

import pytest

from settings import autostart

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="autostart only targets Windows")


class _FakeKey:
    def __init__(self, store: dict):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _make_fake_winreg(store: dict):
    fake = types.SimpleNamespace()
    fake.HKEY_CURRENT_USER = "HKCU"
    fake.KEY_SET_VALUE = 1
    fake.KEY_READ = 2
    fake.REG_SZ = 1
    fake.OpenKey = lambda hive, subkey, reserved, access: _FakeKey(store)
    fake.SetValueEx = lambda key, name, reserved, type_, value: key.store.__setitem__(name, value)

    def query(key, name):
        if name not in key.store:
            raise FileNotFoundError(name)
        return key.store[name], fake.REG_SZ

    def delete(key, name):
        if name not in key.store:
            raise FileNotFoundError(name)
        del key.store[name]

    fake.QueryValueEx = query
    fake.DeleteValue = delete
    return fake


@pytest.fixture
def fake_registry(monkeypatch):
    store: dict = {}
    monkeypatch.setitem(sys.modules, "winreg", _make_fake_winreg(store))
    return store


def test_enable_autostart_writes_command(fake_registry):
    autostart.enable_autostart(r"C:\Steve\main.exe")
    assert fake_registry[autostart.APP_NAME] == r"C:\Steve\main.exe"


def test_is_autostart_enabled_true_after_enable(fake_registry):
    autostart.enable_autostart(r"C:\Steve\main.exe")
    assert autostart.is_autostart_enabled() is True


def test_is_autostart_enabled_false_when_never_set(fake_registry):
    assert autostart.is_autostart_enabled() is False


def test_disable_autostart_removes_entry(fake_registry):
    autostart.enable_autostart(r"C:\Steve\main.exe")
    autostart.disable_autostart()
    assert autostart.is_autostart_enabled() is False


def test_disable_autostart_is_safe_when_not_set(fake_registry):
    autostart.disable_autostart()  # should not raise


# --- V1.3: idempotency, corrupted state, launch command -------------------------------


def test_enabling_twice_does_not_create_duplicate_entries(fake_registry):
    """A single named Run-key value can't structurally hold two entries — enabling twice
    just overwrites the same value. This locks that guarantee in with a test."""
    autostart.enable_autostart(r"C:\Steve\main.exe")
    autostart.enable_autostart(r"C:\Steve\main.exe")

    assert list(fake_registry.keys()) == [autostart.APP_NAME]
    assert fake_registry[autostart.APP_NAME] == r"C:\Steve\main.exe"
    assert autostart.is_autostart_enabled() is True


def test_enabling_twice_with_a_different_command_overwrites_not_duplicates(fake_registry):
    autostart.enable_autostart(r"C:\Old\main.exe")
    autostart.enable_autostart(r"C:\New\main.exe")

    assert list(fake_registry.keys()) == [autostart.APP_NAME]
    assert fake_registry[autostart.APP_NAME] == r"C:\New\main.exe"


def test_disabling_twice_is_idempotent(fake_registry):
    autostart.enable_autostart(r"C:\Steve\main.exe")
    autostart.disable_autostart()
    autostart.disable_autostart()  # should not raise the second time

    assert autostart.is_autostart_enabled() is False


def test_is_autostart_enabled_false_on_corrupted_registry_state(monkeypatch, fake_registry):
    """A generic OSError (permission problem, unexpected registry state) reading the
    value must not crash a startup diagnostic — it should be treated as 'not enabled',
    same as the value never having existed."""
    autostart.enable_autostart(r"C:\Steve\main.exe")

    def _raise_permission_error(*_args, **_kwargs):
        raise PermissionError("simulated corrupted/inaccessible registry state")

    monkeypatch.setattr(sys.modules["winreg"], "QueryValueEx", _raise_permission_error)

    assert autostart.is_autostart_enabled() is False


def test_default_launch_command_points_at_this_interpreter_and_main_py():
    command = autostart.default_launch_command()

    assert sys.executable in command
    assert "main.py" in command
