"""Structured data for the Security Center (V1.5). Mirrors system_monitor/models.py's
philosophy: frozen dataclasses, tuples (never lists) for anything handed to a widget,
and `None`/explicit "unknown" states instead of ever fabricating a value nothing
actually observed.

Nothing here executes code, deletes files, or takes any destructive action — these are
pure data shapes. See security/engine.py, security/rules.py, security/risk.py for the
logic that produces them.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum, Enum


class Severity(IntEnum):
    """Ordered so severity levels compare naturally (HIGH > LOW, etc.) — see
    security/risk.py, which never assigns HIGH/CRITICAL from a single weak signal."""

    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class FindingCategory(str, Enum):
    PROCESS = "process"
    FILE = "file"
    NETWORK = "network"
    BEHAVIOR = "behavior"
    CONFIGURATION = "configuration"


class FindingStatus(str, Enum):
    """DETECTED is the only state the engine itself ever sets — REVIEWED/DISMISSED are
    user actions (see ui/desktop/dashboard/security_tab.py), never automatic. There is
    deliberately no "THREAT_CONFIRMED" state: this version never claims certainty a
    rule-based, evidence-driven engine cannot actually back up."""

    DETECTED = "detected"
    REVIEWED = "reviewed"
    DISMISSED = "dismissed"


@dataclass(frozen=True)
class SecurityFinding:
    """One evidence-backed observation. `evidence` is the list of concrete signals that
    led to `severity`/`confidence` (security/risk.py) — always populated, so a finding
    can be explained by naming its reasons instead of just showing a bare number (see
    docs/ARCHITECTURE.md's Security Center section)."""

    id: str
    timestamp: float
    severity: Severity
    category: FindingCategory
    title: str
    description: str
    evidence: tuple[str, ...]
    confidence: float
    process_name: str | None = None
    pid: int | None = None
    executable_path: str | None = None
    status: FindingStatus = FindingStatus.DETECTED

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex


@dataclass(frozen=True)
class RiskAssessment:
    """The output of security/risk.py — always carries its own reasons, never a bare
    score. `signals` is the same evidence a SecurityFinding.evidence ends up holding."""

    severity: Severity
    confidence: float
    signals: tuple[str, ...]


@dataclass(frozen=True)
class FileMetadata:
    """Static facts about a file, gathered WITHOUT ever executing, opening for content
    interpretation, or loading it as code — see security/file_analysis.py. `sha256` is
    `None` only when the file couldn't be read (access denied, vanished mid-read, or too
    large for this version's synchronous hashing) — never a placeholder for "not
    computed yet"."""

    path: str
    exists: bool
    size_bytes: int | None = None
    extension: str = ""
    created_at: float | None = None
    modified_at: float | None = None
    sha256: str | None = None
    #: `None` = not evaluated in this version (see security/file_analysis.py's
    #: docstring on why Authenticode verification is a best-effort, Windows-only check).
    signed: bool | None = None
    signer: str | None = None
    error: str | None = None


class ScanResultStatus(str, Enum):
    """SAFE never means "guaranteed free of malware" — see security/file_analysis.py's
    module docstring and docs/ARCHITECTURE.md's Security Center limitations section."""

    SAFE = "safe"
    SUSPICIOUS = "suspicious"
    UNKNOWN = "unknown"
    ERROR = "error"


@dataclass(frozen=True)
class FileScanResult:
    path: str
    status: ScanResultStatus
    metadata: FileMetadata
    #: Empty when status is SAFE/ERROR; the concrete reasons when SUSPICIOUS; a single
    #: explanatory entry when UNKNOWN (e.g. "arquivo grande demais para hash nesta versão").
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class NetworkEvidence:
    """One observed process<->remote-endpoint association — never packet content, never
    TLS-terminated (see security/network_analysis.py's module docstring)."""

    pid: int | None
    process_name: str | None
    remote_address: str
    remote_port: int
    status: str  # psutil's raw connection status string (e.g. "ESTABLISHED")


class ProtectionState(str, Enum):
    """PROTECTED requires the engine to actually be running with no critical failure —
    see security/engine.py. Never set to PROTECTED just because the Dashboard is open."""

    PROTECTED = "protected"
    MONITORING = "monitoring"
    DEGRADED = "degraded"
    DISABLED = "disabled"


class ScanKind(str, Enum):
    QUICK = "quick"
    FILE = "file"
    FOLDER = "folder"


@dataclass(frozen=True)
class ScanProgress:
    kind: ScanKind
    scanned: int
    total: int | None  # None when the total isn't known in advance (e.g. quick scan)
    current_path: str = ""


@dataclass
class ScanSummary:
    """Mutable (unlike the rest of this module) because it's built up incrementally by
    security/scanner.py over the course of one scan run, then handed out as the final
    result — never mutated again after the scan completes."""

    kind: ScanKind
    started_at: float
    finished_at: float | None = None
    files_scanned: int = 0
    findings: list[SecurityFinding] = field(default_factory=list)
    cancelled: bool = False
    error: str | None = None


def now() -> float:
    return time.time()
