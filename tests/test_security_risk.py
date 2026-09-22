"""Unit tests for security/risk.py — the explainable severity scoring."""
from __future__ import annotations

from security.models import Severity
from security.risk import assess


def test_no_signals_is_info_with_full_confidence():
    result = assess([])
    assert result.severity == Severity.INFO
    assert result.confidence == 1.0
    assert result.signals == ()


def test_single_weak_signal_never_exceeds_medium():
    """The V1.5 spec's explicit rule: "um único sinal fraco não deve gerar
    automaticamente HIGH/CRITICAL" — even a deliberately high-weight single signal
    (weight 10, well above the CRITICAL threshold) must be capped at MEDIUM."""
    result = assess([("um sinal muito forte sozinho", 10)])
    assert result.severity == Severity.MEDIUM


def test_two_signals_can_reach_high():
    result = assess([("sinal a", 3), ("sinal b", 3)])
    assert result.severity == Severity.HIGH


def test_many_signals_can_reach_critical():
    result = assess([("a", 3), ("b", 3), ("c", 3)])
    assert result.severity == Severity.CRITICAL


def test_signals_preserved_as_reasons():
    result = assess([("motivo um", 1), ("motivo dois", 1)])
    assert result.signals == ("motivo um", "motivo dois")


def test_confidence_increases_with_more_signals_but_never_reaches_one():
    one_signal = assess([("a", 1)])
    three_signals = assess([("a", 1), ("b", 1), ("c", 1)])
    assert three_signals.confidence > one_signal.confidence
    assert three_signals.confidence < 1.0


def test_low_total_weight_is_low_severity():
    result = assess([("sinal fraco", 1)])
    assert result.severity == Severity.LOW
