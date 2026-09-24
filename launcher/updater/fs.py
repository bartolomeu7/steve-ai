"""Staging, backup, apply and rollback — all directory-level operations, kept
together because they're tightly coupled (apply's payload set IS the backup's
payload set IS the staging extraction's payload set) and none of the three is
large enough on its own to justify a separate module (see
STEVE_PHASE2_SECURE_UPDATE_CLIENT.md, "arquitetura" — this merges the
reference layout's staging.py/backup.py/rollback.py into one file).

APP vs DATA vs CACHE vs LOGS vs MODELS (contract section 14): only the "app"
payload below is ever touched. User data, logs, the venv and every cache/tool
directory are never read, copied or overwritten by this module.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

# Top-level entries under project_root that are never part of the update
# payload — mirrors the exclusion philosophy in scripts/build_release.py's
# FORBIDDEN_PATTERNS (that file validates the *published* zip; this one
# protects the *live install* on the user's machine — kept as two small,
# independent lists on purpose: the shipped launcher must not import from
# scripts/, which is release-pipeline-only tooling, never bundled into the
# app. If one changes, check the other.).
EXCLUDED_TOP_LEVEL = {
    "data", "logs", ".venv", "venv", "__pycache__", ".pytest_cache",
    ".serena", ".claude", ".git", "test_all_output.txt", "test_output.txt",
    "unused.wav",
}
# launcher/ subpaths that must never be touched even though "launcher" itself
# is part of the app payload (they're build/runtime artifacts and the
# updater's own working directories, not source).
EXCLUDED_LAUNCHER_SUBPATHS = {"dist", "build", "backups", "staging", "__pycache__"}


class FsError(Exception):
    pass


def _iter_payload_entries(root: Path) -> list[Path]:
    entries = []
    for entry in sorted(root.iterdir()):
        if entry.name in EXCLUDED_TOP_LEVEL:
            continue
        if entry.suffix == ".exe":
            continue
        entries.append(entry)
    return entries


def _copy_entry(src: Path, dest_root: Path) -> None:
    dest = dest_root / src.name
    if src.name == "launcher" and src.is_dir():
        _copy_launcher_dir(src, dest)
        return
    if dest.exists():
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    if src.is_dir():
        shutil.copytree(src, dest)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def _copy_launcher_dir(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for child in sorted(src.iterdir()):
        if child.name in EXCLUDED_LAUNCHER_SUBPATHS or child.suffix == ".exe":
            continue
        child_dest = dest / child.name
        if child_dest.exists():
            if child_dest.is_dir():
                shutil.rmtree(child_dest)
            else:
                child_dest.unlink()
        if child.is_dir():
            shutil.copytree(child, child_dest)
        else:
            shutil.copy2(child, child_dest)


def create_staging_dir(project_root: Path) -> Path:
    base = project_root / "launcher" / "staging"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="update_", dir=str(base)))


def cleanup_staging_dir(staging_dir: Path) -> None:
    shutil.rmtree(staging_dir, ignore_errors=True)


def backup_current_install(project_root: Path, current_version: str) -> Path:
    backup_dir = project_root / "launcher" / "backups" / f"v{current_version}"
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    for entry in _iter_payload_entries(project_root):
        _copy_entry(entry, backup_dir)
    return backup_dir


def apply_update(staged_source_dir: Path, project_root: Path) -> None:
    """staged_source_dir is the already-verified, already-extracted new
    version. Overwrites only the app payload in project_root — data/, logs/,
    .venv/ are never touched."""
    for entry in _iter_payload_entries(staged_source_dir):
        _copy_entry(entry, project_root)


def restore_backup(backup_dir: Path, project_root: Path) -> None:
    for entry in _iter_payload_entries(backup_dir):
        _copy_entry(entry, project_root)


def health_check(project_root: Path, python_exe: Path, timeout: float = 20.0) -> bool:
    """Mirrors the check already described in STEVE_UPDATE_CONTRACT.md 4.7:
    a minimal import, not a full test run — must stay fast."""
    try:
        result = subprocess.run(
            [str(python_exe), "-c", "import main"],
            cwd=str(project_root),
            capture_output=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0
