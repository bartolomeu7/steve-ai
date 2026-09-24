"""Integrity + authenticity verification, and safe ZIP extraction.

Trust model (per docs/reports/STEVE_UPDATE_CONTRACT.md section 4.4): SHA-256
proves the download wasn't corrupted; it does NOT prove the download came from
us. Only the Ed25519 signature proves authenticity, checked against a public
key embedded in the client — never against "it came from github.com".

TRUSTED_PUBLIC_KEY_PEM is intentionally empty right now: no production Ed25519
keypair has been generated yet (STEVE_UPDATE_PRIVATE_KEY is still an
unconfigured GitHub Secret — see STEVE_PHASE0_PHASE1_FINAL_AUDIT.md). Every
verification here fails CLOSED when no trusted key is configured, the same
way security/authenticode.py returns None instead of guessing: an update is
never trusted by default, only when a real key is embedded and the signature
actually checks out against it.
"""
from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Populate with the real production public key (PEM, safe to distribute) once
# STEVE_UPDATE_PRIVATE_KEY is generated and configured. Until then this stays
# empty and every update is correctly treated as untrusted.
TRUSTED_PUBLIC_KEY_PEM: str = ""

# Defends against a decompression bomb inside the release zip — the real
# package is ~1-2 MB (see STEVE_LAUNCHER_GITHUB_RELEASE_PHASE1.md section 9),
# so this leaves generous headroom without being unbounded.
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024  # 200 MB


class SecurityError(Exception):
    """Raised for any integrity/authenticity/path-safety violation. Callers
    must treat this as ABORT UPDATE, never as a warning to continue past."""


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected_hex: str) -> None:
    actual = compute_sha256(path)
    expected = expected_hex.strip().split()[0].lower()
    if actual.lower() != expected:
        raise SecurityError(
            f"SHA-256 mismatch for {path.name}: expected {expected}, got {actual}"
        )


def load_trusted_public_key() -> Optional[Ed25519PublicKey]:
    if not TRUSTED_PUBLIC_KEY_PEM.strip():
        return None
    key = serialization.load_pem_public_key(TRUSTED_PUBLIC_KEY_PEM.encode("utf-8"))
    if not isinstance(key, Ed25519PublicKey):
        raise SecurityError("TRUSTED_PUBLIC_KEY_PEM is not an Ed25519 public key")
    return key


def verify_ed25519(path: Path, signature: bytes, public_key: Optional[Ed25519PublicKey] = None) -> None:
    key = public_key if public_key is not None else load_trusted_public_key()
    if key is None:
        raise SecurityError(
            "No trusted public key configured — refusing to trust any update "
            "(fail-closed; see TRUSTED_PUBLIC_KEY_PEM in this module)"
        )
    try:
        key.verify(signature, path.read_bytes())
    except InvalidSignature as exc:
        raise SecurityError(f"Ed25519 signature is invalid for {path.name}") from exc


def safe_extract_zip(zip_path: Path, dest_dir: Path) -> None:
    """Extracts zip_path into dest_dir, rejecting anything that would escape
    dest_dir (../, absolute paths, drive letters) and capping total
    uncompressed size against a naive zip-bomb."""
    dest_dir = dest_dir.resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        total_uncompressed = 0
        planned: list[tuple[zipfile.ZipInfo, Path]] = []

        for info in zf.infolist():
            name = info.filename
            if name.startswith("/") or name.startswith("\\"):
                raise SecurityError(f"Refusing absolute path in package: {name!r}")
            if ":" in name.split("/")[0]:
                raise SecurityError(f"Refusing drive-letter path in package: {name!r}")

            target = (dest_dir / name).resolve()
            if not (target == dest_dir or target.is_relative_to(dest_dir)):
                raise SecurityError(f"Refusing path-traversal entry in package: {name!r}")

            total_uncompressed += info.file_size
            if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                raise SecurityError(
                    "Package exceeds the maximum allowed uncompressed size "
                    f"({MAX_UNCOMPRESSED_BYTES} bytes) — possible zip bomb"
                )
            planned.append((info, target))

        for info, target in planned:
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, open(target, "wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
