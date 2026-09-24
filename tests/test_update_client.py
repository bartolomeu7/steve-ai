"""Tests for launcher/updater/ (FASE 2 — secure update client).

Follows this project's existing convention for mocking `requests`
(monkeypatch.setattr("module.requests.get", fake), see test_ai_provider.py)
rather than adding a new test dependency. No test here depends on a real
GitHub Release existing — everything is mocked/local, per the task's own
"NÃO depender de Release real" instruction.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from launcher.updater import client, fs, security
from launcher.updater.models import ReleaseAsset, ReleaseInfo, UpdateState
from launcher.updater.version import VersionError, is_update_available, parse_tag_version, read_local_version


# ---------------------------------------------------------------------------
# 1-3: version comparison
# ---------------------------------------------------------------------------

def test_update_available_when_remote_is_newer():
    assert is_update_available(parse_tag_version("v1.6.0"), parse_tag_version("v1.7.0")) is True


def test_no_update_when_versions_are_equal():
    assert is_update_available(parse_tag_version("v1.6.0"), parse_tag_version("v1.6.0")) is False


def test_no_update_when_remote_is_older_downgrade_rejected():
    assert is_update_available(parse_tag_version("v1.6.0"), parse_tag_version("v1.2.0")) is False


def test_version_comparison_is_not_lexicographic():
    # the exact bug class this module exists to avoid: '1.10.0' < '1.6.0' as strings
    assert is_update_available(parse_tag_version("v1.6.0"), parse_tag_version("v1.10.0")) is True


def test_read_local_version_missing_file_raises(tmp_path):
    with pytest.raises(VersionError):
        read_local_version(tmp_path)


# ---------------------------------------------------------------------------
# 4: invalid metadata
# ---------------------------------------------------------------------------

class FakeJSONResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _write_version(tmp_path: Path, version: str) -> None:
    (tmp_path / "VERSION").write_text(version, encoding="utf-8")


def test_check_for_update_invalid_metadata_missing_assets(tmp_path, monkeypatch):
    _write_version(tmp_path, "1.6.0")
    payload = {"tag_name": "v1.7.0", "assets": []}  # no .zip asset at all
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: FakeJSONResponse(200, payload))

    result = client.check_for_update(tmp_path)
    assert result.state == UpdateState.INVALID_METADATA


def test_check_for_update_invalid_metadata_bad_tag(tmp_path, monkeypatch):
    _write_version(tmp_path, "1.6.0")
    payload = {
        "tag_name": "not-a-version",
        "assets": [
            {"name": "steve-ai-vX.zip", "browser_download_url": "https://x/z", "size": 1},
            {"name": "steve-ai-vX.zip.sha256", "browser_download_url": "https://x/s", "size": 1},
            {"name": "steve-ai-vX.zip.sig", "browser_download_url": "https://x/g", "size": 1},
        ],
    }
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: FakeJSONResponse(200, payload))

    result = client.check_for_update(tmp_path)
    assert result.state == UpdateState.INVALID_METADATA


# ---------------------------------------------------------------------------
# 5 + 17: timeout / offline — must never raise, must never block startup
# ---------------------------------------------------------------------------

def test_check_for_update_timeout_is_check_failed_not_raised(tmp_path, monkeypatch):
    _write_version(tmp_path, "1.6.0")

    def fake_get(*a, **k):
        raise requests.Timeout("simulated timeout")

    monkeypatch.setattr(client.requests, "get", fake_get)
    result = client.check_for_update(tmp_path)
    assert result.state == UpdateState.CHECK_FAILED


def test_check_for_update_offline_is_check_failed_not_raised(tmp_path, monkeypatch):
    _write_version(tmp_path, "1.6.0")

    def fake_get(*a, **k):
        raise requests.ConnectionError("simulated: no network")

    monkeypatch.setattr(client.requests, "get", fake_get)
    result = client.check_for_update(tmp_path)
    assert result.state == UpdateState.CHECK_FAILED


def test_check_for_update_no_releases_yet_is_check_failed(tmp_path, monkeypatch):
    _write_version(tmp_path, "1.6.0")
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: FakeJSONResponse(404, {}))
    result = client.check_for_update(tmp_path)
    assert result.state == UpdateState.CHECK_FAILED


def test_check_for_update_up_to_date(tmp_path, monkeypatch):
    _write_version(tmp_path, "1.6.0")
    payload = {
        "tag_name": "v1.6.0",
        "assets": [
            {"name": "steve-ai-v1.6.0.zip", "browser_download_url": "https://x/z", "size": 1},
            {"name": "steve-ai-v1.6.0.zip.sha256", "browser_download_url": "https://x/s", "size": 1},
            {"name": "steve-ai-v1.6.0.zip.sig", "browser_download_url": "https://x/g", "size": 1},
        ],
    }
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: FakeJSONResponse(200, payload))
    result = client.check_for_update(tmp_path)
    assert result.state == UpdateState.UP_TO_DATE


def test_check_for_update_update_available(tmp_path, monkeypatch):
    _write_version(tmp_path, "1.6.0")
    payload = {
        "tag_name": "v1.7.0",
        "assets": [
            {"name": "steve-ai-v1.7.0.zip", "browser_download_url": "https://x/z", "size": 1},
            {"name": "steve-ai-v1.7.0.zip.sha256", "browser_download_url": "https://x/s", "size": 1},
            {"name": "steve-ai-v1.7.0.zip.sig", "browser_download_url": "https://x/g", "size": 1},
        ],
    }
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: FakeJSONResponse(200, payload))
    result = client.check_for_update(tmp_path)
    assert result.state == UpdateState.UPDATE_AVAILABLE
    assert result.release.tag == "v1.7.0"


# ---------------------------------------------------------------------------
# 6-10: download / checksum / signature
# ---------------------------------------------------------------------------

def _make_release(tmp_path, keypair=None):
    """Builds a real, valid zip+sha256+sig on disk and a ReleaseInfo pointing
    at file:// style stand-in URLs (download is monkeypatched separately)."""
    payload_dir = tmp_path / "release_source"
    payload_dir.mkdir()
    zip_path = payload_dir / "steve-ai-v1.7.0.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("VERSION", "1.7.0")
        zf.writestr("core/app.py", "NEW_CODE = True")

    sha_hex = security.compute_sha256(zip_path)
    sha_path = payload_dir / "steve-ai-v1.7.0.zip.sha256"
    sha_path.write_text(f"{sha_hex}  steve-ai-v1.7.0.zip\n", encoding="utf-8")

    priv = keypair or Ed25519PrivateKey.generate()
    sig = priv.sign(zip_path.read_bytes())
    sig_path = payload_dir / "steve-ai-v1.7.0.zip.sig"
    sig_path.write_bytes(sig)

    release = ReleaseInfo(
        tag="v1.7.0",
        version=parse_tag_version("v1.7.0"),
        zip_asset=ReleaseAsset("steve-ai-v1.7.0.zip", "https://example/zip", zip_path.stat().st_size),
        sha256_asset=ReleaseAsset("steve-ai-v1.7.0.zip.sha256", "https://example/sha", sha_path.stat().st_size),
        sig_asset=ReleaseAsset("steve-ai-v1.7.0.zip.sig", "https://example/sig", sig_path.stat().st_size),
    )
    return release, payload_dir, priv


def _patch_download_from_local_files(monkeypatch, payload_dir: Path):
    """Redirects client._download to copy from payload_dir by asset name
    instead of hitting the network, keyed off the URL's last path segment."""
    def fake_download(url, dest, *, max_bytes=None):
        name = {"https://example/zip": "steve-ai-v1.7.0.zip",
                "https://example/sha": "steve-ai-v1.7.0.zip.sha256",
                "https://example/sig": "steve-ai-v1.7.0.zip.sig"}[url]
        dest.write_bytes((payload_dir / name).read_bytes())

    monkeypatch.setattr(client, "_download", fake_download)


def test_download_and_verify_success(tmp_path, monkeypatch):
    release, payload_dir, priv = _make_release(tmp_path)
    _patch_download_from_local_files(monkeypatch, payload_dir)
    monkeypatch.setattr(security, "load_trusted_public_key", lambda: priv.public_key())

    staging = tmp_path / "staging"
    staging.mkdir()
    zip_path = client.download_and_verify(release, staging)
    assert zip_path.is_file()


def test_download_and_verify_rejects_bad_checksum(tmp_path, monkeypatch):
    release, payload_dir, priv = _make_release(tmp_path)
    # corrupt the checksum file on disk
    (payload_dir / "steve-ai-v1.7.0.zip.sha256").write_text("0" * 64 + "  steve-ai-v1.7.0.zip\n")
    _patch_download_from_local_files(monkeypatch, payload_dir)
    monkeypatch.setattr(security, "load_trusted_public_key", lambda: priv.public_key())

    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(security.SecurityError, match="SHA-256 mismatch"):
        client.download_and_verify(release, staging)


def test_download_and_verify_rejects_bad_signature(tmp_path, monkeypatch):
    release, payload_dir, priv = _make_release(tmp_path)
    other_key = Ed25519PrivateKey.generate()  # verifying against the WRONG public key
    _patch_download_from_local_files(monkeypatch, payload_dir)
    monkeypatch.setattr(security, "load_trusted_public_key", lambda: other_key.public_key())

    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(security.SecurityError, match="signature is invalid"):
        client.download_and_verify(release, staging)


def test_download_and_verify_fails_closed_with_no_trusted_key(tmp_path, monkeypatch):
    release, payload_dir, priv = _make_release(tmp_path)
    _patch_download_from_local_files(monkeypatch, payload_dir)
    monkeypatch.setattr(security, "load_trusted_public_key", lambda: None)

    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(security.SecurityError, match="No trusted public key"):
        client.download_and_verify(release, staging)


def test_download_aborts_when_response_exceeds_max_bytes(tmp_path, monkeypatch):
    class FakeStreamResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield b"x" * (chunk_size)
            yield b"x" * (chunk_size)

    monkeypatch.setattr(client.requests, "get", lambda *a, **k: FakeStreamResponse())
    dest = tmp_path / "out.bin"
    with pytest.raises(security.SecurityError, match="exceeded"):
        client._download("https://example/whatever", dest, max_bytes=10)


# ---------------------------------------------------------------------------
# 11-12: ZIP validity / path traversal (also covered directly in security.py,
# exercised again here through the client's own extraction step)
# ---------------------------------------------------------------------------

def test_apply_update_rejects_path_traversal_package(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()
    _write_version(project_root, "1.6.0")
    (project_root / "core").mkdir()
    (project_root / "core" / "app.py").write_text("OLD")

    # release metadata whose zip contains a traversal entry
    payload_dir = tmp_path / "release_source"
    payload_dir.mkdir()
    zip_path = payload_dir / "steve-ai-v1.7.0.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../evil.txt", "pwned")
    sha_hex = security.compute_sha256(zip_path)
    (payload_dir / "steve-ai-v1.7.0.zip.sha256").write_text(f"{sha_hex}  steve-ai-v1.7.0.zip\n")
    priv = Ed25519PrivateKey.generate()
    (payload_dir / "steve-ai-v1.7.0.zip.sig").write_bytes(priv.sign(zip_path.read_bytes()))

    release = ReleaseInfo(
        tag="v1.7.0",
        version=parse_tag_version("v1.7.0"),
        zip_asset=ReleaseAsset("steve-ai-v1.7.0.zip", "https://example/zip", 1),
        sha256_asset=ReleaseAsset("steve-ai-v1.7.0.zip.sha256", "https://example/sha", 1),
        sig_asset=ReleaseAsset("steve-ai-v1.7.0.zip.sig", "https://example/sig", 1),
    )
    _patch_download_from_local_files(monkeypatch, payload_dir)
    monkeypatch.setattr(security, "load_trusted_public_key", lambda: priv.public_key())

    result = client.apply_update(project_root, release, project_root / "fake_python.exe")
    assert result.state == UpdateState.UNTRUSTED_UPDATE
    assert (project_root / "core" / "app.py").read_text() == "OLD"  # never touched


# ---------------------------------------------------------------------------
# 13-16: staging / backup / health check / rollback (fs.py, plus one
# end-to-end apply_update() success+failure path through client.py)
# ---------------------------------------------------------------------------

def test_staging_dir_created_and_cleaned_up(tmp_path):
    project_root = tmp_path
    (project_root / "launcher").mkdir()
    staging = fs.create_staging_dir(project_root)
    assert staging.is_dir()
    fs.cleanup_staging_dir(staging)
    assert not staging.exists()


def test_backup_excludes_data_and_venv(tmp_path):
    root = tmp_path
    (root / "core").mkdir()
    (root / "core" / "app.py").write_text("code")
    (root / "data").mkdir()
    (root / "data" / "steve.db").write_text("personal")
    (root / ".venv").mkdir()
    (root / ".venv" / "marker").write_text("venv")

    backup_dir = fs.backup_current_install(root, "1.6.0")
    assert (backup_dir / "core" / "app.py").is_file()
    assert not (backup_dir / "data").exists()
    assert not (backup_dir / ".venv").exists()


def test_health_check_true_for_importable_module(tmp_path):
    (tmp_path / "main.py").write_text("X = 1\n")
    result = fs.health_check(tmp_path, Path(__import__("sys").executable), timeout=15)
    assert result is True


def test_health_check_false_for_broken_module(tmp_path):
    (tmp_path / "main.py").write_text("raise RuntimeError('broken on import')\n")
    result = fs.health_check(tmp_path, Path(__import__("sys").executable), timeout=15)
    assert result is False


def test_apply_update_end_to_end_success_then_rollback_on_bad_health(tmp_path, monkeypatch):
    import sys as _sys

    project_root = tmp_path / "project"
    project_root.mkdir()
    _write_version(project_root, "1.6.0")
    (project_root / "main.py").write_text("X = 'old'\n")
    (project_root / "data").mkdir()
    (project_root / "data" / "steve.db").write_text("personal, must survive")

    # Build a release whose payload makes main.py import successfully (good health).
    payload_dir = tmp_path / "release_source"
    payload_dir.mkdir()
    zip_path = payload_dir / "steve-ai-v1.7.0.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("VERSION", "1.7.0")
        zf.writestr("main.py", "X = 'new'\n")
    sha_hex = security.compute_sha256(zip_path)
    (payload_dir / "steve-ai-v1.7.0.zip.sha256").write_text(f"{sha_hex}  steve-ai-v1.7.0.zip\n")
    priv = Ed25519PrivateKey.generate()
    (payload_dir / "steve-ai-v1.7.0.zip.sig").write_bytes(priv.sign(zip_path.read_bytes()))

    release = ReleaseInfo(
        tag="v1.7.0",
        version=parse_tag_version("v1.7.0"),
        zip_asset=ReleaseAsset("steve-ai-v1.7.0.zip", "https://example/zip", 1),
        sha256_asset=ReleaseAsset("steve-ai-v1.7.0.zip.sha256", "https://example/sha", 1),
        sig_asset=ReleaseAsset("steve-ai-v1.7.0.zip.sig", "https://example/sig", 1),
    )
    _patch_download_from_local_files(monkeypatch, payload_dir)
    monkeypatch.setattr(security, "load_trusted_public_key", lambda: priv.public_key())

    result = client.apply_update(project_root, release, Path(_sys.executable))
    assert result.state == UpdateState.SUCCESS
    assert (project_root / "main.py").read_text() == "X = 'new'\n"
    assert (project_root / "data" / "steve.db").read_text() == "personal, must survive"

    # Now simulate a broken new version -> health check fails -> automatic rollback.
    _write_version(project_root, "1.7.0")
    (project_root / "main.py").write_text("X = 'new'\n")
    payload_dir2 = tmp_path / "release_source2"
    payload_dir2.mkdir()
    zip_path2 = payload_dir2 / "steve-ai-v1.8.0.zip"
    with zipfile.ZipFile(zip_path2, "w") as zf:
        zf.writestr("VERSION", "1.8.0")
        zf.writestr("main.py", "raise RuntimeError('broken')\n")
    sha_hex2 = security.compute_sha256(zip_path2)
    (payload_dir2 / "steve-ai-v1.8.0.zip.sha256").write_text(f"{sha_hex2}  steve-ai-v1.8.0.zip\n")
    (payload_dir2 / "steve-ai-v1.8.0.zip.sig").write_bytes(priv.sign(zip_path2.read_bytes()))

    release2 = ReleaseInfo(
        tag="v1.8.0",
        version=parse_tag_version("v1.8.0"),
        zip_asset=ReleaseAsset("steve-ai-v1.8.0.zip", "https://example2/zip", 1),
        sha256_asset=ReleaseAsset("steve-ai-v1.8.0.zip.sha256", "https://example2/sha", 1),
        sig_asset=ReleaseAsset("steve-ai-v1.8.0.zip.sig", "https://example2/sig", 1),
    )

    def fake_download2(url, dest, *, max_bytes=None):
        name = {"https://example2/zip": "steve-ai-v1.8.0.zip",
                "https://example2/sha": "steve-ai-v1.8.0.zip.sha256",
                "https://example2/sig": "steve-ai-v1.8.0.zip.sig"}[url]
        dest.write_bytes((payload_dir2 / name).read_bytes())

    monkeypatch.setattr(client, "_download", fake_download2)

    result2 = client.apply_update(project_root, release2, Path(_sys.executable))
    assert result2.state == UpdateState.ROLLED_BACK
    assert (project_root / "main.py").read_text() == "X = 'new'\n"  # restored to the v1.7.0 backup
    assert (project_root / "data" / "steve.db").read_text() == "personal, must survive"


# ---------------------------------------------------------------------------
# 18: dry-run — verifies everything, touches nothing
# ---------------------------------------------------------------------------

def test_apply_update_dry_run_does_not_mutate(tmp_path, monkeypatch):
    import sys as _sys

    project_root = tmp_path / "project"
    project_root.mkdir()
    _write_version(project_root, "1.6.0")
    (project_root / "main.py").write_text("X = 'old'\n")

    release, payload_dir, priv = _make_release(tmp_path)
    _patch_download_from_local_files(monkeypatch, payload_dir)
    monkeypatch.setattr(security, "load_trusted_public_key", lambda: priv.public_key())

    result = client.apply_update(project_root, release, Path(_sys.executable), dry_run=True)
    assert result.state == UpdateState.SUCCESS
    assert "dry-run" in result.message
    assert (project_root / "main.py").read_text() == "X = 'old'\n"  # untouched
    assert not (project_root / "launcher" / "backups").exists()  # no backup was made
