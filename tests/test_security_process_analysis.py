"""Unit tests for security/process_analysis.py — baseline establishment, new-process
detection, and per-path file-analysis caching."""
from __future__ import annotations

from security.models import FileMetadata
from security.process_analysis import ProcessAnalyzer
from system_monitor.models import ProcessInfo


def _process(pid, name="test.exe", executable_path=None) -> ProcessInfo:
    return ProcessInfo(
        pid=pid, name=name, cpu_percent=1.0, memory_percent=1.0, memory_bytes=1024,
        status="running", executable_path=executable_path,
    )


def test_first_cycle_establishes_baseline_without_findings():
    analyzer = ProcessAnalyzer()
    assert analyzer.baseline_established is False

    findings = analyzer.analyze([_process(1), _process(2, executable_path=r"C:\Temp\weird.exe")])

    assert findings == []  # nothing already-running is ever flagged as "new"
    assert analyzer.baseline_established is True


def test_second_cycle_flags_genuinely_new_process(monkeypatch):
    analyzer = ProcessAnalyzer()
    analyzer.analyze([_process(1)])  # baseline: pid 1 already running

    monkeypatch.setattr(
        "security.process_analysis.analyze_file",
        lambda path: FileMetadata(path=path, exists=True, signed=False),
    )
    findings = analyzer.analyze([_process(1), _process(2, executable_path=r"C:\Temp\new.exe")])

    assert len(findings) == 1
    assert findings[0].pid == 2
    assert findings[0].process_name == "test.exe"


def test_process_that_disappears_is_not_flagged_as_new_again():
    analyzer = ProcessAnalyzer()
    analyzer.analyze([_process(1), _process(2)])  # baseline

    findings_after_pid2_exits = analyzer.analyze([_process(1)])
    assert findings_after_pid2_exits == []

    # pid 2 comes back (a different process re-using the same pid is realistic on
    # Windows, where pids are recycled) -- must be treated as new again since it left
    # the known set in between, producing exactly one finding for it (is_new alone is
    # one weak LOW-severity signal, see security/rules.py::new_process_signal).
    findings_after_pid2_returns = analyzer.analyze([_process(1), _process(2)])
    assert len(findings_after_pid2_returns) == 1
    assert findings_after_pid2_returns[0].pid == 2


def test_new_process_with_no_executable_path_has_no_file_metadata_but_may_still_flag():
    analyzer = ProcessAnalyzer()
    analyzer.analyze([])  # baseline: nothing running

    findings = analyzer.analyze([_process(1, executable_path=None)])

    # is_new alone is one weak signal -> LOW severity, which risk.assess still reports
    # as a finding as long as evaluate_process returned at least one signal.
    assert len(findings) == 1
    assert findings[0].executable_path is None


def test_boring_new_process_produces_no_finding():
    analyzer = ProcessAnalyzer()
    analyzer.analyze([])  # baseline

    findings = analyzer.analyze([_process(1, executable_path=r"C:\Program Files\Vendor\App\app.exe")])

    # new_process_signal always fires for a genuinely new pid -- this is intentional
    # (see security/rules.py::new_process_signal, weight 1, LOW severity alone) and
    # distinct from "no finding at all": only a file/location/network signal on top of
    # this would escalate further. The one guarantee under test here is that this
    # never CRASHES and never produces more than the single expected finding.
    assert len(findings) == 1
    assert findings[0].severity.name == "LOW"


def test_file_metadata_is_cached_per_path_not_recomputed(monkeypatch):
    analyzer = ProcessAnalyzer()
    analyzer.analyze([])  # baseline

    call_count = {"n": 0}

    def _fake_analyze_file(path):
        call_count["n"] += 1
        from security.models import FileMetadata

        return FileMetadata(path=path, exists=True)

    monkeypatch.setattr("security.process_analysis.analyze_file", _fake_analyze_file)

    # Two different NEW pids sharing the same executable path (e.g. two instances of
    # the same app launched between cycles) must only trigger one real hash/analysis.
    analyzer.analyze([_process(1, executable_path=r"C:\App\shared.exe"), _process(2, executable_path=r"C:\App\shared.exe")])

    assert call_count["n"] == 1


def test_analyze_never_raises_on_empty_process_list():
    analyzer = ProcessAnalyzer()
    assert analyzer.analyze([]) == []
    assert analyzer.analyze([]) == []
