from __future__ import annotations

import pandas as pd
import pytest

from financial_dashboard.engines import HamDashboardDecisionEngine, SystemState
from financial_dashboard.engines.raw_indicator_dashboard import IndicatorEvidence, RawDataQuality, RawIndicatorSnapshot, TrendReason, VolumeQuality

TZ = "Europe/Istanbul"


def _ev(value: float, *, maximum: float = 1.0) -> IndicatorEvidence:
    direction = 1 if value > 0 else -1 if value < 0 else 0
    return IndicatorEvidence(
        value=value,
        valid=True,
        direction=direction,
        pending_direction=0,
        reason=TrendReason.CONFIRMED,
        consistency=90.0,
        movement_strength=min(abs(value / maximum), 1.0),
        signed_zone=max(-1.0, min(1.0, value / maximum)),
        evidence=value,
        relative_evidence=max(-1.0, min(1.0, value / maximum)),
    )


def _raw(price: float, momentum_core: float, timing: float, flow: float) -> RawIndicatorSnapshot:
    indicators = {
        "PRICE_CONTEXT": _ev(price),
        "MACD": _ev(momentum_core),
        "MOMENTUM": _ev(momentum_core),
        "RSI": _ev(momentum_core),
        "CCI": _ev(momentum_core),
        "SMI": _ev(momentum_core),
        "CMF": _ev(flow),
        "OBV": _ev(flow),
        "STOCHASTIC": _ev(timing, maximum=0.65),
        "STOCH_RSI": _ev(timing, maximum=0.65),
    }
    rel = [e.relative_evidence or 0.0 for e in indicators.values()]
    return RawIndicatorSnapshot(
        timestamp=pd.Timestamp("2026-08-20 18:00", tz=TZ),
        data_quality=RawDataQuality.OK,
        volume_quality=VolumeQuality.ADEQUATE,
        volume_coverage=100.0,
        volume_variation=100.0,
        volume_calculable=True,
        volume_reliable=True,
        volume_trust=1.0,
        atr=2.0,
        atr_ratio=1.0,
        price_context=price,
        price_context_valid=True,
        indicators=indicators,
        valid_evidence_count=10,
        up_evidence_count=sum(v >= 0.15 for v in rel),
        down_evidence_count=sum(v <= -0.15 for v in rel),
        strong_up_count=sum(v >= 0.60 for v in rel),
        strong_down_count=sum(v <= -0.60 for v in rel),
        net_evidence_score=0.0,
    )


def test_countertrend_up_reaction_requires_timing_plus_second_family_support() -> None:
    engine = HamDashboardDecisionEngine()
    result = engine._decide(_raw(price=-0.20, momentum_core=0.40, timing=0.30, flow=0.20))
    assert result.system_state == SystemState.REACTION_UP
    assert result.system_bias == 1


def test_countertrend_reaction_does_not_fire_from_timing_alone() -> None:
    engine = HamDashboardDecisionEngine()
    result = engine._decide(_raw(price=-0.20, momentum_core=0.0, timing=0.30, flow=0.0))
    assert result.system_state != SystemState.REACTION_UP


def test_weakening_requires_stable_family_coverage_and_score_drop() -> None:
    engine = HamDashboardDecisionEngine()
    first = engine._decide(_raw(price=0.85, momentum_core=0.85, timing=0.60, flow=0.70))
    assert first.system_state == SystemState.STRONG_UP
    engine._history.append(first)

    second = engine._decide(_raw(price=0.50, momentum_core=0.45, timing=0.20, flow=0.30))
    assert second.family_decision_coverage_stable is True
    assert first.family_decision_score is not None and second.family_decision_score is not None
    assert first.family_decision_score - second.family_decision_score >= engine.config.weakening_score_drop
    assert second.system_state == SystemState.WEAKENING_UP


def test_weakening_is_suppressed_when_family_coverage_changes() -> None:
    engine = HamDashboardDecisionEngine()
    first = engine._decide(_raw(price=0.85, momentum_core=0.85, timing=0.60, flow=0.70))
    engine._history.append(first)

    raw = _raw(price=0.50, momentum_core=0.45, timing=0.20, flow=0.30)
    broken = dict(raw.indicators)
    broken["CMF"] = IndicatorEvidence(0.0, False, 0, 0, TrendReason.DATA_WAIT, None, 0.0, 0.0, None, None)
    broken["OBV"] = IndicatorEvidence(0.0, False, 0, 0, TrendReason.DATA_WAIT, None, 0.0, 0.0, None, None)
    raw = RawIndicatorSnapshot(
        timestamp=raw.timestamp,
        data_quality=raw.data_quality,
        volume_quality=VolumeQuality.MISSING,
        volume_coverage=0.0,
        volume_variation=None,
        volume_calculable=False,
        volume_reliable=False,
        volume_trust=0.0,
        atr=raw.atr,
        atr_ratio=raw.atr_ratio,
        price_context=raw.price_context,
        price_context_valid=True,
        indicators=broken,
        valid_evidence_count=8,
        up_evidence_count=raw.up_evidence_count,
        down_evidence_count=raw.down_evidence_count,
        strong_up_count=raw.strong_up_count,
        strong_down_count=raw.strong_down_count,
        net_evidence_score=raw.net_evidence_score,
    )
    second = engine._decide(raw)
    assert second.family_decision_coverage_stable is False
    assert second.system_state != SystemState.WEAKENING_UP
