"""Unit tests for security/file_analysis.py — metadata gathering, hashing, and file
scan classification. Never executes/opens-for-content anything it analyzes; every file
used here is a plain, harmless test fixture created within the test itself."""
from __future__ import annotations

import hashlib

import pytest

from security.file_analysis import analyze_file, scan_file
from security.models import ScanResultStatus


@pytest.fixture
def plain_file(tmp_path):
    path = tmp_path / "readme.txt"
    path.write_text("hello world", encoding="utf-8")
    return path


def test_analyze_file_missing_file():
    metadata = analyze_file(r"Z:\definitely\not\a\real\path.exe")
    assert metadata.exists is False
    assert metadata.sha256 is None
    assert metadata.error


def test_analyze_file_directory_is_not_a_file(tmp_path):
    metadata = analyze_file(str(tmp_path))
    assert metadata.exists is False
    assert metadata.error


def test_analyze_file_real_file_computes_correct_sha256(plain_file):
    metadata = analyze_file(str(plain_file))
    assert metadata.exists is True
    expected = hashlib.sha256(b"hello world").hexdigest()
    assert metadata.sha256 == expected
    assert metadata.size_bytes == len(b"hello world")
    assert metadata.extension == ".txt"
    assert metadata.error is None


def test_analyze_file_skips_hash_above_size_limit(tmp_path, monkeypatch):
    """Never actually allocates a 200MB+ file in a test -- lowers the size limit
    constant instead (to below this small real file's actual size) so the exact same
    code path (stat.st_size > limit) is exercised for real, without faking Path
    internals (which risks breaking exists()/is_file(), themselves stat-based)."""
    import security.file_analysis as file_analysis_module

    monkeypatch.setattr(file_analysis_module, "HASH_SIZE_LIMIT_BYTES", 5)

    path = tmp_path / "bigger_than_limit.bin"
    path.write_bytes(b"x" * 10)

    metadata = file_analysis_module.analyze_file(str(path))
    assert metadata.exists is True
    assert metadata.sha256 is None
    assert metadata.error == "Arquivo grande demais para hash nesta versão."


def test_analyze_file_never_executes_or_interprets_content(tmp_path):
    """A file with a .exe extension but containing plain, non-executable bytes must
    analyze cleanly (stat + hash only) -- if this module ever tried to execute or
    parse it as a real PE, this would raise or hang instead of returning quickly."""
    fake_exe = tmp_path / "not_really_an_exe.exe"
    fake_exe.write_bytes(b"just some bytes, not a real executable" * 5)

    metadata = analyze_file(str(fake_exe))

    assert metadata.exists is True
    assert metadata.sha256 == hashlib.sha256(fake_exe.read_bytes()).hexdigest()


def test_scan_file_missing_file_is_error():
    result = scan_file(r"Z:\nope.exe")
    assert result.status == ScanResultStatus.ERROR
    assert result.reasons


def test_scan_file_boring_file_is_safe(plain_file, monkeypatch):
    """Deliberately mocks out location_signal and recently_created_signal (not the
    whole rule set, and not scan_file/analyze_file themselves, which run for real):
    pytest's tmp_path always resolves under the OS temp dir (triggers the temp-location
    signal, correctly), this repo's own checkout happens to live under
    `...\\Downloads\\...` too (triggers the download-location signal, also correctly),
    and any file a test creates is inevitably "just created" (triggers the recency
    signal, also correctly) -- none of that is a code bug, it's exactly what those
    signals are supposed to catch, but it means no real path/timing in a test process
    can demonstrate "a file with zero signals at all" without isolating these two rules
    (both already covered individually in tests/test_security_rules.py)."""
    monkeypatch.setattr("security.rules.location_signal", lambda path: None)
    monkeypatch.setattr("security.rules.recently_created_signal", lambda metadata, reference_time=None: None)

    result = scan_file(str(plain_file))

    assert result.status == ScanResultStatus.SAFE
    assert result.reasons == ()


def test_scan_file_suspicious_combination_is_suspicious():
    """Double extension + temp-like location + recent creation should combine to
    SUSPICIOUS (MEDIUM+ severity) -- uses a real temp-like path so security/rules.py's
    location_signal fires for real, not mocked."""
    import os
    import tempfile

    suspicious_path = os.path.join(tempfile.gettempdir(), "invoice.pdf.exe")
    with open(suspicious_path, "wb") as handle:
        handle.write(b"fake" * 10)
    try:
        result = scan_file(suspicious_path)
        assert result.status == ScanResultStatus.SUSPICIOUS
        assert len(result.reasons) >= 2
    finally:
        os.remove(suspicious_path)


def test_scan_file_never_writes_to_or_modifies_the_scanned_file(plain_file):
    before = plain_file.read_bytes()
    before_mtime = plain_file.stat().st_mtime
    scan_file(str(plain_file))
    assert plain_file.read_bytes() == before
    assert plain_file.stat().st_mtime == before_mtime
