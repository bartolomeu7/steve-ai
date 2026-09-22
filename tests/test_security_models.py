"""Unit tests for security/models.py's plain data shapes."""
from __future__ import annotations

import pytest

from security.models import (
    FileMetadata,
    FindingCategory,
    FindingStatus,
    ProtectionState,
    ScanKind,
    ScanResultStatus,
    SecurityFinding,
    Severity,
)


def test_severity_ordering():
    assert Severity.INFO < Severity.LOW < Severity.MEDIUM < Severity.HIGH < Severity.CRITICAL


def test_security_finding_new_id_is_unique():
    assert SecurityFinding.new_id() != SecurityFinding.new_id()


def test_security_finding_is_frozen():
    finding = SecurityFinding(
        id="x", timestamp=1.0, severity=Severity.LOW, category=FindingCategory.PROCESS,
        title="t", description="d", evidence=(), confidence=0.5,
    )
    with pytest.raises(Exception):
        finding.severity = Severity.HIGH  # type: ignore[misc]


def test_security_finding_defaults():
    finding = SecurityFinding(
        id="x", timestamp=1.0, severity=Severity.INFO, category=FindingCategory.FILE,
        title="t", description="d", evidence=(), confidence=1.0,
    )
    assert finding.status == FindingStatus.DETECTED
    assert finding.process_name is None
    assert finding.pid is None


def test_file_metadata_missing_file_defaults():
    metadata = FileMetadata(path="Z:\\nope.exe", exists=False, error="não encontrado")
    assert metadata.sha256 is None
    assert metadata.signed is None


def test_scan_result_status_values_are_stable_strings():
    # These are used as dict keys / persisted-ish values -- a rename here would be a
    # silent breaking change, so pin the literal values explicitly.
    assert ScanResultStatus.SAFE.value == "safe"
    assert ScanResultStatus.SUSPICIOUS.value == "suspicious"
    assert ScanResultStatus.UNKNOWN.value == "unknown"
    assert ScanResultStatus.ERROR.value == "error"


def test_protection_state_values_are_stable_strings():
    assert ProtectionState.PROTECTED.value == "protected"
    assert ProtectionState.MONITORING.value == "monitoring"
    assert ProtectionState.DEGRADED.value == "degraded"
    assert ProtectionState.DISABLED.value == "disabled"


def test_scan_kind_values_are_stable_strings():
    assert ScanKind.QUICK.value == "quick"
    assert ScanKind.FILE.value == "file"
    assert ScanKind.FOLDER.value == "folder"
