"""Explainable risk scoring: a Severity is always backed by the concrete signals that
produced it (security/models.py::RiskAssessment.signals), never a bare number — see
the V1.5 spec's explicit "Risk = 87% sem explicação" anti-pattern.

Signals come from security/rules.py, each as (reason_text, weight). This module only
aggregates weights into a Severity bucket; it has no opinion about what counts as
suspicious — that's entirely rules.py's job, kept separate so the "how much do
combined signals matter" logic (here) doesn't get tangled with "what counts as a
signal at all" (there).
"""
from __future__ import annotations

from security.models import RiskAssessment, Severity

Signal = tuple[str, int]

#: Cumulative weight thresholds, checked from highest to lowest. Individual signal
#: weights (security/rules.py) are deliberately kept low (1-3) so reaching HIGH/CRITICAL
#: requires several signals to combine — see the hard single-signal cap below for the
#: explicit, auditable guarantee rather than relying on weight-tuning alone.
_THRESHOLDS: tuple[tuple[Severity, int], ...] = (
    (Severity.CRITICAL, 8),
    (Severity.HIGH, 5),
    (Severity.MEDIUM, 3),
    (Severity.LOW, 1),
)


def _severity_for_weight(total_weight: int) -> Severity:
    for severity, threshold in _THRESHOLDS:
        if total_weight >= threshold:
            return severity
    return Severity.INFO


def assess(signals: list[Signal]) -> RiskAssessment:
    """`signals` empty -> INFO, no evidence at all. A single signal is explicitly capped
    at MEDIUM regardless of its own weight — "um único sinal fraco não deve gerar
    automaticamente HIGH/CRITICAL" is enforced here as a hard rule, not left as an
    emergent property of how rules.py happens to weight things today."""
    if not signals:
        return RiskAssessment(severity=Severity.INFO, confidence=1.0, signals=())

    total_weight = sum(weight for _, weight in signals)
    severity = _severity_for_weight(total_weight)
    if len(signals) == 1 and severity > Severity.MEDIUM:
        severity = Severity.MEDIUM

    # More independent signals agreeing raises confidence, but never claims certainty
    # (capped below 1.0 unless the caller has none to weigh at all, handled above).
    confidence = min(0.95, 0.4 + 0.15 * len(signals))

    return RiskAssessment(
        severity=severity,
        confidence=round(confidence, 2),
        signals=tuple(text for text, _ in signals),
    )
