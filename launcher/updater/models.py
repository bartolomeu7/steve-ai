"""Data types shared across the update client — states, release metadata, results.

The state names mirror the machine described in docs/reports/STEVE_UPDATE_CONTRACT.md
section 4.8 exactly, so a log line or a future UI can map 1:1 back to that document.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

from packaging.version import Version


class UpdateState(str, enum.Enum):
    IDLE = "idle"
    CHECKING = "checking"
    UP_TO_DATE = "up_to_date"
    UPDATE_AVAILABLE = "update_available"
    CHECK_FAILED = "check_failed"
    INVALID_METADATA = "invalid_metadata"
    UNTRUSTED_UPDATE = "untrusted_update"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    BACKING_UP = "backing_up"
    APPLYING = "applying"
    VALIDATING = "validating"
    SUCCESS = "success"
    FAILED = "failed"
    ROLLBACK = "rollback"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    version: Version
    zip_asset: ReleaseAsset
    sha256_asset: ReleaseAsset
    sig_asset: ReleaseAsset


@dataclass
class UpdateResult:
    state: UpdateState
    message: str = ""
    release: Optional[ReleaseInfo] = None

    def __bool__(self) -> bool:
        return self.state in (UpdateState.SUCCESS, UpdateState.UP_TO_DATE)
