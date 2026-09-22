"""V1.4.1 PATCH 04, item 24 (dashboard rendering of N/A): unit tests for
ui/desktop/dashboard/formatting.py's None -> UNAVAILABLE_LABEL contract. This module was
already correct before this patch (V1.4) but had no dedicated test file — this closes
that gap without touching any GUI/Tkinter machinery, since the formatters are plain
functions."""
from __future__ import annotations

from ui.desktop.dashboard.formatting import (
    UNAVAILABLE_LABEL,
    format_bytes,
    format_celsius,
    format_mhz,
    format_percent,
)


def test_format_percent_none_is_unavailable_label():
    assert format_percent(None) == UNAVAILABLE_LABEL


def test_format_percent_real_value():
    assert format_percent(42.567) == "42.6%"


def test_format_celsius_none_is_unavailable_label():
    """Directly exercises the exact rendering path CPU/GPU temperature go through now
    that both are always None by design (V1.4.1 PATCH 04) — the Dashboard must show
    "Indisponível", never "0.0 °C" or any other fabricated-looking number."""
    assert format_celsius(None) == UNAVAILABLE_LABEL


def test_format_celsius_real_value():
    assert format_celsius(45.0) == "45.0 °C"


def test_format_mhz_none_is_unavailable_label():
    assert format_mhz(None) == UNAVAILABLE_LABEL


def test_format_mhz_below_1000_shows_mhz():
    assert format_mhz(800.0) == "800 MHz"


def test_format_mhz_at_or_above_1000_shows_ghz():
    assert format_mhz(3700.0) == "3.70 GHz"


def test_format_bytes_none_is_unavailable_label():
    assert format_bytes(None) == UNAVAILABLE_LABEL


def test_format_bytes_scales_units():
    assert format_bytes(500) == "500 B"
    assert format_bytes(1536) == "1.5 KB"
    assert format_bytes(12868124672) == "12.0 GB"


def test_format_bytes_per_second_suffix():
    assert format_bytes(1024, per_second=True) == "1.0 KB/s"
    assert format_bytes(None, per_second=True) == UNAVAILABLE_LABEL
