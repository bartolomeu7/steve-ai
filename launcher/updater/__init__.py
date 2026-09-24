"""Steve AI — Launcher secure update client (FASE 2).

Public surface:
    check_for_update(project_root) -> UpdateResult
    apply_update(project_root, release, python_exe, dry_run=...) -> UpdateResult

Never imported by main.py / the running Steve app — this package is only
ever used by launcher/SteveLauncher.py (and its frozen PyInstaller build),
before Steve itself starts. See docs/reports/STEVE_PHASE2_SECURE_UPDATE_CLIENT.md.
"""
from .client import apply_update, check_for_update, download_and_verify
from .models import ReleaseAsset, ReleaseInfo, UpdateResult, UpdateState
from .version import VersionError, is_update_available, parse_tag_version, read_local_version

__all__ = [
    "apply_update",
    "check_for_update",
    "download_and_verify",
    "ReleaseAsset",
    "ReleaseInfo",
    "UpdateResult",
    "UpdateState",
    "VersionError",
    "is_update_available",
    "parse_tag_version",
    "read_local_version",
]
