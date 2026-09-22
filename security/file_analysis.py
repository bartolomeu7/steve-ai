"""File metadata and SHA-256 hashing — NEVER opens a file for interpretation, executes
it, or loads it as code. Only `pathlib.Path.stat()` (metadata) and a streamed binary
read for hashing (`hashlib.sha256`), exactly like copying the file's bytes elsewhere
would — this reads the same bytes a file copy operation would, never anything that
interprets them.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from security.authenticode import is_authenticode_signed
from security import rules
from security.models import FileMetadata, FileScanResult, ScanResultStatus, Severity
from security.risk import assess

logger = logging.getLogger("steve.security.file_analysis")

#: Files larger than this are not hashed synchronously in this version — hashing is a
#: streamed read (constant memory, see _sha256_of), but a multi-GB file would still
#: block a scan for a long time with no progress reporting at the single-file level.
#: Reported as UNKNOWN (not SAFE, not an error), never silently skipped.
HASH_SIZE_LIMIT_BYTES = 200 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def analyze_file(path: str) -> FileMetadata:
    """Gathers static facts only. Any failure (missing file, access denied, vanished
    mid-read) becomes FileMetadata.error — never an exception escaping to the caller,
    matching every other collector in this project (system_monitor/metrics.py, etc.)."""
    target = Path(path)
    try:
        if not target.exists():
            return FileMetadata(path=path, exists=False, error="Arquivo não encontrado.")
        if not target.is_file():
            return FileMetadata(path=path, exists=False, error="O caminho não é um arquivo.")
        stat = target.stat()
    except OSError as exc:
        logger.debug("Falha ao consultar metadados de %s.", path, exc_info=True)
        return FileMetadata(path=path, exists=False, error=str(exc))

    sha256: str | None = None
    error: str | None = None
    if stat.st_size <= HASH_SIZE_LIMIT_BYTES:
        try:
            sha256 = _sha256_of(target)
        except OSError as exc:
            logger.debug("Falha ao calcular SHA-256 de %s.", path, exc_info=True)
            error = f"Falha ao calcular hash: {exc}"
    else:
        error = "Arquivo grande demais para hash nesta versão."

    return FileMetadata(
        path=str(target),
        exists=True,
        size_bytes=stat.st_size,
        extension=target.suffix.lower(),
        created_at=stat.st_ctime,
        modified_at=stat.st_mtime,
        sha256=sha256,
        signed=is_authenticode_signed(str(target)),
        signer=None,
        error=error,
    )


def scan_file(path: str) -> FileScanResult:
    """Classifies one file using only structural heuristics (security/rules.py) — this
    is NOT a signature-based antivirus scanner. SAFE means "no indicator this version
    checks for was found", never "guaranteed free of malware" (see
    security/models.py::ScanResultStatus and docs/ARCHITECTURE.md)."""
    metadata = analyze_file(path)

    if not metadata.exists:
        return FileScanResult(path=path, status=ScanResultStatus.ERROR, metadata=metadata, reasons=(metadata.error or "Arquivo inacessível.",))

    if metadata.sha256 is None and metadata.error:
        # Couldn't hash (too large, or a read error after stat succeeded) -- can't make
        # a confident determination either way, so this is explicitly UNKNOWN, not SAFE.
        return FileScanResult(path=path, status=ScanResultStatus.UNKNOWN, metadata=metadata, reasons=(metadata.error,))

    signals = rules.evaluate_file(metadata)
    if not signals:
        return FileScanResult(path=path, status=ScanResultStatus.SAFE, metadata=metadata, reasons=())

    assessment = assess(signals)
    if assessment.severity >= Severity.MEDIUM:
        return FileScanResult(path=path, status=ScanResultStatus.SUSPICIOUS, metadata=metadata, reasons=assessment.signals)

    # Only weak (LOW) signals -- not enough to call it suspicious outright, but not a
    # clean bill either; UNKNOWN is the honest middle ground the spec asks for.
    return FileScanResult(path=path, status=ScanResultStatus.UNKNOWN, metadata=metadata, reasons=assessment.signals)
