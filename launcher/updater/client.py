"""Update client — orchestrates the flow from STEVE_UPDATE_CONTRACT.md 4.8:

    CHECKING -> UPDATE_AVAILABLE -> DOWNLOADING -> VERIFYING -> BACKING_UP
    -> APPLYING -> VALIDATING -> SUCCESS   (or FAILED -> ROLLBACK -> ROLLED_BACK)

Steve is local-first: `check_for_update()` never raises and never blocks the
Launcher's own startup — any network/API failure becomes UpdateState.CHECK_FAILED,
logged, and the caller is free to start Steve exactly as before. Only
`apply_update()` performs any filesystem mutation, and only after every
verification step in `download_and_verify()` has passed.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import requests

from . import fs, security
from .models import ReleaseAsset, ReleaseInfo, UpdateResult, UpdateState
from .version import VersionError, is_update_available, parse_tag_version, read_local_version

log = logging.getLogger("steve.launcher.updater")

GITHUB_API_TIMEOUT = 10.0
DOWNLOAD_TIMEOUT = 60.0
DEFAULT_REPO = "bartolomeu7/steve-ai"


def check_for_update(project_root: Path, repo: str = DEFAULT_REPO) -> UpdateResult:
    log.info("update check: starting (repo=%s)", repo)
    try:
        local_version = read_local_version(project_root)
    except VersionError as exc:
        log.error("update check: local VERSION unreadable: %s", exc)
        return UpdateResult(UpdateState.CHECK_FAILED, message=str(exc))

    try:
        response = requests.get(
            f"https://api.github.com/repos/{repo}/releases/latest",
            timeout=GITHUB_API_TIMEOUT,
            headers={"Accept": "application/vnd.github+json"},
        )
    except requests.RequestException as exc:
        log.warning("update check: network/API failure, continuing offline: %s", exc)
        return UpdateResult(UpdateState.CHECK_FAILED, message=f"network error: {exc}")

    if response.status_code == 404:
        log.info("update check: no releases published yet")
        return UpdateResult(UpdateState.CHECK_FAILED, message="no releases published yet")
    if response.status_code != 200:
        log.warning("update check: unexpected status %s", response.status_code)
        return UpdateResult(UpdateState.CHECK_FAILED, message=f"GitHub API status {response.status_code}")

    try:
        payload = response.json()
        release_info = _parse_release_payload(payload)
    except (ValueError, KeyError, VersionError) as exc:
        log.error("update check: invalid release metadata: %s", exc)
        return UpdateResult(UpdateState.INVALID_METADATA, message=str(exc))

    if not is_update_available(local_version, release_info.version):
        log.info("update check: up to date (local=%s, remote=%s)", local_version, release_info.version)
        return UpdateResult(UpdateState.UP_TO_DATE, release=release_info)

    log.info("update check: update available (local=%s, remote=%s)", local_version, release_info.version)
    return UpdateResult(UpdateState.UPDATE_AVAILABLE, release=release_info)


def _parse_release_payload(payload: dict) -> ReleaseInfo:
    tag = payload["tag_name"]
    version = parse_tag_version(tag)
    assets_by_name = {a["name"]: a for a in payload.get("assets", [])}

    zip_name = next((n for n in assets_by_name if n.endswith(".zip")), None)
    if zip_name is None:
        raise KeyError("no .zip asset in release")
    sha_name = f"{zip_name}.sha256"
    sig_name = f"{zip_name}.sig"
    for name in (sha_name, sig_name):
        if name not in assets_by_name:
            raise KeyError(f"missing required asset: {name}")

    def _asset(name: str) -> ReleaseAsset:
        a = assets_by_name[name]
        return ReleaseAsset(name=a["name"], download_url=a["browser_download_url"], size=a.get("size", 0))

    return ReleaseInfo(
        tag=tag,
        version=version,
        zip_asset=_asset(zip_name),
        sha256_asset=_asset(sha_name),
        sig_asset=_asset(sig_name),
    )


def _download(url: str, dest: Path, *, max_bytes: int = security.MAX_UNCOMPRESSED_BYTES) -> None:
    with requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True) as resp:
        resp.raise_for_status()
        written = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise security.SecurityError(f"download exceeded {max_bytes} bytes, aborting")
                f.write(chunk)


def download_and_verify(release: ReleaseInfo, staging_dir: Path) -> Path:
    """Downloads zip+sha256+sig into staging_dir and verifies both before
    returning. Raises security.SecurityError (never applies) on any failure."""
    zip_path = staging_dir / release.zip_asset.name
    sha_path = staging_dir / release.sha256_asset.name
    sig_path = staging_dir / release.sig_asset.name

    log.info("download: %s", release.zip_asset.name)
    _download(release.zip_asset.download_url, zip_path)
    _download(release.sha256_asset.download_url, sha_path)
    _download(release.sig_asset.download_url, sig_path)

    log.info("verify: sha256")
    security.verify_sha256(zip_path, sha_path.read_text(encoding="utf-8"))

    log.info("verify: ed25519 signature")
    security.verify_ed25519(zip_path, sig_path.read_bytes())

    return zip_path


def apply_update(
    project_root: Path,
    release: ReleaseInfo,
    python_exe: Path,
    *,
    dry_run: bool = False,
) -> UpdateResult:
    """Full BACKING_UP -> APPLYING -> VALIDATING flow with automatic rollback
    on health-check failure. dry_run=True stops right before any mutation and
    reports what would happen (contract section 4.8 dry-run requirement)."""
    staging_dir = fs.create_staging_dir(project_root)
    try:
        try:
            local_version = read_local_version(project_root)
            zip_path = download_and_verify(release, staging_dir)
        except (security.SecurityError, VersionError, requests.RequestException) as exc:
            log.error("apply: aborted before mutation: %s", exc)
            return UpdateResult(UpdateState.UNTRUSTED_UPDATE, message=str(exc), release=release)

        extract_dir = staging_dir / "extracted"
        try:
            security.safe_extract_zip(zip_path, extract_dir)
        except security.SecurityError as exc:
            log.error("apply: unsafe package, aborted before mutation: %s", exc)
            return UpdateResult(UpdateState.UNTRUSTED_UPDATE, message=str(exc), release=release)

        if dry_run:
            log.info("dry-run: would back up v%s and apply v%s — no files touched", local_version, release.version)
            return UpdateResult(
                UpdateState.SUCCESS,
                message=f"dry-run OK: v{local_version} -> v{release.version}",
                release=release,
            )

        log.info("apply: backing up v%s", local_version)
        backup_dir = fs.backup_current_install(project_root, str(local_version))

        log.info("apply: applying v%s", release.version)
        try:
            fs.apply_update(extract_dir, project_root)
        except OSError as exc:
            log.error("apply: mutation failed (%s), rolling back", exc)
            fs.restore_backup(backup_dir, project_root)
            return UpdateResult(UpdateState.ROLLED_BACK, message=f"apply failed, restored backup: {exc}", release=release)

        log.info("apply: health check")
        if not fs.health_check(project_root, python_exe):
            log.error("apply: health check failed, rolling back")
            fs.restore_backup(backup_dir, project_root)
            return UpdateResult(UpdateState.ROLLED_BACK, message="health check failed after apply, restored backup", release=release)

        log.info("apply: success, now on v%s", release.version)
        return UpdateResult(UpdateState.SUCCESS, message=f"updated to v{release.version}", release=release)
    finally:
        fs.cleanup_staging_dir(staging_dir)
