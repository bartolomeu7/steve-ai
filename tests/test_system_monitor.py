"""Unit tests for system_monitor/ — all hardware/OS access is mocked (psutil, win32pdh,
win32com, winreg) so this suite never depends on the actual machine's hardware, per the
V1.4 spec. Real-hardware validation lives in the manual/real testing performed for the
V1.4 report, not in this automated suite."""
from __future__ import annotations

import sys
import threading
import time
import types
from dataclasses import FrozenInstanceError

import pytest

from core.events import EventBus
from system_monitor.gpu import GpuMonitor
from system_monitor.metrics import CpuUsageTracker, collect_cpu_metrics, collect_disk_metrics, collect_memory_metrics
from system_monitor.models import (
    CpuMetrics,
    DiskMetrics,
    GpuAdapter,
    GpuMetrics,
    MemoryMetrics,
    NetworkMetrics,
    ProcessInfo,
    ProcessSnapshot,
    SystemSnapshot,
)
from system_monitor.network import DiskIoMonitor, NetworkMonitor, RateTracker
from system_monitor.processes import ProcessCollector
from system_monitor.service import (
    NETWORK_METRICS_UPDATED,
    PROCESS_LIST_UPDATED,
    SYSTEM_METRICS_UPDATED,
    SYSTEM_MONITOR_ERROR,
    SYSTEM_MONITOR_STARTED,
    SYSTEM_MONITOR_STOPPED,
    SystemMonitorService,
)


def _mock_fast_process_collection(monkeypatch) -> None:
    """SystemMonitorService._run() always collects processes once immediately on the
    first cycle, regardless of process_interval (see service.py) — real, unmocked
    psutil.process_iter() was measured to occasionally take up to ~1s+ on this machine
    under system load (see the V1.4 report's performance section), which made
    start()/stop()-timing tests flaky under the concurrent load of the full suite.
    Call this in any test that starts a real SystemMonitorService and doesn't itself
    care about process-list content. NOT autouse — the ProcessCollector-level tests
    below exercise its real .sample() logic directly and must not be short-circuited."""
    monkeypatch.setattr(
        "system_monitor.processes.ProcessCollector.sample",
        lambda self, limit=100: ProcessSnapshot(timestamp=0.0, processes=()),
    )

# --- 1: SystemSnapshot / 17: immutability -----------------------------------------------


def _fake_snapshot() -> SystemSnapshot:
    return SystemSnapshot(
        timestamp=1.0,
        cpu=CpuMetrics(percent=10.0),
        gpu=GpuMetrics(available=False, unavailable_reason="teste"),
        memory=MemoryMetrics(percent=50.0, used_bytes=1, available_bytes=1, total_bytes=2),
        disk=DiskMetrics(path="C:\\", percent=1.0, used_bytes=1, free_bytes=1, total_bytes=2),
        network=NetworkMetrics(upload_bytes_per_sec=None, download_bytes_per_sec=None, bytes_sent_total=0, bytes_recv_total=0),
    )


def test_system_snapshot_construction():
    snapshot = _fake_snapshot()
    assert snapshot.cpu.percent == 10.0
    assert snapshot.gpu.available is False


def test_system_snapshot_is_immutable():
    snapshot = _fake_snapshot()
    with pytest.raises(FrozenInstanceError):
        snapshot.timestamp = 2.0  # type: ignore[misc]


def test_process_snapshot_is_immutable_and_uses_tuples():
    snapshot = ProcessSnapshot(timestamp=1.0, processes=(ProcessInfo(pid=1, name="a", cpu_percent=1.0, memory_percent=1.0, memory_bytes=1, status="running"),))
    assert isinstance(snapshot.processes, tuple)
    with pytest.raises(FrozenInstanceError):
        snapshot.processes = ()  # type: ignore[misc]


# --- 2-4: CPU / RAM / Disk metrics (psutil mocked) ---------------------------------------


def test_collect_cpu_metrics_uses_psutil(monkeypatch):
    monkeypatch.setattr("system_monitor.metrics.psutil.cpu_percent", lambda **kwargs: 33.3 if not kwargs.get("percpu") else [10.0, 20.0])
    monkeypatch.setattr("system_monitor.metrics.psutil.cpu_freq", lambda: types.SimpleNamespace(current=2500.0, max=4200.0))
    monkeypatch.setattr("system_monitor.metrics.psutil.cpu_count", lambda logical=True: 8 if logical else 4)
    monkeypatch.setattr("system_monitor.metrics._cpu_temperature_celsius", lambda: 45.0)

    tracker = CpuUsageTracker()
    tracker.sample()  # first sample is always discarded (see test_cpu_usage_first_sample_is_unavailable) -- prime it here
    metrics = collect_cpu_metrics(tracker)

    assert metrics.percent == 33.3
    assert metrics.per_core_percent == (10.0, 20.0)
    assert metrics.frequency_mhz == 2500.0
    assert metrics.frequency_max_mhz == 4200.0
    assert metrics.temperature_celsius == 45.0
    assert metrics.core_count_logical == 8
    assert metrics.core_count_physical == 4


def test_cpu_usage_first_sample_is_unavailable(monkeypatch):
    """V1.4.1 PATCH 04: psutil.cpu_percent(interval=None) documents its own first-ever
    call as meaningless (nothing to compare against yet) — verified for real on this
    project's dev hardware: the very first call after import returned 0.0, the second
    (0.3s later) returned a real 26.2. Showing that first 0.0 as if it were a stable
    reading would be exactly the deceptive-first-sample bug this patch exists to fix."""
    values = iter([0.0, 42.0])
    per_core_values = iter([[0.0, 0.0], [40.0, 44.0]])
    monkeypatch.setattr(
        "system_monitor.metrics.psutil.cpu_percent",
        lambda **kwargs: next(per_core_values) if kwargs.get("percpu") else next(values),
    )

    tracker = CpuUsageTracker()
    first_percent, first_per_core = tracker.sample()
    second_percent, second_per_core = tracker.sample()

    assert first_percent is None
    assert first_per_core == ()
    assert second_percent == 42.0
    assert second_per_core == (40.0, 44.0)


def test_collect_cpu_metrics_survives_psutil_failure(monkeypatch):
    def _raise(**kwargs):
        raise OSError("simulated failure")

    monkeypatch.setattr("system_monitor.metrics.psutil.cpu_percent", _raise)
    monkeypatch.setattr("system_monitor.metrics.psutil.cpu_freq", _raise)
    monkeypatch.setattr("system_monitor.metrics.psutil.cpu_count", lambda logical=True: None)
    monkeypatch.setattr("system_monitor.metrics._cpu_temperature_celsius", lambda: None)

    metrics = collect_cpu_metrics(CpuUsageTracker())

    assert metrics.percent is None
    assert metrics.per_core_percent == ()
    assert metrics.frequency_mhz is None
    assert metrics.frequency_max_mhz is None


def test_cpu_temperature_is_always_unavailable_by_design():
    """V1.4.1 PATCH 04: MSAcpi_ThermalZoneTemperature was re-verified on real hardware
    to still be frozen at the exact same 27.9°C already found unreliable in V1.4 (see
    system_monitor/metrics.py's docstring) — the function no longer queries WMI at all
    and always returns None, regardless of platform or any WMI mocking, by design."""
    from system_monitor.metrics import _cpu_temperature_celsius

    assert _cpu_temperature_celsius() is None


def test_cpu_temperature_unavailable_when_wmi_query_fails(monkeypatch):
    """Kept from V1.4 for regression coverage: even if something re-introduced a WMI
    call here in the future, a failing query must still resolve to None, never raise."""
    from system_monitor.metrics import _cpu_temperature_celsius

    class _FailingWmiClient:
        @staticmethod
        def GetObject(_path):
            raise OSError("simulated: no ACPI thermal zone provider")

    fake_win32com = types.SimpleNamespace(client=_FailingWmiClient)
    monkeypatch.setitem(sys.modules, "win32com", fake_win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", _FailingWmiClient)
    monkeypatch.setattr(sys, "platform", "win32")

    assert _cpu_temperature_celsius() is None


def test_collect_memory_metrics_uses_psutil(monkeypatch):
    monkeypatch.setattr(
        "system_monitor.metrics.psutil.virtual_memory",
        lambda: types.SimpleNamespace(percent=62.5, used=100, available=50, total=150),
    )
    metrics = collect_memory_metrics()
    assert metrics == MemoryMetrics(percent=62.5, used_bytes=100, available_bytes=50, total_bytes=150)


def test_collect_disk_metrics_uses_psutil(monkeypatch):
    monkeypatch.setattr(
        "system_monitor.metrics.psutil.disk_usage",
        lambda path: types.SimpleNamespace(percent=80.0, used=800, free=200, total=1000),
    )
    metrics = collect_disk_metrics("D:\\", read_bytes_per_sec=10.0, write_bytes_per_sec=5.0)
    assert metrics.path == "D:\\"
    assert metrics.percent == 80.0
    assert metrics.read_bytes_per_sec == 10.0
    assert metrics.write_bytes_per_sec == 5.0


def test_collect_disk_metrics_survives_missing_drive(monkeypatch):
    """V1.4.1 PATCH 04: a drive that can't be queried must report None across the
    board, never a fabricated all-zero disk (which used to look like a real, empty
    0-byte drive rather than "unavailable")."""

    def _raise(path):
        raise OSError("no such drive")

    monkeypatch.setattr("system_monitor.metrics.psutil.disk_usage", _raise)
    metrics = collect_disk_metrics("Z:\\")
    assert metrics.percent is None
    assert metrics.used_bytes is None
    assert metrics.free_bytes is None
    assert metrics.total_bytes is None


# --- 5: network metrics -------------------------------------------------------------------


def test_rate_tracker_first_sample_returns_none():
    tracker = RateTracker()
    assert tracker.update(100, 200) == (None, None)


def test_rate_tracker_computes_rate_between_samples(monkeypatch):
    tracker = RateTracker()
    times = iter([10.0, 11.0])
    monkeypatch.setattr("system_monitor.network.time.monotonic", lambda: next(times))
    tracker.update(0, 0)
    upload, download = tracker.update(100, 200)
    assert upload == pytest.approx(100.0)
    assert download == pytest.approx(200.0)


def test_network_monitor_sample_uses_psutil(monkeypatch):
    counters = iter([
        types.SimpleNamespace(bytes_sent=0, bytes_recv=0),
        types.SimpleNamespace(bytes_sent=500, bytes_recv=1000),
    ])
    times = iter([0.0, 1.0])
    monkeypatch.setattr("system_monitor.network.psutil.net_io_counters", lambda: next(counters))
    monkeypatch.setattr("system_monitor.network.time.monotonic", lambda: next(times))

    monitor = NetworkMonitor()
    first = monitor.sample()
    second = monitor.sample()

    assert first.upload_bytes_per_sec is None
    assert second.upload_bytes_per_sec == pytest.approx(500.0)
    assert second.download_bytes_per_sec == pytest.approx(1000.0)
    assert second.bytes_sent_total == 500


def test_network_monitor_survives_psutil_failure(monkeypatch):
    def _raise():
        raise OSError("no network adapters")

    monkeypatch.setattr("system_monitor.network.psutil.net_io_counters", _raise)
    metrics = NetworkMonitor().sample()
    assert metrics.upload_bytes_per_sec is None
    assert metrics.bytes_sent_total == 0


def test_disk_io_monitor_returns_none_when_unavailable(monkeypatch):
    monkeypatch.setattr("system_monitor.network.psutil.disk_io_counters", lambda: None)
    assert DiskIoMonitor().sample() == (None, None)


def test_disk_io_monitor_first_sample_returns_none_rates(monkeypatch):
    """V1.4.1 PATCH 04, item 19 (disk rate priming): the first sample must be treated as
    priming only, same as NetworkMonitor's first sample — explicit coverage for
    DiskIoMonitor specifically, not just the generic RateTracker it's built on."""
    counters = iter([
        types.SimpleNamespace(read_bytes=1000, write_bytes=500),
        types.SimpleNamespace(read_bytes=2000, write_bytes=1500),
    ])
    times = iter([0.0, 1.0])
    monkeypatch.setattr("system_monitor.network.psutil.disk_io_counters", lambda: next(counters))
    monkeypatch.setattr("system_monitor.network.time.monotonic", lambda: next(times))

    monitor = DiskIoMonitor()
    first_read, first_write = monitor.sample()
    second_read, second_write = monitor.sample()

    assert (first_read, first_write) == (None, None)
    assert second_read == pytest.approx(1000.0)
    assert second_write == pytest.approx(1000.0)


# --- 6-7: GPU available / unavailable ------------------------------------------------------


def _fake_wmi_video_controllers(names: list[str]):
    class _Row:
        def __init__(self, name):
            self.Name = name

    class _Wmi:
        @staticmethod
        def ExecQuery(_query):
            return [_Row(n) for n in names]

    class _Client:
        @staticmethod
        def GetObject(_moniker):
            return _Wmi()

    return types.SimpleNamespace(client=_Client)


def test_gpu_monitor_unavailable_when_no_adapters(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    fake_win32com = _fake_wmi_video_controllers([])
    monkeypatch.setitem(sys.modules, "win32com", fake_win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", fake_win32com.client)

    metrics = GpuMonitor().sample()

    assert metrics.available is False
    assert metrics.unavailable_reason


def test_gpu_monitor_unavailable_on_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    metrics = GpuMonitor().sample()
    assert metrics.available is False


def test_gpu_monitor_available_reports_adapters(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    fake_win32com = _fake_wmi_video_controllers(["Fake GPU 3000"])
    monkeypatch.setitem(sys.modules, "win32com", fake_win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", fake_win32com.client)
    # win32pdh unavailable -> utilization/memory come back None, but adapters still show
    monkeypatch.setitem(sys.modules, "win32pdh", None)

    metrics = GpuMonitor().sample()

    assert metrics.available is True
    assert metrics.adapters == (GpuAdapter(name="Fake GPU 3000"),)
    assert metrics.utilization_percent is None
    assert metrics.temperature_celsius is None  # never fabricated — no cross-vendor Windows API exists


def test_gpu_monitor_close_is_safe_when_never_sampled():
    GpuMonitor().close()  # must not raise


def test_gpu_monitor_multiple_adapters_lists_all_names(monkeypatch):
    """V1.4.1 PATCH 04, item 16 (multi-GPU): real dev hardware has both a dedicated AMD
    card and an integrated Intel GPU — Win32_VideoController lists both, and the code
    must not silently pick/hide one."""
    monkeypatch.setattr(sys, "platform", "win32")
    fake_win32com = _fake_wmi_video_controllers(["AMD Radeon RX 6750 XT", "Intel(R) UHD Graphics 770"])
    monkeypatch.setitem(sys.modules, "win32com", fake_win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", fake_win32com.client)
    monkeypatch.setitem(sys.modules, "win32pdh", None)

    metrics = GpuMonitor().sample()

    assert metrics.available is True
    assert {a.name for a in metrics.adapters} == {"AMD Radeon RX 6750 XT", "Intel(R) UHD Graphics 770"}


def _fake_win32pdh(engine_instances: list[str], values_by_call: list[dict[str, float]]):
    """Simulates win32pdh's real, verified-on-hardware behavior: GetFormattedCounterValue
    raises for any instance not present in the current call's value map (real PDH raises
    PDH_INVALID_DATA for a rate counter on its first CollectQueryData after AddCounter —
    see GpuMonitor._utilization_percent's docstring); `values_by_call[i]` is consulted on
    the (i+1)-th CollectQueryData call."""
    collect_count = {"n": 0}
    added_counters: dict[object, str] = {}

    def OpenQuery():
        return object()

    def EnumObjectItems(_a, _b, _obj, _detail):
        return None, list(engine_instances)

    def MakeCounterPath(parts):
        return parts[2]  # instance name, per (None, obj, instance, None, -1, counter)

    def AddCounter(_query, path):
        counter_id = object()
        added_counters[counter_id] = path
        return counter_id

    def CollectQueryData(_query):
        collect_count["n"] += 1

    def GetFormattedCounterValue(counter_id, _fmt):
        instance = added_counters[counter_id]
        values = values_by_call[min(collect_count["n"] - 1, len(values_by_call) - 1)]
        if instance not in values:
            raise OSError("PDH_INVALID_DATA (simulated: counter still priming)")
        return (None, values[instance])

    def CloseQuery(_query):
        pass

    return types.SimpleNamespace(
        OpenQuery=OpenQuery, EnumObjectItems=EnumObjectItems, MakeCounterPath=MakeCounterPath,
        AddCounter=AddCounter, CollectQueryData=CollectQueryData,
        GetFormattedCounterValue=GetFormattedCounterValue, CloseQuery=CloseQuery,
        PERF_DETAIL_WIZARD=0, PDH_FMT_DOUBLE=1, PDH_FMT_LARGE=2,
    )


def test_gpu_utilization_first_sample_is_unavailable_not_zero(monkeypatch):
    """V1.4.1 PATCH 04: verified on real hardware that every single freshly-added PDH
    "Utilization Percentage" counter raises PDH_INVALID_DATA on its first
    CollectQueryData (it's a rate counter, needs two samples over real elapsed time) —
    the pre-existing code treated every failure as "skip it" and returned 0.0 (sum of
    nothing), indistinguishable from a genuinely idle GPU. Must return None instead."""
    fake_pdh = _fake_win32pdh(
        engine_instances=["proc1_engtype_3D", "proc2_engtype_3D"],
        values_by_call=[{}, {"proc1_engtype_3D": 15.0, "proc2_engtype_3D": 7.0}],
    )
    monkeypatch.setitem(sys.modules, "win32pdh", fake_pdh)

    monitor = GpuMonitor()
    first = monitor._utilization_percent()
    second = monitor._utilization_percent()

    assert first is None
    assert second == pytest.approx(22.0)


def test_gpu_utilization_clamped_to_100_when_multiple_adapters_sum_over(monkeypatch):
    """A genuine aggregate across 2 simultaneously-busy adapters can exceed 100% (see
    GpuMonitor's class docstring on multi-adapter summing) — clamped for display
    sanity, since a single "GPU usage" figure implies a 0-100 scale to the reader."""
    fake_pdh = _fake_win32pdh(
        engine_instances=["gpu0_engtype_3D", "gpu1_engtype_3D"],
        values_by_call=[{"gpu0_engtype_3D": 60.0, "gpu1_engtype_3D": 70.0}],
    )
    monkeypatch.setitem(sys.modules, "win32pdh", fake_pdh)

    assert GpuMonitor()._utilization_percent() == 100.0


def test_gpu_utilization_rejects_non_finite_values(monkeypatch):
    fake_pdh = _fake_win32pdh(
        engine_instances=["gpu0_engtype_3D"],
        values_by_call=[{"gpu0_engtype_3D": float("nan")}],
    )
    monkeypatch.setitem(sys.modules, "win32pdh", fake_pdh)

    assert GpuMonitor()._utilization_percent() is None


def _fake_winreg(adapters: dict[str, int | None]):
    """adapters: {subkey_name: qwMemorySize_or_None} — None simulates a subkey with no
    HardwareInformation.qwMemorySize value at all (e.g. an integrated GPU, per the real
    Intel UHD 770 finding in V1.4.1 PATCH 04)."""

    class _FakeKey:
        def __init__(self, subkeys=(), qw_memory_size=None):
            self.subkeys = subkeys
            self.qw_memory_size = qw_memory_size

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    hklm = object()
    class_key = _FakeKey(subkeys=tuple(adapters.keys()))
    adapter_keys = {name: _FakeKey(qw_memory_size=size) for name, size in adapters.items()}

    def OpenKey(parent, name):
        if parent is hklm:
            return class_key
        return adapter_keys[name]

    def EnumKey(key, index):
        if index >= len(key.subkeys):
            raise OSError("no more subkeys")
        return key.subkeys[index]

    def QueryValueEx(key, value_name):
        assert value_name == "HardwareInformation.qwMemorySize"
        if key.qw_memory_size is None:
            raise OSError("value not present")
        return (key.qw_memory_size, 1)

    return types.SimpleNamespace(HKEY_LOCAL_MACHINE=hklm, OpenKey=OpenKey, EnumKey=EnumKey, QueryValueEx=QueryValueEx)


def test_total_vram_bytes_above_4gb_not_truncated(monkeypatch):
    """V1.4.1 PATCH 04: re-verifies the V1.4 finding that Win32_VideoController.AdapterRAM
    truncates at 4GB (32-bit DWORD) — the registry path must return the real, untruncated
    value. 12868124672 bytes (~11.98 GB) is the exact value read from this project's own
    real AMD RX 6750 XT during the PATCH 04 hardware audit."""
    from system_monitor.gpu import _total_vram_bytes

    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg({"0000": 12868124672}))
    assert _total_vram_bytes() == 12868124672


def test_total_vram_bytes_at_or_below_4gb(monkeypatch):
    from system_monitor.gpu import _total_vram_bytes

    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg({"0000": 2 * 1024**3}))
    assert _total_vram_bytes() == 2 * 1024**3


def test_total_vram_bytes_registry_key_missing_returns_none(monkeypatch):
    def _raise_open_key(*_a, **_k):
        raise OSError("class key not present on this machine")

    fake = types.SimpleNamespace(
        HKEY_LOCAL_MACHINE=object(), OpenKey=_raise_open_key,
        EnumKey=lambda *a: (_ for _ in ()).throw(OSError()),
        QueryValueEx=lambda *a: (_ for _ in ()).throw(OSError()),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)

    from system_monitor.gpu import _total_vram_bytes

    assert _total_vram_bytes() is None


def test_total_vram_bytes_igpu_without_memory_size_key_is_skipped_not_summed(monkeypatch):
    """Real hardware finding (V1.4.1 PATCH 04): the Intel iGPU's registry subkey exists
    but has no HardwareInformation.qwMemorySize value at all (it shares system RAM
    rather than having fixed dedicated VRAM) — must contribute nothing to the total,
    never crash, and never be reported as "0 GB" (which would imply a real, empty GPU)."""
    from system_monitor.gpu import _total_vram_bytes

    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg({"0000": 12868124672, "0001": None}))
    assert _total_vram_bytes() == 12868124672


def test_total_vram_bytes_sums_multiple_adapters_when_both_present(monkeypatch):
    """If two adapters BOTH happen to expose the registry key, the current, documented
    (not hidden) behavior is to sum them — see GpuMonitor's class docstring."""
    from system_monitor.gpu import _total_vram_bytes

    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg({"0000": 8 * 1024**3, "0001": 4 * 1024**3}))
    assert _total_vram_bytes() == 12 * 1024**3


# --- 8-9: process collection / access denied ------------------------------------------------


class _FakeProcess:
    def __init__(self, pid, name, cpu=1.0, memory_percent=2.0, rss=1024, status="running", exe="C:\\fake.exe", ppid=4):
        self.info = {
            "pid": pid, "name": name, "memory_percent": memory_percent,
            "memory_info": types.SimpleNamespace(rss=rss), "status": status, "exe": exe, "ppid": ppid,
        }
        self._pid = pid
        self._cpu = cpu
        self._create_time = 100.0

    def cpu_percent(self, _interval):
        return self._cpu

    def create_time(self):
        return self._create_time


def test_process_collector_returns_sorted_snapshot(monkeypatch):
    """cpu_percent needs priming (same psutil semantics as the real library — see
    system_monitor/processes.py's docstring): a brand-new collector's first sample()
    always reports 0.0 for every process, meaningful sorting only starts from the
    second call — so this calls sample() twice, exactly like a real persistent
    SystemMonitorService would across two polls."""
    procs = [_FakeProcess(1, "a", cpu=5.0), _FakeProcess(2, "b", cpu=50.0)]
    monkeypatch.setattr("system_monitor.processes.psutil.process_iter", lambda attrs: iter(procs))
    collector = ProcessCollector()
    collector.sample()  # priming call

    snapshot = collector.sample()

    assert [p.pid for p in snapshot.processes] == [2, 1]  # sorted by cpu desc
    assert snapshot.processes[0].name == "b"


def test_process_collector_skips_access_denied_processes(monkeypatch):
    import psutil as real_psutil

    class _DeniedProcess:
        info = {"pid": 99}

        def cpu_percent(self, _interval):
            raise real_psutil.AccessDenied(pid=99)

        def create_time(self):
            raise real_psutil.AccessDenied(pid=99)

    good = _FakeProcess(1, "ok")
    monkeypatch.setattr("system_monitor.processes.psutil.process_iter", lambda attrs: iter([_DeniedProcess(), good]))

    snapshot = ProcessCollector().sample()

    assert [p.pid for p in snapshot.processes] == [1]


def test_process_collector_skips_processes_that_exited(monkeypatch):
    """Simulates a process exiting in the gap between psutil.process_iter() listing it
    and this collector priming cpu_percent() on it — a real, well-known psutil race,
    not hypothetical. For a never-before-seen pid, cpu_percent() is the first call made
    on it (see system_monitor/processes.py), so that's where NoSuchProcess is raised."""
    import psutil as real_psutil

    class _GoneProcess:
        info = {"pid": 100}

        def cpu_percent(self, _interval):
            raise real_psutil.NoSuchProcess(pid=100)

    monkeypatch.setattr("system_monitor.processes.psutil.process_iter", lambda attrs: iter([_GoneProcess()]))

    snapshot = ProcessCollector().sample()

    assert snapshot.processes == ()


def test_process_collector_respects_limit(monkeypatch):
    procs = [_FakeProcess(i, f"p{i}", cpu=float(i)) for i in range(10)]
    monkeypatch.setattr("system_monitor.processes.psutil.process_iter", lambda attrs: iter(procs))

    snapshot = ProcessCollector().sample(limit=3)

    assert len(snapshot.processes) == 3


def test_process_collector_default_limit_does_not_drop_low_cpu_processes(monkeypatch):
    """V1.5 Security Center regression guard: a near-idle process must not be dropped
    from the default snapshot just because 100+ other processes use more CPU — the
    previous default of 100 made this silently invisible to security/engine.py's
    process-based analysis (a real finding from hardware validation, not hypothetical:
    a deliberately quiet test process never appeared in a real snapshot on a machine
    with 100+ processes running)."""
    procs = [_FakeProcess(i, f"p{i}", cpu=float(i % 5)) for i in range(150)]  # 150 > old default of 100
    monkeypatch.setattr("system_monitor.processes.psutil.process_iter", lambda attrs: iter(procs))

    snapshot = ProcessCollector().sample()  # default limit, not explicitly overridden

    assert len(snapshot.processes) == 150
    assert {p.pid for p in snapshot.processes} == {p for p in range(150)}


# --- 10: limited history --------------------------------------------------------------------


def test_service_history_is_bounded(monkeypatch):
    monkeypatch.setattr("system_monitor.service.collect_cpu_metrics", lambda tracker: CpuMetrics(percent=1.0))
    monkeypatch.setattr("system_monitor.service.collect_memory_metrics", lambda: MemoryMetrics(percent=1.0, used_bytes=1, available_bytes=1, total_bytes=2))
    monkeypatch.setattr("system_monitor.service.collect_disk_metrics", lambda *a, **k: DiskMetrics(path="C:\\", percent=1.0, used_bytes=1, free_bytes=1, total_bytes=2))

    service = SystemMonitorService(event_bus=EventBus())
    service._gpu_monitor.sample = lambda: GpuMetrics(available=False, unavailable_reason="x")
    service._network_monitor.sample = lambda: NetworkMetrics(upload_bytes_per_sec=None, download_bytes_per_sec=None, bytes_sent_total=0, bytes_recv_total=0)
    service._disk_io_monitor.sample = lambda: (None, None)

    from system_monitor.service import HISTORY_LENGTH

    for _ in range(HISTORY_LENGTH + 20):
        service._collect_metrics()

    assert len(service.cpu_history) == HISTORY_LENGTH


# --- 11-15: service start/stop/duplicate/error/interval --------------------------------------


def _quiet_service(event_bus: EventBus, **kwargs) -> SystemMonitorService:
    service = SystemMonitorService(event_bus=event_bus, **kwargs)
    return service


def test_monitor_start_publishes_started_event(monkeypatch):
    _mock_fast_process_collection(monkeypatch)
    bus = EventBus()
    seen = []
    bus.subscribe(SYSTEM_MONITOR_STARTED, seen.append)
    service = _quiet_service(bus, metrics_interval=5.0, process_interval=5.0)

    service.start()
    try:
        assert service.is_running is True
        assert len(seen) == 1
    finally:
        service.stop()


def test_monitor_stop_publishes_stopped_event_and_joins_thread(monkeypatch):
    _mock_fast_process_collection(monkeypatch)
    bus = EventBus()
    seen = []
    bus.subscribe(SYSTEM_MONITOR_STOPPED, seen.append)
    service = _quiet_service(bus, metrics_interval=0.05, process_interval=0.05)
    service.start()

    service.stop()

    assert service.is_running is False
    assert len(seen) == 1


def test_duplicate_start_does_not_create_a_second_thread(monkeypatch):
    _mock_fast_process_collection(monkeypatch)
    bus = EventBus()
    started_events = []
    bus.subscribe(SYSTEM_MONITOR_STARTED, started_events.append)
    service = _quiet_service(bus, metrics_interval=1.0, process_interval=1.0)

    service.start()
    first_thread = service._thread
    service.start()
    second_thread = service._thread

    try:
        assert first_thread is second_thread
        assert len(started_events) == 1
    finally:
        service.stop()


def test_stop_without_start_is_a_safe_no_op():
    service = _quiet_service(EventBus())
    service.stop()  # must not raise
    assert service.is_running is False


def test_monitor_error_handling_publishes_error_event_and_keeps_running(monkeypatch):
    bus = EventBus()
    errors = []
    bus.subscribe(SYSTEM_MONITOR_ERROR, errors.append)

    def _raise(tracker):
        raise RuntimeError("simulated collection failure")

    monkeypatch.setattr("system_monitor.service.collect_cpu_metrics", _raise)
    _mock_fast_process_collection(monkeypatch)
    service = _quiet_service(bus, metrics_interval=0.05, process_interval=10.0)

    service.start()
    time.sleep(0.2)
    service.stop()

    assert len(errors) >= 1
    assert errors[0]["stage"] == "metrics"


def test_metrics_updated_event_carries_a_real_snapshot(monkeypatch):
    _mock_fast_process_collection(monkeypatch)
    monkeypatch.setattr("system_monitor.service.collect_cpu_metrics", lambda tracker: CpuMetrics(percent=7.0))
    monkeypatch.setattr("system_monitor.service.collect_memory_metrics", lambda: MemoryMetrics(percent=1.0, used_bytes=1, available_bytes=1, total_bytes=2))
    monkeypatch.setattr("system_monitor.service.collect_disk_metrics", lambda *a, **k: DiskMetrics(path="C:\\", percent=1.0, used_bytes=1, free_bytes=1, total_bytes=2))

    bus = EventBus()
    received = []
    bus.subscribe(SYSTEM_METRICS_UPDATED, received.append)
    bus.subscribe(NETWORK_METRICS_UPDATED, lambda d: None)

    service = _quiet_service(bus, metrics_interval=0.05, process_interval=10.0)
    service._gpu_monitor.sample = lambda: GpuMetrics(available=False, unavailable_reason="x")
    service._network_monitor.sample = lambda: NetworkMetrics(upload_bytes_per_sec=None, download_bytes_per_sec=None, bytes_sent_total=0, bytes_recv_total=0)
    service._disk_io_monitor.sample = lambda: (None, None)

    service.start()
    time.sleep(0.2)
    service.stop()

    assert received
    assert received[0]["snapshot"].cpu.percent == 7.0


def test_process_list_updated_event_published(monkeypatch):
    monkeypatch.setattr(
        "system_monitor.processes.ProcessCollector.sample",
        lambda self, limit=100: ProcessSnapshot(timestamp=1.0, processes=()),
    )
    bus = EventBus()
    received = []
    bus.subscribe(PROCESS_LIST_UPDATED, received.append)
    service = _quiet_service(bus, metrics_interval=10.0, process_interval=0.05)
    service._gpu_monitor.sample = lambda: GpuMetrics(available=False, unavailable_reason="x")

    service.start()
    time.sleep(0.2)
    service.stop()

    assert received
    assert received[0]["snapshot"].processes == ()


# --- 18: shutdown releases resources ----------------------------------------------------------


def test_stop_closes_the_gpu_monitor(monkeypatch):
    _mock_fast_process_collection(monkeypatch)
    closed = []
    service = _quiet_service(EventBus(), metrics_interval=0.05, process_interval=10.0)
    monkeypatch.setattr(service._gpu_monitor, "close", lambda: closed.append(True))
    service._gpu_monitor.sample = lambda: GpuMetrics(available=False, unavailable_reason="x")

    service.start()
    service.stop()

    assert closed == [True]


def test_stop_leaves_no_lingering_thread(monkeypatch):
    """service.stop() already blocks on thread.join(timeout=THREAD_JOIN_TIMEOUT_SECONDS)
    — the real correctness guarantee. This test's first assertion polls for up to one
    more second on top of that before failing, to absorb OS scheduling noise without
    masking a genuine stuck/leaked thread: a thread that's actually leaked stays alive
    well past both the join and this extra second. Real process collection is mocked
    out (see _mock_fast_process_collection) since it was the actual root cause of
    flakiness under full-suite load — an unmocked first cycle could block shutdown for
    up to ~1s+ (and, observed once during real hardware validation, occasionally much
    longer — see the PATCH 03 report)."""
    _mock_fast_process_collection(monkeypatch)
    service = _quiet_service(EventBus(), metrics_interval=0.05, process_interval=10.0)
    service._gpu_monitor.sample = lambda: GpuMetrics(available=False, unavailable_reason="x")
    service.start()
    thread = service._thread

    service.stop()

    deadline = time.monotonic() + 1.0
    while thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not thread.is_alive()
    assert threading.active_count() >= 1  # sanity: the test process itself is still alive


# --- V1.4.1 PATCH 03: lifecycle concurrency -------------------------------------------------------


def test_start_after_stop_restarts_cleanly(monkeypatch):
    _mock_fast_process_collection(monkeypatch)
    bus = EventBus()
    started_events = []
    bus.subscribe(SYSTEM_MONITOR_STARTED, started_events.append)
    service = _quiet_service(bus, metrics_interval=0.05, process_interval=10.0)
    service._gpu_monitor.sample = lambda: GpuMetrics(available=False, unavailable_reason="x")

    service.start()
    first_thread = service._thread
    service.stop()
    service.start()
    second_thread = service._thread

    try:
        assert service.is_running is True
        assert second_thread is not first_thread
        assert not first_thread.is_alive()
        assert len(started_events) == 2
    finally:
        service.stop()


def test_start_holds_the_lifecycle_lock_while_creating_the_thread(monkeypatch):
    """Deterministic (white-box) regression guard for the concurrency fix: a black-box
    Barrier-based race (see test_concurrent_start_calls_create_only_one_thread below)
    turned out to pass even against a version with the lock removed — the GIL and
    thread-creation overhead make the exact interleaving too hard to force reliably in
    this environment, so it's not a trustworthy regression guard on its own. This test
    instead directly verifies the actual fix: the lock is held for the full
    check-then-act sequence, by spying on threading.Thread.start() (called from inside
    that sequence) and asserting the lock is already acquired at that point."""
    _mock_fast_process_collection(monkeypatch)
    service = _quiet_service(EventBus(), metrics_interval=10.0, process_interval=10.0)
    service._gpu_monitor.sample = lambda: GpuMetrics(available=False, unavailable_reason="x")

    observed_locked = []
    original_thread_start = threading.Thread.start

    def spying_start(self):
        observed_locked.append(service._lifecycle_lock.locked())
        return original_thread_start(self)

    monkeypatch.setattr(threading.Thread, "start", spying_start)
    try:
        service.start()
        assert observed_locked == [True]
    finally:
        service.stop()


def test_concurrent_start_calls_create_only_one_thread(monkeypatch):
    """Two real threads call .start() at the exact same instant (synchronized with a
    Barrier, not a sleep). Best-effort robustness check (not the primary regression
    guard — see the deterministic test above): confirms no exception/deadlock and a
    consistent end state under concurrent calls either way the race resolves."""
    _mock_fast_process_collection(monkeypatch)
    bus = EventBus()
    started_events = []
    bus.subscribe(SYSTEM_MONITOR_STARTED, started_events.append)
    service = _quiet_service(bus, metrics_interval=1.0, process_interval=10.0)
    service._gpu_monitor.sample = lambda: GpuMetrics(available=False, unavailable_reason="x")

    # Baseline BEFORE this test's own threads exist — a leftover "steve-system-monitor"
    # thread from a different, unrelated test elsewhere in the suite (e.g. one whose
    # cleanup hasn't finished yet) must not produce a false failure here; only the
    # DELTA this test itself creates matters.
    baseline_monitor_thread_count = len([t for t in threading.enumerate() if t.name == "steve-system-monitor"])

    barrier = threading.Barrier(2)

    def call_start():
        barrier.wait(timeout=5)
        service.start()

    callers = [threading.Thread(target=call_start) for _ in range(2)]
    for t in callers:
        t.start()
    for t in callers:
        t.join(timeout=5)

    try:
        assert len(started_events) == 1
        assert service.is_running is True
        # exactly one NEW steve-system-monitor thread exists because of this test, not two
        monitor_threads = [t for t in threading.enumerate() if t.name == "steve-system-monitor"]
        assert len(monitor_threads) - baseline_monitor_thread_count == 1
    finally:
        service.stop()


def test_concurrent_stop_calls_are_safe(monkeypatch):
    _mock_fast_process_collection(monkeypatch)
    bus = EventBus()
    stopped_events = []
    bus.subscribe(SYSTEM_MONITOR_STOPPED, stopped_events.append)
    service = _quiet_service(bus, metrics_interval=0.05, process_interval=10.0)
    service._gpu_monitor.sample = lambda: GpuMetrics(available=False, unavailable_reason="x")
    service.start()

    barrier = threading.Barrier(2)

    def call_stop():
        barrier.wait(timeout=5)
        service.stop()

    callers = [threading.Thread(target=call_stop) for _ in range(2)]
    for t in callers:
        t.start()
    for t in callers:
        t.join(timeout=5)

    assert service.is_running is False
    assert len(stopped_events) == 1  # the second concurrent stop() saw is_running=False and no-opped


def test_shutdown_waits_for_in_flight_collection_to_finish(monkeypatch):
    """stop() called while a collection cycle is actively running must wait for that
    cycle to finish (thread.join()), not truncate it — synchronized with real
    threading.Event objects, never an arbitrary sleep, so the timing is exact."""
    collection_started = threading.Event()
    release_collection = threading.Event()
    collection_finished = threading.Event()

    def _slow_cpu_metrics(tracker):
        collection_started.set()
        assert release_collection.wait(timeout=5), "test setup did not release in time"
        collection_finished.set()
        return CpuMetrics(percent=1.0)

    monkeypatch.setattr("system_monitor.service.collect_cpu_metrics", _slow_cpu_metrics)
    monkeypatch.setattr("system_monitor.service.collect_memory_metrics", lambda: MemoryMetrics(percent=1.0, used_bytes=1, available_bytes=1, total_bytes=2))
    monkeypatch.setattr("system_monitor.service.collect_disk_metrics", lambda *a, **k: DiskMetrics(path="C:\\", percent=1.0, used_bytes=1, free_bytes=1, total_bytes=2))
    _mock_fast_process_collection(monkeypatch)

    service = _quiet_service(EventBus(), metrics_interval=10.0, process_interval=10.0)
    service._gpu_monitor.sample = lambda: GpuMetrics(available=False, unavailable_reason="x")
    service._network_monitor.sample = lambda: NetworkMetrics(upload_bytes_per_sec=None, download_bytes_per_sec=None, bytes_sent_total=0, bytes_recv_total=0)
    service._disk_io_monitor.sample = lambda: (None, None)

    service.start()
    assert collection_started.wait(timeout=5), "collection never started"

    stop_finished = threading.Event()

    def call_stop():
        service.stop()
        stop_finished.set()

    stopper = threading.Thread(target=call_stop)
    stopper.start()

    # stop() must be blocked inside thread.join(), waiting — it must NOT have finished
    # yet, because the collection cycle it's waiting on hasn't been released.
    assert not stop_finished.wait(timeout=0.3)
    assert not collection_finished.is_set()

    release_collection.set()
    stopper.join(timeout=5)

    assert stop_finished.is_set()
    assert collection_finished.is_set()  # the in-flight cycle ran to completion, not truncated
    assert service.is_running is False


# --- 19: no external network transmission ------------------------------------------------------


def test_system_monitor_source_never_imports_networking_libraries():
    """Static guard: system_monitor/ must stay purely local — psutil/win32 APIs only,
    never requests/socket/urllib. A grep-level check rather than a runtime spy, since
    there's no network call anywhere in the package to intercept in the first place."""
    import pathlib

    package_dir = pathlib.Path(__file__).resolve().parent.parent / "system_monitor"
    forbidden = ("import requests", "import socket", "import urllib", "import http.client")
    for path in package_dir.glob("*.py"):
        content = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in content, f"{path} unexpectedly references '{token}'"
