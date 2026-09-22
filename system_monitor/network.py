"""Instantaneous network (and disk I/O) throughput — psutil only exposes cumulative
byte counters since boot, so "speed" has to be derived from the delta between two
samples over real elapsed time. Small, stateful trackers instead of a bare function so
SystemMonitorService can hold one of each across polls (first sample has nothing to
diff against, so its rate is always None — expected, not an error, same as
GpuMonitor's utilization needing to be primed)."""
from __future__ import annotations

import logging
import time

import psutil

from system_monitor.models import NetworkMetrics

logger = logging.getLogger("steve.system_monitor.network")


class RateTracker:
    """Generic cumulative-counter -> per-second rate tracker, reused for both network
    and disk I/O so the two don't duplicate this logic."""

    def __init__(self):
        self._last_sample_time: float | None = None
        self._last_values: tuple[int, ...] | None = None

    def update(self, *values: int) -> tuple[float | None, ...]:
        """Returns one rate (bytes/sec, or None if this is the first sample) per value
        passed in, in the same order."""
        now = time.monotonic()
        if self._last_sample_time is None or self._last_values is None:
            self._last_sample_time, self._last_values = now, values
            return tuple(None for _ in values)

        elapsed = now - self._last_sample_time
        if elapsed <= 0:
            return tuple(None for _ in values)

        rates = tuple(max(0.0, (current - previous) / elapsed) for current, previous in zip(values, self._last_values))
        self._last_sample_time, self._last_values = now, values
        return rates


class NetworkMonitor:
    def __init__(self):
        self._tracker = RateTracker()

    def sample(self) -> NetworkMetrics:
        try:
            counters = psutil.net_io_counters()
        except Exception:
            logger.exception("Falha ao ler contadores de rede.")
            return NetworkMetrics(upload_bytes_per_sec=None, download_bytes_per_sec=None, bytes_sent_total=0, bytes_recv_total=0)

        upload_rate, download_rate = self._tracker.update(counters.bytes_sent, counters.bytes_recv)
        return NetworkMetrics(
            upload_bytes_per_sec=upload_rate,
            download_bytes_per_sec=download_rate,
            bytes_sent_total=counters.bytes_sent,
            bytes_recv_total=counters.bytes_recv,
        )


class DiskIoMonitor:
    def __init__(self):
        self._tracker = RateTracker()

    def sample(self) -> tuple[float | None, float | None]:
        """Returns (read_bytes_per_sec, write_bytes_per_sec)."""
        try:
            counters = psutil.disk_io_counters()
        except Exception:
            logger.debug("Contadores de E/S de disco indisponíveis.", exc_info=True)
            return None, None
        if counters is None:
            return None, None
        return self._tracker.update(counters.read_bytes, counters.write_bytes)
