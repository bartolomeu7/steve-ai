"""Real CPU/RAM/disk metrics via psutil (already a dependency — see tools/system.py,
which already used it for the check_cpu/check_ram/check_disk tools; this module is the
new single collection point instead of spreading more psutil calls across widgets).

Every function here is defensive: a missing sensor or an OS-level failure never raises,
it logs the reason and returns None for that specific field — see
system_monitor/models.py and docs/ARCHITECTURE.md.
"""
from __future__ import annotations

import logging

import psutil

from system_monitor.models import CpuMetrics, DiskMetrics, MemoryMetrics

logger = logging.getLogger("steve.system_monitor.metrics")


class CpuUsageTracker:
    """psutil.cpu_percent(interval=None) is documented by psutil itself to return a
    meaningless value (0.0 or whatever garbage was last in the buffer) the first time
    it's ever called, because it has nothing to compare against yet — the exact same
    "needs priming" shape already handled for GPU utilization
    (system_monitor/gpu.py::GpuMonitor._utilization_percent) and network/disk rates
    (system_monitor/network.py::RateTracker). Verified on this project's real dev
    machine: the very first call after import returned 0.0, second call (0.3s later)
    returned 26.2 — a real, varying value. One instance lives for the lifetime of
    SystemMonitorService, so "first call" means "since this service started", matching
    how RateTracker/GpuMonitor are each scoped to one service instance too."""

    def __init__(self):
        self._primed = False

    def sample(self) -> tuple[float | None, tuple[float, ...]]:
        first_call = not self._primed
        self._primed = True

        try:
            percent = psutil.cpu_percent(interval=None)
        except Exception:
            logger.exception("Falha ao ler uso de CPU.")
            percent = None
        else:
            if first_call:
                percent = None  # real API output, but documented-meaningless this once

        per_core: tuple[float, ...] = ()
        try:
            raw_per_core = tuple(psutil.cpu_percent(interval=None, percpu=True))
            if not first_call:
                per_core = raw_per_core  # first call's per-core reading is discarded too
        except Exception:
            logger.debug("Uso de CPU por núcleo indisponível.", exc_info=True)

        return percent, per_core


def collect_cpu_metrics(tracker: CpuUsageTracker) -> CpuMetrics:
    percent, per_core = tracker.sample()

    frequency_mhz = None
    frequency_max_mhz = None
    try:
        freq = psutil.cpu_freq()
        if freq:
            frequency_mhz = freq.current
            frequency_max_mhz = freq.max or None
    except Exception:
        logger.debug("Frequência de CPU indisponível.", exc_info=True)

    return CpuMetrics(
        percent=percent,
        per_core_percent=per_core,
        frequency_mhz=frequency_mhz,
        frequency_max_mhz=frequency_max_mhz,
        temperature_celsius=_cpu_temperature_celsius(),
        core_count_logical=psutil.cpu_count(logical=True),
        core_count_physical=psutil.cpu_count(logical=False),
    )


def _cpu_temperature_celsius() -> float | None:
    """Always unavailable, by design — this is deliberate, not a placeholder for a
    future improvement (V1.4.1 PATCH 04 hardening).

    Windows has no standard, driver-independent way to read real CPU package/core
    temperature. The only built-in WMI source, MSAcpi_ThermalZoneTemperature, was
    verified twice on this project's own real dev hardware (AMD/Intel desktop) to be
    unreliable: in V1.4 it stayed frozen at exactly 27.9°C through 6 seconds of
    sustained 8-thread CPU load that should visibly move any real package sensor; in
    this PATCH 04 audit it was re-queried independently and returned the exact same
    frozen 27.9°C again, confirming it is not tracking actual CPU temperature on this
    hardware at all (likely a different/unconnected ACPI thermal zone, e.g.
    motherboard chipset rather than CPU package). psutil itself has no Windows
    equivalent either (psutil.sensors_temperatures() does not exist on this platform —
    confirmed via AttributeError, it's Linux/FreeBSD-only).

    Per the explicit PATCH 04 rule, a WMI value already known to be unreliable must
    never be surfaced as if it were a real reading, even though it IS genuine API
    output (never fabricated) — showing N/A is preferable to a plausible-looking wrong
    number. A trustworthy universal reading would need a vendor-specific kernel driver
    (as HWiNFO/Core Temp use — NVML for NVIDIA, a WinRing0-style driver for others),
    which is out of scope here (see PATCH 04 spec's "novas dependências" constraints)."""
    return None


def collect_memory_metrics() -> MemoryMetrics:
    vm = psutil.virtual_memory()
    return MemoryMetrics(percent=vm.percent, used_bytes=vm.used, available_bytes=vm.available, total_bytes=vm.total)


def collect_disk_metrics(
    path: str = "C:\\", read_bytes_per_sec: float | None = None, write_bytes_per_sec: float | None = None
) -> DiskMetrics:
    try:
        usage = psutil.disk_usage(path)
    except OSError:
        # V1.4.1 PATCH 04: previously returned an all-zero DiskMetrics here, which
        # rendered as a real-looking "0% used, 0 B free / 0 B total" drive — exactly
        # the class of fabricated-value bug this patch exists to eliminate. A disk that
        # couldn't be queried (e.g. a disconnected removable/network drive letter) must
        # show "Indisponível", never a plausible-looking empty disk.
        logger.warning("Falha ao consultar uso de disco em %s.", path, exc_info=True)
        return DiskMetrics(
            path=path,
            percent=None,
            used_bytes=None,
            free_bytes=None,
            total_bytes=None,
            read_bytes_per_sec=read_bytes_per_sec,
            write_bytes_per_sec=write_bytes_per_sec,
        )
    return DiskMetrics(
        path=path,
        percent=usage.percent,
        used_bytes=usage.used,
        free_bytes=usage.free,
        total_bytes=usage.total,
        read_bytes_per_sec=read_bytes_per_sec,
        write_bytes_per_sec=write_bytes_per_sec,
    )
