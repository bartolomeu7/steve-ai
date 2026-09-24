"""VERSION file reading + version comparison.

Uses `packaging.version.Version` (PEP 440, the same parser pip/setuptools use)
instead of a hand-rolled comparator — plain string comparison would get
`'1.10.0' < '1.6.0'` wrong (lexicographic), which is exactly the class of bug
this module exists to avoid.
"""
from __future__ import annotations

from pathlib import Path

from packaging.version import InvalidVersion, Version


class VersionError(Exception):
    pass


def read_local_version(project_root: Path) -> Version:
    version_file = project_root / "VERSION"
    if not version_file.is_file():
        raise VersionError(f"VERSION file not found at {version_file}")
    raw = version_file.read_text(encoding="utf-8").strip()
    return _parse(raw, source=f"VERSION file ({version_file})")


def parse_tag_version(tag: str) -> Version:
    """Tags are 'vX.Y.Z' per STEVE_UPDATE_CONTRACT.md section 4.2; VERSION has no 'v'."""
    raw = tag[1:] if tag[:1] in ("v", "V") else tag
    return _parse(raw, source=f"release tag ({tag!r})")


def _parse(raw: str, *, source: str) -> Version:
    try:
        return Version(raw)
    except InvalidVersion as exc:
        raise VersionError(f"{source} is not a valid version: {raw!r}") from exc


def is_update_available(local: Version, remote: Version) -> bool:
    """remote > local only — remote == local is a no-op, remote < local is a
    downgrade attempt and must be rejected (contract section 4.5)."""
    return remote > local
