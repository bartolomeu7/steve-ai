from __future__ import annotations

from security.permissions import PermissionLevel, PermissionManager


def test_low_auto_approved_by_default():
    manager = PermissionManager(auto_approve_up_to=PermissionLevel.LOW)
    assert manager.requires_confirmation(PermissionLevel.LOW) is False


def test_medium_and_high_require_confirmation_by_default():
    manager = PermissionManager(auto_approve_up_to=PermissionLevel.LOW)
    assert manager.requires_confirmation(PermissionLevel.MEDIUM) is True
    assert manager.requires_confirmation(PermissionLevel.HIGH) is True


def test_from_str_parses_level_case_insensitively():
    assert PermissionLevel.from_str("high") == PermissionLevel.HIGH
    assert PermissionLevel.from_str("LOW") == PermissionLevel.LOW
