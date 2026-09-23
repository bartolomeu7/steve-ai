"""Steve AI — release packaging/signing helper.

Implements the FASE 1 pipeline steps described in
docs/reports/STEVE_UPDATE_CONTRACT.md: build the distributable ZIP from
exactly the git-tracked tree (no ad-hoc include/exclude list to keep in
sync by hand), validate it never contains anything on the forbidden list,
compute its SHA-256, and (optionally) sign it with Ed25519.

Runnable locally without any secret — only the `sign` step needs
STEVE_UPDATE_PRIVATE_KEY, and it fails in a clearly-labeled, distinct way
(exit code 2) when that secret is absent, instead of pretending to succeed.

Subcommands:
    package   --tag vX.Y.Z --output-dir DIR
    checksum  ZIP_PATH
    sign      ZIP_PATH
    verify-tag --tag vX.Y.Z
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Patterns checked against every path inside the built ZIP. Mirrors the
# exclusion list in .gitignore / STEVE_GITHUB_INITIAL_SYNC.md / the FASE 0
# contract (section 4.9) — enforced again here as a second, independent
# safety net rather than trusting `git archive` silently.
FORBIDDEN_PATTERNS = [
    ".venv/*",
    "venv/*",
    "data/*",
    "logs/*",
    "__pycache__/*",
    "*.pyc",
    ".pytest_cache/*",
    ".serena/*",
    ".claude/*",
    "*.exe",
    "launcher/dist/*",
    "launcher/build/*",
    ".env",
    ".env.*",
    "*.pem",
    "*secret*",
    "*token*",
    "*.key",
    "test_all_output.txt",
    "test_output.txt",
    "unused.wav",
]


class ReleaseError(Exception):
    pass


def read_version(root: Path = ROOT) -> str:
    version_file = root / "VERSION"
    if not version_file.exists():
        raise ReleaseError(f"VERSION file not found at {version_file}")
    version = version_file.read_text(encoding="utf-8").strip()
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ReleaseError(f"VERSION content is not MAJOR.MINOR.PATCH: {version!r}")
    return version


def validate_tag_matches_version(tag: str, version: str) -> None:
    if not tag.startswith("v"):
        raise ReleaseError(f"Tag must start with 'v': {tag!r}")
    tag_version = tag[1:]
    if tag_version != version:
        raise ReleaseError(
            f"VERSION file ({version!r}) does not match tag ({tag!r}). "
            "Release refused — see STEVE_UPDATE_CONTRACT.md section 4.1/4.2."
        )


def git_tracked_files(root: Path = ROOT) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def build_zip(tag: str, output_dir: Path, root: Path = ROOT) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / f"steve-ai-{tag}.zip"
    if zip_path.exists():
        zip_path.unlink()

    files = git_tracked_files(root)
    if not files:
        raise ReleaseError("`git ls-files` returned no files — refusing to build an empty package")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path in files:
            src = root / rel_path
            if not src.is_file():
                continue
            zf.write(src, arcname=rel_path)

    return zip_path


def validate_package_contents(zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()

    violations = []
    for name in names:
        for pattern in FORBIDDEN_PATTERNS:
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(name.split("/")[-1], pattern):
                violations.append((name, pattern))
                break

    if violations:
        details = "\n".join(f"  - {name} (matched {pattern!r})" for name, pattern in violations)
        raise ReleaseError(
            f"Forbidden file(s) found inside {zip_path.name} — release refused:\n{details}"
        )


def compute_sha256(zip_path: Path) -> str:
    digest = hashlib.sha256()
    with open(zip_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksum_file(zip_path: Path) -> Path:
    sha256_hex = compute_sha256(zip_path)
    checksum_path = zip_path.with_name(zip_path.name + ".sha256")
    checksum_path.write_text(f"{sha256_hex}  {zip_path.name}\n", encoding="utf-8")
    return checksum_path


PENDING_SECRET_MESSAGE = "PIPELINE PREPARADO — ASSINATURA DE PRODUÇÃO PENDENTE DE SECRET"


def sign_zip(zip_path: Path, private_key_pem: str | None) -> Path | None:
    if not private_key_pem or not private_key_pem.strip():
        print(PENDING_SECRET_MESSAGE)
        print("STEVE_UPDATE_PRIVATE_KEY não está configurado neste ambiente — "
              "assinatura e publicação da Release não serão feitas.")
        return None

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"), password=None
    )
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ReleaseError("STEVE_UPDATE_PRIVATE_KEY is not an Ed25519 private key")

    signature = private_key.sign(zip_path.read_bytes())
    sig_path = zip_path.with_name(zip_path.name + ".sig")
    sig_path.write_bytes(signature)
    return sig_path


def cmd_package(args: argparse.Namespace) -> int:
    version = read_version()
    validate_tag_matches_version(args.tag, version)
    zip_path = build_zip(args.tag, Path(args.output_dir))
    validate_package_contents(zip_path)
    print(f"OK: package built and validated -> {zip_path}")
    return 0


def cmd_checksum(args: argparse.Namespace) -> int:
    zip_path = Path(args.zip_path)
    checksum_path = write_checksum_file(zip_path)
    print(f"OK: checksum written -> {checksum_path}")
    print((checksum_path.read_text(encoding="utf-8")).strip())
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    import os

    zip_path = Path(args.zip_path)
    private_key_pem = os.environ.get("STEVE_UPDATE_PRIVATE_KEY")
    sig_path = sign_zip(zip_path, private_key_pem)
    if sig_path is None:
        return 2
    print(f"OK: signature written -> {sig_path}")
    return 0


def cmd_verify_tag(args: argparse.Namespace) -> int:
    version = read_version()
    validate_tag_matches_version(args.tag, version)
    print(f"OK: VERSION ({version}) matches tag ({args.tag})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_package = sub.add_parser("package", help="Build and validate the release ZIP")
    p_package.add_argument("--tag", required=True)
    p_package.add_argument("--output-dir", required=True)
    p_package.set_defaults(func=cmd_package)

    p_checksum = sub.add_parser("checksum", help="Write the .sha256 file for a ZIP")
    p_checksum.add_argument("zip_path")
    p_checksum.set_defaults(func=cmd_checksum)

    p_sign = sub.add_parser("sign", help="Sign a ZIP with STEVE_UPDATE_PRIVATE_KEY (env var)")
    p_sign.add_argument("zip_path")
    p_sign.set_defaults(func=cmd_sign)

    p_verify = sub.add_parser("verify-tag", help="Check VERSION file matches a tag, no build")
    p_verify.add_argument("--tag", required=True)
    p_verify.set_defaults(func=cmd_verify_tag)

    args = parser.parse_args()
    try:
        return args.func(args)
    except ReleaseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
