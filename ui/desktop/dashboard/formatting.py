"""Small, shared display-formatting helpers for the Dashboard tabs — kept out of the
widget modules so the "N/A" / unit-conversion logic isn't duplicated per tab."""
from __future__ import annotations

UNAVAILABLE_LABEL = "Indisponível"


def format_bytes(value: int | float | None, *, per_second: bool = False) -> str:
    if value is None:
        return UNAVAILABLE_LABEL
    suffix = "/s" if per_second else ""
    magnitude = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if magnitude < 1024.0 or unit == "TB":
            return f"{magnitude:.1f} {unit}{suffix}" if unit != "B" else f"{magnitude:.0f} {unit}{suffix}"
        magnitude /= 1024.0
    return f"{magnitude:.1f} TB{suffix}"


def format_percent(value: float | None) -> str:
    if value is None:
        return UNAVAILABLE_LABEL
    return f"{value:.1f}%"


def format_mhz(value: float | None) -> str:
    if value is None:
        return UNAVAILABLE_LABEL
    if value >= 1000:
        return f"{value / 1000:.2f} GHz"
    return f"{value:.0f} MHz"


def format_celsius(value: float | None) -> str:
    if value is None:
        return UNAVAILABLE_LABEL
    return f"{value:.1f} °C"
