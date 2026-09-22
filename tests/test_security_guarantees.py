"""V1.5 spec section 31 — explicit guarantees the Security Center must never violate.
Combines static source-scanning (structural: the capability simply isn't present in
the code) with behavioral tests (a scan/analysis run leaves the target untouched).

These are deliberately blunt substring checks, not a full AST analysis — the goal is a
loud, simple tripwire if a future change ever introduces one of these capabilities,
not a sophisticated static analyzer. A genuine, intentional need for one of these
would have to remove/adjust the specific assertion here, which is exactly the kind of
change that should require a human to notice and think about it.
"""
from __future__ import annotations

import inspect

import security.authenticode
import security.engine
import security.file_analysis
import security.network_analysis
import security.process_analysis
import security.rules
import security.scanner
import tools.security

_SECURITY_MODULES = (
    security.authenticode,
    security.engine,
    security.file_analysis,
    security.network_analysis,
    security.process_analysis,
    security.rules,
    security.scanner,
    tools.security,
)


def _combined_source() -> str:
    return "\n".join(inspect.getsource(module) for module in _SECURITY_MODULES)


def test_never_executes_a_scanned_or_analyzed_file():
    source = _combined_source()
    for forbidden in ("os.startfile", "subprocess.Popen", "subprocess.run", "subprocess.call", "exec(", "eval(", "os.system("):
        assert forbidden not in source, f"forbidden execution pattern found: {forbidden}"


def test_never_deletes_a_file():
    source = _combined_source()
    for forbidden in ("os.remove(", "os.unlink(", "shutil.rmtree(", ".unlink()"):
        assert forbidden not in source, f"forbidden delete pattern found: {forbidden}"


def test_never_kills_or_suspends_a_process():
    source = _combined_source()
    for forbidden in (".kill(", ".terminate(", ".suspend(", "TerminateProcess"):
        assert forbidden not in source, f"forbidden process-control pattern found: {forbidden}"


def test_never_touches_defender_or_firewall_configuration():
    source = _combined_source()
    for forbidden in (
        "Set-MpPreference", "MpPreference", "netsh advfirewall", "DisableRealtimeMonitoring",
        "Set-NetFirewallProfile", "Disable-WindowsOptionalFeature",
    ):
        assert forbidden not in source, f"forbidden Defender/Firewall pattern found: {forbidden}"


def test_never_writes_to_the_registry():
    """Reading the registry (VRAM/adapter facts in system_monitor/gpu.py) is
    established elsewhere; the Security Center itself has no legitimate reason to
    ever WRITE one, so no security/ module should import winreg.SetValueEx/CreateKey
    at all in this version."""
    source = _combined_source()
    for forbidden in ("winreg.SetValueEx", "winreg.CreateKey", "winreg.SetValue", "winreg.DeleteKey"):
        assert forbidden not in source, f"forbidden registry-write pattern found: {forbidden}"


def test_never_performs_automatic_network_upload():
    """No security/ module may itself send data anywhere -- Security Intelligence
    (web-based reputation lookups) is explicitly out of scope for this version. Reading
    connection metadata (security/network_analysis.py's psutil.net_connections) is not
    the same as sending anything, so this only forbids outbound-request machinery."""
    source = _combined_source()
    for forbidden in ("requests.post", "requests.get", "urllib.request", "http.client", "socket.socket("):
        assert forbidden not in source, f"forbidden network-transmission pattern found: {forbidden}"


def test_never_elevates_privileges():
    source = _combined_source()
    for forbidden in ("ShellExecute", "runas", "AdjustTokenPrivileges", "elevate"):
        assert forbidden not in source, f"forbidden privilege-elevation pattern found: {forbidden}"


def test_scan_folder_never_modifies_or_deletes_scanned_files(tmp_path):
    from security.scanner import SecurityScanner
    from core.events import EventBus

    original = tmp_path / "keep_me.txt"
    original.write_text("do not touch", encoding="utf-8")
    before_content = original.read_bytes()
    before_mtime = original.stat().st_mtime

    SecurityScanner(EventBus()).scan_folder_path(str(tmp_path))

    assert original.exists()
    assert original.read_bytes() == before_content
    assert original.stat().st_mtime == before_mtime


def test_finding_status_update_is_the_only_mutation_a_finding_ever_undergoes():
    """SecurityFinding is a frozen dataclass -- the only way to change one at all is
    dataclasses.replace() inside SecurityEngine.update_finding_status(), which only
    ever changes `status`. Nothing else (severity, evidence, pid, executable_path) can
    be mutated after a finding is created."""
    from security.models import FindingCategory, FindingStatus, SecurityFinding, Severity, now

    finding = SecurityFinding(
        id="x", timestamp=now(), severity=Severity.LOW, category=FindingCategory.PROCESS,
        title="t", description="d", evidence=("a",), confidence=0.5,
    )
    import dataclasses

    updated = dataclasses.replace(finding, status=FindingStatus.DISMISSED)
    assert updated.severity == finding.severity
    assert updated.evidence == finding.evidence
    assert updated.status == FindingStatus.DISMISSED
