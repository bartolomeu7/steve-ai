"""Structured, immutable snapshots produced by SystemMonitorService. Deliberately
decoupled from any GUI widget — a snapshot is plain data; ui/desktop/dashboard/ renders
it, nothing here imports tkinter/customtkinter. Every dataclass is frozen and only holds
tuples (never lists) so a snapshot handed to a widget can't be mutated out from under the
collector that produced it.

A field being `None` always means "genuinely not available on this hardware/driver",
never a placeholder for a real value — see system_monitor/metrics.py and
docs/ARCHITECTURE.md for what can and can't be read on Windows without a vendor SDK.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CpuMetrics:
    #: `None` on the very first sample after SystemMonitorService starts — psutil's
    #: cpu_percent(interval=None) is documented to return a meaningless value on its
    #: first-ever call (nothing to compare against yet); see
    #: system_monitor/metrics.py::CpuUsageTracker. Real from the second sample onward.
    percent: float | None
    per_core_percent: tuple[float, ...] = ()
    #: Instantaneous clock speed as reported by the OS (psutil's "current"). On at least
    #: one real test machine this was empirically found to equal `frequency_max_mhz` at
    #: all times regardless of load — see system_monitor/metrics.py and
    #: docs/ARCHITECTURE.md. Exposing both lets a caller/UI notice when they're
    #: suspiciously identical instead of only ever seeing one number.
    frequency_mhz: float | None = None
    #: The CPU's rated maximum clock speed (psutil's cpu_freq().max) — never an
    #: instantaneous reading, shown alongside frequency_mhz for comparison.
    frequency_max_mhz: float | None = None
    temperature_celsius: float | None = None
    core_count_logical: int | None = None
    core_count_physical: int | None = None


@dataclass(frozen=True)
class GpuAdapter:
    name: str


@dataclass(frozen=True)
class GpuMetrics:
    available: bool
    adapters: tuple[GpuAdapter, ...] = ()
    utilization_percent: float | None = None
    temperature_celsius: float | None = None
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None
    #: Human-readable reason `available=False` (or a specific field is None) — shown in
    #: the UI instead of a blank/zero value, e.g. "Nenhum adaptador de vídeo encontrado."
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class MemoryMetrics:
    percent: float
    used_bytes: int
    available_bytes: int
    total_bytes: int


@dataclass(frozen=True)
class DiskMetrics:
    #: All `None` together means the drive/path couldn't be queried (e.g. a
    #: disconnected removable/network drive letter) — see
    #: system_monitor/metrics.py::collect_disk_metrics. Never fabricated as an
    #: all-zero (0%, 0 bytes) disk, which would look like a real, empty drive.
    path: str
    percent: float | None
    used_bytes: int | None
    free_bytes: int | None
    total_bytes: int | None
    read_bytes_per_sec: float | None = None
    write_bytes_per_sec: float | None = None


@dataclass(frozen=True)
class NetworkMetrics:
    upload_bytes_per_sec: float | None
    download_bytes_per_sec: float | None
    bytes_sent_total: int
    bytes_recv_total: int


@dataclass(frozen=True)
class SystemSnapshot:
    timestamp: float
    cpu: CpuMetrics
    gpu: GpuMetrics
    memory: MemoryMetrics
    disk: DiskMetrics
    network: NetworkMetrics


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str
    cpu_percent: float | None
    memory_percent: float | None
    memory_bytes: int | None
    status: str | None
    executable_path: str | None = None
    parent_pid: int | None = None


@dataclass(frozen=True)
class ProcessSnapshot:
    timestamp: float
    processes: tuple[ProcessInfo, ...]
