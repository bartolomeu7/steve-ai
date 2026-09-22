"""Unit tests for security/rules.py's individual signal functions."""
from __future__ import annotations

import time

from security import rules
from security.models import FileMetadata, NetworkEvidence
from system_monitor.models import ProcessInfo


def _process(**overrides) -> ProcessInfo:
    base = dict(pid=1234, name="test.exe", cpu_percent=1.0, memory_percent=1.0, memory_bytes=1024, status="running")
    base.update(overrides)
    return ProcessInfo(**base)


def _metadata(**overrides) -> FileMetadata:
    base = dict(path="C:\\some\\file.exe", exists=True)
    base.update(overrides)
    return FileMetadata(**base)


def test_location_signal_fires_for_temp_directory():
    signal = rules.location_signal(r"C:\Users\Junior\AppData\Local\Temp\evil.exe")
    assert signal is not None
    assert signal[1] > 0


def test_location_signal_none_for_normal_program_files():
    assert rules.location_signal(r"C:\Program Files\Vendor\App\app.exe") is None


def test_double_extension_signal_fires_for_classic_disguise():
    signal = rules.double_extension_signal(r"C:\Downloads\invoice.pdf.exe")
    assert signal is not None


def test_double_extension_signal_none_for_normal_executable():
    assert rules.double_extension_signal(r"C:\Program Files\App\app.exe") is None


def test_double_extension_signal_none_for_normal_document():
    assert rules.double_extension_signal(r"C:\Users\Junior\Documents\report.docx") is None


def test_unsigned_signal_fires_only_on_definite_false():
    assert rules.unsigned_signal(_metadata(signed=False)) is not None
    assert rules.unsigned_signal(_metadata(signed=True)) is None
    assert rules.unsigned_signal(_metadata(signed=None)) is None  # unknown != unsigned


def test_recently_created_signal_fires_for_new_file():
    now = time.time()
    metadata = _metadata(created_at=now - 60)
    assert rules.recently_created_signal(metadata, reference_time=now) is not None


def test_recently_created_signal_none_for_old_file():
    now = time.time()
    metadata = _metadata(created_at=now - 3600 * 24 * 30)
    assert rules.recently_created_signal(metadata, reference_time=now) is None


def test_recently_created_signal_none_without_created_at():
    assert rules.recently_created_signal(_metadata(created_at=None)) is None


def test_external_connection_signal_names_the_endpoint():
    evidence = NetworkEvidence(pid=1, process_name="x.exe", remote_address="8.8.8.8", remote_port=443, status="ESTABLISHED")
    signal = rules.external_connection_signal(evidence)
    assert "8.8.8.8" in signal[0]
    assert "443" in signal[0]


def test_evaluate_process_combines_applicable_signals():
    process = _process(executable_path=r"C:\Users\Junior\AppData\Local\Temp\suspicious.pdf.exe")
    metadata = _metadata(path=process.executable_path, signed=False, created_at=time.time())
    network = NetworkEvidence(pid=process.pid, process_name=process.name, remote_address="1.2.3.4", remote_port=80, status="ESTABLISHED")

    signals = rules.evaluate_process(process, is_new=True, file_metadata=metadata, network=network)

    reasons = [s[0] for s in signals]
    assert len(signals) == 6  # new + temp-location + double-ext + unsigned + recent + network
    assert any("não observado" in r for r in reasons)
    assert any("incomum" in r for r in reasons)
    assert any("extensão dupla" in r for r in reasons)
    assert any("sem assinatura" in r for r in reasons)
    assert any("recentemente" in r for r in reasons)
    assert any("Conexão externa" in r for r in reasons)


def test_evaluate_process_no_signals_for_boring_process():
    process = _process(executable_path=r"C:\Program Files\Vendor\App\app.exe")
    signals = rules.evaluate_process(process, is_new=False, file_metadata=None, network=None)
    assert signals == []


def test_evaluate_process_skips_rules_with_no_evidence():
    process = _process(executable_path=None)
    signals = rules.evaluate_process(process, is_new=False, file_metadata=None, network=None)
    assert signals == []


def test_evaluate_file_combines_applicable_signals():
    metadata = _metadata(path=r"C:\Temp\invoice.pdf.exe", signed=False, created_at=time.time())
    signals = rules.evaluate_file(metadata)
    assert len(signals) >= 3  # location + double-ext + unsigned + recent


def test_is_temp_directory_true_for_temp_path():
    assert rules.is_temp_directory(r"C:\Users\Junior\AppData\Local\Temp\x.exe") is True


def test_is_temp_directory_false_for_program_files():
    assert rules.is_temp_directory(r"C:\Program Files\App\app.exe") is False
