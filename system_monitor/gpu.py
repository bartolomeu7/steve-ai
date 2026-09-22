"""Real, cross-vendor (AMD/Intel/NVIDIA) GPU metrics on Windows — deliberately NOT using
a vendor SDK (nvidia-ml-py/pynvml, ADL, ...): those only cover one vendor each, and this
project explicitly must not assume a specific GPU vendor. Instead this uses two Windows
mechanisms already reachable through pywin32 (an existing dependency — see
ai/providers/ollama_provider.py's SAPI5 use via the same package), so no new dependency
was needed:

- WMI (`Win32_VideoController`) for adapter names — standard, cheap, cross-vendor.
- Performance Counters ("GPU Engine", "GPU Adapter Memory") for utilization/VRAM — the
  exact same mechanism Windows' own Task Manager uses since Windows 10 1803, so it works
  for any adapter with a WDDM driver, not just one vendor.

GPU *temperature* has no equivalent: Windows has no built-in, cross-vendor way to read
it (only vendor SDKs do — NVML for NVIDIA, ADL for AMD, igcl for Intel), so this always
reports it as unavailable rather than adding three heavy vendor SDKs for one field.

"Utilization Percentage" is a rate counter — like `psutil.cpu_percent()`, it needs two
samples over real elapsed time to mean anything; the first read after opening a query is
unreliable. `GpuMonitor` keeps its counter query open across calls (primed once, read on
every `sample()` after) instead of opening/sampling twice per call, which would mean
blocking for ~1s inside every single poll — see SystemMonitorService, which already
polls every ~1s anyway, so the natural gap between polls is what primes the rate.
"""
from __future__ import annotations

import logging
import math
import sys

from system_monitor.models import GpuAdapter, GpuMetrics

logger = logging.getLogger("steve.system_monitor.gpu")

_NO_GPU_REASON = "Nenhum adaptador de vídeo encontrado."
_NON_WINDOWS_REASON = "Monitoramento de GPU só é suportado no Windows."
_NO_PDH_REASON = "Contadores de desempenho do Windows indisponíveis nesta máquina."


class GpuMonitor:
    """Stateful on purpose (see module docstring) — one instance lives for the lifetime
    of SystemMonitorService; call `close()` on shutdown to release the open PDH query.

    Multi-adapter systems (V1.4.1 PATCH 04 finding, real hardware: AMD RX 6750 XT
    dedicated + Intel UHD 770 integrated): `utilization_percent` and
    `memory_used_bytes` are each the SUM across every "engtype_3D"/"Dedicated Usage"
    PDH instance found on the system, not a single chosen adapter's value — this is a
    deliberate simplification (see PATCH 04 spec's explicit "não implementar suporte
    complexo a múltiplas GPUs"), not an oversight. In the common case this is harmless
    or even correct-looking (an idle iGPU reports 0, so the sum equals the active
    dGPU's real value, as verified on this dev machine: 2 of 3 "GPU Adapter Memory"
    instances read 0 MB, the third read the AMD card's real ~2.5 GB), but if multiple
    adapters are genuinely active at once (e.g. iGPU doing video decode while the dGPU
    renders), this number combines both rather than reporting either one individually.
    `memory_total_bytes` does NOT have this problem in practice: it comes from
    `_total_vram_bytes()`, which only finds a registry entry for adapters that expose
    `HardwareInformation.qwMemorySize` (typically just the dedicated GPU — integrated
    adapters usually have no such key, confirmed empirically: the Intel iGPU here has
    none), so summing "when present" naturally reduces to just the dGPU's real total in
    the common single-dGPU-plus-iGPU case."""

    def __init__(self):
        self._query = None
        self._tracked_instances: set[str] = set()
        self._counters: dict[str, object] = {}
        self._pdh_unavailable = False
        #: Adapter names and total VRAM are static hardware facts (V1.4.1 PATCH 04:
        #: "informações estáticas... podem ser cacheadas") — a GPU doesn't appear or
        #: disappear mid-session, unlike utilization/used-VRAM which genuinely change
        #: every poll and must never be cached. Before this fix, every ~1s sample()
        #: call re-ran a WMI COM query (_adapter_names) and a registry scan
        #: (_total_vram_bytes) for values that can never change once known.
        self._adapter_names_cache: list[str] | None = None
        self._vram_total_queried = False
        self._vram_total_cache: int | None = None

    def sample(self) -> GpuMetrics:
        if sys.platform != "win32":
            return GpuMetrics(available=False, unavailable_reason=_NON_WINDOWS_REASON)

        if not self._adapter_names_cache:
            # Falsy (None or empty) means "not yet successfully cached" -- keep retrying
            # every cycle until we get a real answer (a transient WMI/COM hiccup early
            # in the process's life shouldn't be cached as "no GPU" forever), then never
            # query again once we do.
            self._adapter_names_cache = self._adapter_names()
        adapters = self._adapter_names_cache
        if not adapters:
            return GpuMetrics(available=False, unavailable_reason=_NO_GPU_REASON)

        utilization = self._utilization_percent()
        used_bytes, total_bytes = self._memory_bytes()

        return GpuMetrics(
            available=True,
            adapters=tuple(GpuAdapter(name=name) for name in adapters),
            utilization_percent=utilization,
            temperature_celsius=None,
            memory_used_bytes=used_bytes,
            memory_total_bytes=total_bytes,
        )

    def close(self) -> None:
        if self._query is not None:
            try:
                import win32pdh

                win32pdh.CloseQuery(self._query)
            except Exception:
                logger.debug("Falha ao fechar a query PDH de GPU (ignorado).", exc_info=True)
            self._query = None
            self._tracked_instances.clear()
            self._counters.clear()

    # --- adapter names -----------------------------------------------------------------
    @staticmethod
    def _adapter_names() -> list[str]:
        try:
            import win32com.client
        except ImportError:
            return []
        try:
            wmi = win32com.client.GetObject("winmgmts:")
            return [row.Name for row in wmi.ExecQuery("SELECT Name FROM Win32_VideoController") if row.Name]
        except Exception:
            logger.debug("Não foi possível listar adaptadores de vídeo via WMI.", exc_info=True)
            return []

    # --- utilization (persistent query, rate counter) -----------------------------------
    def _utilization_percent(self) -> float | None:
        if self._pdh_unavailable:
            return None
        try:
            import win32pdh
        except ImportError:
            self._pdh_unavailable = True
            return None

        try:
            if self._query is None:
                self._query = win32pdh.OpenQuery()
            _, instances = win32pdh.EnumObjectItems(None, None, "GPU Engine", win32pdh.PERF_DETAIL_WIZARD)
        except Exception:
            logger.debug(_NO_PDH_REASON, exc_info=True)
            self._pdh_unavailable = True
            return None

        engine_instances = {i for i in instances if i.endswith("engtype_3D")}
        for instance in engine_instances - self._tracked_instances:
            try:
                path = win32pdh.MakeCounterPath((None, "GPU Engine", instance, None, -1, "Utilization Percentage"))
                self._counters[instance] = win32pdh.AddCounter(self._query, path)
                self._tracked_instances.add(instance)
            except Exception:
                logger.debug("Falha ao adicionar contador para %s (ignorado).", instance, exc_info=True)

        if not self._counters:
            return None

        try:
            win32pdh.CollectQueryData(self._query)
        except Exception:
            logger.debug("Falha ao coletar dados de utilização de GPU.", exc_info=True)
            return None

        total = 0.0
        successes = 0
        for counter in list(self._counters.values()):
            try:
                _, value = win32pdh.GetFormattedCounterValue(counter, win32pdh.PDH_FMT_DOUBLE)
                total += value
                successes += 1
            except Exception:
                # PDH_INVALID_DATA is the EXPECTED, guaranteed outcome for a counter on
                # its first CollectQueryData right after AddCounter — "Utilization
                # Percentage" is a rate counter needing two samples over real elapsed
                # time, just like this same file's docstring already says. Verified on
                # real hardware (V1.4.1 PATCH 04): every single freshly-added counter
                # raises here on the very first call, only succeeding from the second
                # CollectQueryData onward. Also covers the pre-existing case of a
                # genuinely stale (process exited) instance — same "skip it" handling.
                continue

        if successes == 0:
            # Every counter we tried was still priming this cycle (typically: this is
            # the very first sample() call ever, so ALL instances are brand new) — we
            # have zero real data points, not a genuine 0% reading. Returning 0.0 here
            # would look exactly like "idle GPU" when it actually means "no data yet",
            # which is the deceptive first-sample bug PATCH 04 exists to fix.
            return None

        if not math.isfinite(total):
            logger.warning("Valor de utilização de GPU não numérico (%r) descartado.", total)
            return None

        # A genuine aggregate across multiple simultaneously-busy adapters can exceed
        # 100% (see the class docstring) — clamped for display sanity, since a single
        # "GPU usage" figure implies a 0-100 scale to whoever reads it.
        return min(round(total, 1), 100.0)

    # --- VRAM (fresh one-shot query, gauge counter — no priming needed) -----------------
    def _memory_bytes(self) -> tuple[int | None, int | None]:
        used_bytes = None
        try:
            import win32pdh
        except ImportError:
            win32pdh = None  # VRAM *total* below is registry-based, not PDH-based, and
            # must still be attempted even when PDH itself is unavailable — a pre-
            # existing latent bug (V1.4.1 PATCH 04 finding) had the whole function bail
            # out here, silently losing a real, obtainable total for no real reason.

        if win32pdh is not None:
            try:
                query = win32pdh.OpenQuery()
                try:
                    _, instances = win32pdh.EnumObjectItems(None, None, "GPU Adapter Memory", win32pdh.PERF_DETAIL_WIZARD)
                    counters = []
                    for instance in set(instances):
                        try:
                            path = win32pdh.MakeCounterPath((None, "GPU Adapter Memory", instance, None, -1, "Dedicated Usage"))
                            counters.append(win32pdh.AddCounter(query, path))
                        except Exception:
                            continue
                    if counters:
                        win32pdh.CollectQueryData(query)
                        used_bytes = 0
                        for counter in counters:
                            try:
                                _, value = win32pdh.GetFormattedCounterValue(counter, win32pdh.PDH_FMT_LARGE)
                                used_bytes += value
                            except Exception:
                                continue
                finally:
                    win32pdh.CloseQuery(query)
            except Exception:
                logger.debug("VRAM usada indisponível.", exc_info=True)

        if not self._vram_total_queried:
            # Registry-based, unlike WMI/COM this has no transient-availability concern
            # (it's a plain registry read) — the key either exists for this hardware or
            # it never will for the life of this process, so caching a None result here
            # is correct, not just an optimization.
            self._vram_total_cache = _total_vram_bytes()
            self._vram_total_queried = True
        return used_bytes, self._vram_total_cache


def _total_vram_bytes() -> int | None:
    """Best-effort, real value from the driver's own registry entry — NOT WMI's
    Win32_VideoController.AdapterRAM, which is a documented Windows limitation: that
    field is a 32-bit DWORD and silently wraps/truncates for any card with more than 4GB
    of VRAM (verified on this project's own dev hardware: an AMD card with 12GB reports
    a wrapped ~4GB via WMI, but the correct ~12GB via this registry key). Returns None
    (not a wrong number) if the key isn't present — e.g. most integrated GPUs, which
    share system RAM rather than having fixed dedicated VRAM."""
    try:
        import winreg
    except ImportError:
        return None
    base = r"SYSTEM\ControlSet001\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    total = 0
    found_any = False
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as class_key:
            index = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(class_key, index)
                except OSError:
                    break
                index += 1
                if not subkey_name.isdigit():
                    continue
                try:
                    with winreg.OpenKey(class_key, subkey_name) as adapter_key:
                        size = winreg.QueryValueEx(adapter_key, "HardwareInformation.qwMemorySize")[0]
                        total += int(size)
                        found_any = True
                except OSError:
                    continue
    except OSError:
        return None
    return total if found_any else None
