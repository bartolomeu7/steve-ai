"""Explicit, auditable signal rules — "what counts as suspicious" lives here and ONLY
here, so every signal that ever reaches security/risk.py can be traced back to one named
function in this file. Deliberately simple, structural heuristics (path, extension,
signature, age, network endpoint) — this is NOT a signature-based antivirus engine and
never claims to be (see docs/ARCHITECTURE.md's Security Center limitations section).

Every individual signal weight is kept low (1-3) — see security/risk.py's module
docstring for why that matters (reaching HIGH/CRITICAL needs several signals to agree).
"""
from __future__ import annotations

import os
import time
from pathlib import PureWindowsPath

from security.models import FileMetadata, NetworkEvidence
from system_monitor.models import ProcessInfo

Signal = tuple[str, int]

#: Directories where a legitimate installed application rarely runs its main
#: executable from directly — but plenty of installers, updaters and portable tools
#: legitimately do too, hence this is a single, modest-weight signal, never
#: sufficient alone to reach HIGH/CRITICAL (see risk.py's single-signal cap).
_SUSPICIOUS_DIR_MARKERS = ("\\temp\\", "\\tmp\\", "\\downloads\\")

#: A file recently created/modified is one weak signal among many a real intrusion
#: could produce — but so is installing a new legitimate app, hence low weight.
_RECENT_THRESHOLD_SECONDS = 5 * 60

#: Extensions that, when preceded by a second (non-executable-looking) extension in
#: the same filename, are the classic "invoice.pdf.exe" disguise — a much stronger,
#: fairly specific signal than the others here.
_EXECUTABLE_EXTENSIONS = {".exe", ".scr", ".com", ".pif", ".bat", ".cmd", ".vbs", ".js"}
_DISGUISE_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".jpg", ".jpeg", ".png", ".txt", ".zip", ".rar"}


def new_process_signal() -> Signal:
    return ("Processo não observado anteriormente nesta sessão de monitoramento", 1)


def location_signal(path: str) -> Signal | None:
    lowered = path.lower()
    for marker in _SUSPICIOUS_DIR_MARKERS:
        if marker in lowered:
            return (f"Executando a partir de um diretório incomum ({marker.strip(chr(92))})", 2)
    return None


def double_extension_signal(path: str) -> Signal | None:
    name = PureWindowsPath(path).name.lower()
    suffixes = PureWindowsPath(name).suffixes
    if len(suffixes) < 2:
        return None
    last = suffixes[-1]
    earlier = suffixes[-2]
    if last in _EXECUTABLE_EXTENSIONS and earlier in _DISGUISE_EXTENSIONS:
        return (f"Nome de arquivo com extensão dupla suspeita ({name})", 3)
    return None


def unsigned_signal(metadata: FileMetadata) -> Signal | None:
    if metadata.signed is False:
        return ("Executável sem assinatura digital verificada", 2)
    return None


def recently_created_signal(metadata: FileMetadata, *, reference_time: float | None = None) -> Signal | None:
    if metadata.created_at is None:
        return None
    reference = reference_time if reference_time is not None else time.time()
    age = reference - metadata.created_at
    if 0 <= age <= _RECENT_THRESHOLD_SECONDS:
        return ("Arquivo criado muito recentemente", 1)
    return None


def external_connection_signal(evidence: NetworkEvidence) -> Signal:
    return (f"Conexão externa observada para {evidence.remote_address}:{evidence.remote_port}", 1)


def evaluate_process(
    process: ProcessInfo,
    *,
    is_new: bool,
    file_metadata: FileMetadata | None = None,
    network: NetworkEvidence | None = None,
) -> list[Signal]:
    """Combines every applicable rule for one process observation into a flat signal
    list — security/risk.py turns this into a Severity. Any rule that doesn't apply
    (e.g. no executable_path known, no file metadata gathered) is simply skipped, never
    forced to produce a signal it has no evidence for."""
    signals: list[Signal] = []

    if is_new:
        signals.append(new_process_signal())

    if process.executable_path:
        loc = location_signal(process.executable_path)
        if loc:
            signals.append(loc)
        ext = double_extension_signal(process.executable_path)
        if ext:
            signals.append(ext)

    if file_metadata is not None:
        unsigned = unsigned_signal(file_metadata)
        if unsigned:
            signals.append(unsigned)
        recent = recently_created_signal(file_metadata)
        if recent:
            signals.append(recent)

    if network is not None:
        signals.append(external_connection_signal(network))

    return signals


def evaluate_file(metadata: FileMetadata) -> list[Signal]:
    """Signals for a standalone file scan (security/file_analysis.py), independent of
    any running process — only the rules that make sense without process context."""
    signals: list[Signal] = []

    loc = location_signal(metadata.path)
    if loc:
        signals.append(loc)

    ext = double_extension_signal(metadata.path)
    if ext:
        signals.append(ext)

    unsigned = unsigned_signal(metadata)
    if unsigned:
        signals.append(unsigned)

    recent = recently_created_signal(metadata)
    if recent:
        signals.append(recent)

    return signals


def is_temp_directory(path: str) -> bool:
    """Used by security/process_analysis.py to decide whether a process's executable
    lives in a location this project already treats as noteworthy — kept here so the
    definition of "temp-like" isn't duplicated between rule evaluation and any future
    caller that needs the same check without going through evaluate_process()."""
    lowered = path.lower()
    env_temp = os.environ.get("TEMP", "").lower()
    env_tmp = os.environ.get("TMP", "").lower()
    if env_temp and env_temp in lowered:
        return True
    if env_tmp and env_tmp in lowered:
        return True
    return any(marker in lowered for marker in _SUSPICIOUS_DIR_MARKERS)
