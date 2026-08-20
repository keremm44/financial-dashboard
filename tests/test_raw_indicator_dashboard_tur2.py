from __future__ import annotations

import math

import pandas as pd
import pytest

from financial_dashboard.engines import (
    DecisionConfig,
    HamDashboardDecisionEngine,
    SystemState,
)
from financial_dashboard.engines.raw_indicator_dashboard import (
    IndicatorEvidence,
    RawDataQuality,
    RawIndicatorSnapshot,
    TrendReason,
    VolumeQuality,
)

TZ = "Europe/Istanbul"


def _ev(value: float, *, valid: bool = True, consistency: float = 90.0, pending: int = 0, direction: int | None = None, relative: float | None = None) -> IndicatorEvidence:
    d = direction if direction is not None else (1 if value > 0 else -1 if value < 0 else 0)
    return IndicatorEvidence(
        value=value,
        valid=valid,
        direction=d,
        pending_direction=pending,
        reason=TrendReason.CONFIRMED if valid else TrendReason.DATA_WAIT,
        consistency=consistency if valid else None,
        movement_strength=min(abs(value), 1.0),
        signed_zone=max(-1.0, min(1.0, value)),
        evidence=value if valid else None,
        relative_evidence=(relative if relative is not None else value) if valid else None,
    )


def _raw(
    *,
    price: float = 0.80,
    macd: float = 0.80,
    momentum: float = 0.80,
    rsi: float = 0.70,
    cci: float = 0.70,
    smi: float = 0.70,
    cmf: float = 0.60,
    obv: float = 0.60,
    stoch: float = 0.45,
    srsi: float = 0.45,
    volume_trust: float = 1.0,
    volume_quality: VolumeQuality = VolumeQuality.ADEQUATE,
    atr_ratio: float = 1.0,
) -> RawIndicatorSnapshot:
    indicators = {
        "PRICE_CONTEXT": _ev(price),
        "MACD": _ev(macd),
        "MOMENTUM": _ev(momentum),
        "RSI": _ev(rsi),
        "CCI": _ev(cci),
        "SMI": _ev(smi),
        "CMF": _ev(cmf),
        "OBV": _ev(obv),
        "STOCHASTIC": _ev(stoch, relative=stoch / 0.65),
        "STOCH_RSI": _ev(srsi, relative=srsi / 0.65),
    }
    weak = 0.15
    strong = 0.60
    relatives = [e.relative_evidence or 0.0 for e in indicators.values()]
    return RawIndicatorSnapshot(
        timestamp=pd.Timestamp("2026-08-20 18:00", tz=TZ),
        data_quality=RawDataQuality.OK,
        volume_quality=volume_quality,
        volume_coverage=100.0,
        volume_variation=100.0,
        volume_calculable=volume_quality >= VolumeQuality.LIMITED,
        volume_reliable=volume_quality == VolumeQuality.ADEQUATE,
        volume_trust=volume_trust,
        atr=2.0,
        atr_ratio=atr_ratio,
        price_context=price,
        price_context_valid=True,
        indicators=indicators,
        valid_evidence_count=10,
        up_evidence_count=sum(v >= weak for v in relatives),
        down_evidence_count=sum(v <= -weak for v in relatives),
        strong_up_count=sum(v >= strong for v in relatives),
        strong_down_count=sum(v <= -strong for v in relatives),
        net_evidence_score=50.0,
    )


def _frame(count: int = 150) -> pd.DataFrame:
    rows = []
    for i in range(count):
        base = 100.0 + i * 0.15 + math.sin(i / 6.0) * 1.1
        close = base + math.sin(i / 3.0) * 0.25
        open_ = close - 0.18
        rows.append(
            {
                "timestamp": pd.Timestamp("2026-01-02 10:00", tz=TZ) + pd.Timedelta(hours=i),
                "open": open_,
                "high": max(open_, close) + 0.7,
                "low": min(open_, close) - 0.7,
                "close": close,
                "volume": 1000.0 + (i % 13) * 71.0,
                "is_closed": True,
                "is_complete": True,
            }
        )
    return pd.DataFrame(rows)


def test_family_layer_normalizes_timing_by_065_capacity() -> None:
    engine = HamDashboardDecisionEngine()
    result = engine._decide(_raw(stoch=0.325, srsi=0.325))
    assert result.timing_family is not None
    assert result.timing_family.balance == pytest.approx(50.0)
    assert result.timing_family.activity == pytest.approx(50.0)
    assert result.timing_family.coverage == pytest.approx(100.0)


def test_momentum_family_uses_equal_impulse_and_oscillator_roles() -> None:
    engine = HamDashboardDecisionEngine()
    raw = _raw(macd=1.0, momentum=1.0, rsi=-1.0, cci=-1.0, smi=-1.0)
    result = engine._decide(raw)
    assert result.momentum_family is not None
    assert result.momentum_family.balance == pytest.approx(0.0, abs=1e-9)


def test_flow_confidence_scales_family_balance_and_decision_weight_once() -> None:
    full = HamDashboardDecisionEngine()._decide(_raw(cmf=0.8, obv=0.8, volume_trust=1.0))
    limited = HamDashboardDecisionEngine()._decide(
        _raw(cmf=0.8, obv=0.8, volume_trust=0.5, volume_quality=VolumeQuality.LIMITED)
    )
    assert full.flow_family is not None and limited.flow_family is not None
    assert full.flow_family.balance == pytest.approx(80.0)
    assert limited.flow_family.balance == pytest.approx(40.0)
    assert limited.flow_family.confidence == pytest.approx(0.5)
    assert limited.family_decision_valid_weight < full.family_decision_valid_weight


def test_strong_bullish_quorum_produces_strong_up() -> None:
    result = HamDashboardDecisionEngine()._decide(_raw(stoch=0.60, srsi=0.60))
    assert result.system_state == SystemState.STRONG_UP
    assert result.system_bias == 1
    assert result.up_family_count >= 3
    assert result.strong_up_family_count >= 2
    assert result.decision_quality >= 75.0


def test_strong_bearish_quorum_produces_strong_down() -> None:
    raw = _raw(
        price=-0.80,
        macd=-0.80,
        momentum=-0.80,
        rsi=-0.70,
        cci=-0.70,
        smi=-0.70,
        cmf=-0.60,
        obv=-0.60,
        stoch=-0.60,
        srsi=-0.60,
    )
    result = HamDashboardDecisionEngine()._decide(raw)
    assert result.system_state == SystemState.STRONG_DOWN
    assert result.system_bias == -1


def test_price_momentum_healthy_opposition_is_conflict() -> None:
    raw = _raw(price=0.80, macd=-0.80, momentum=-0.80, rsi=-0.70, cci=-0.70, smi=-0.70, cmf=0.0, obv=0.0, stoch=0.0, srsi=0.0)
    result = HamDashboardDecisionEngine()._decide(raw)
    assert result.system_conflict is True
    assert result.system_state == SystemState.CONFLICT
    assert "ÇELİŞKİ" in result.risk_flags


def test_timing_mismatch_is_risk_not_quality_double_penalty() -> None:
    aligned = HamDashboardDecisionEngine()._decide(_raw(stoch=0.50, srsi=0.50))
    mismatch = HamDashboardDecisionEngine()._decide(_raw(stoch=-0.50, srsi=-0.50))
    assert mismatch.timing_mismatch is True
    assert "ZAMANLAMA TERS" in mismatch.risk_flags
    # Timing is allowed to affect family score/agreement, but there is no extra fixed quality penalty.
    assert mismatch.decision_quality > 0.0
    assert aligned.decision_quality > mismatch.decision_quality


def test_high_atr_is_reported_as_risk_without_changing_system_gate_directly() -> None:
    normal = HamDashboardDecisionEngine()._decide(_raw(atr_ratio=1.0, stoch=0.60, srsi=0.60))
    high = HamDashboardDecisionEngine()._decide(_raw(atr_ratio=1.6, stoch=0.60, srsi=0.60))
    assert high.system_state == normal.system_state
    assert high.decision_quality == pytest.approx(normal.decision_quality)
    assert "VOLATİLİTE ÇOK YÜKSEK" in high.risk_flags


def test_synthetic_decision_block_disables_system_and_exports() -> None:
    cfg = DecisionConfig(decision_chart_allowed=False)
    result = HamDashboardDecisionEngine(decision_config=cfg)._decide(_raw())
    assert result.system_state == SystemState.SYNTHETIC_BLOCK
    assert result.export.momentum_state is None
    assert result.export.timing_state is None


def test_export_contains_only_momentum_and_timing_contract_ports() -> None:
    result = HamDashboardDecisionEngine()._decide(_raw())
    assert result.export.momentum_state in {-2, -1, 0, 1, 2}
    assert result.export.timing_state in {-2, -1, 0, 1, 2}
    assert result.export.momentum_score == pytest.approx(result.momentum_family.balance)
    assert result.export.timing_score == pytest.approx(result.timing_family.balance)
    assert not hasattr(result.export, "price_score")
    assert not hasattr(result.export, "flow_score")


def test_open_and_source_gap_freeze_decision_snapshot_atomically() -> None:
    engine = HamDashboardDecisionEngine()
    confirmed = engine.replay(_frame(130))[-1]

    open_row = _frame(131).iloc[-1].to_dict()
    open_row["is_closed"] = False
    open_seen = engine.update(open_row)
    assert open_seen.data_quality == RawDataQuality.INCOMPLETE_BAR
    assert engine.snapshot == confirmed
    assert open_seen.system_state == confirmed.system_state
    assert open_seen.export == confirmed.export

    gap_row = _frame(131).iloc[-1].to_dict()
    gap_row["is_complete"] = False
    gap_seen = engine.update(gap_row)
    assert gap_seen.data_quality == RawDataQuality.SOURCE_GAP
    assert engine.snapshot == confirmed
    assert gap_seen.family_decision_score == confirmed.family_decision_score


def test_replay_equals_incremental_for_tur2_final_snapshot() -> None:
    frame = _frame(140)
    replay_final = HamDashboardDecisionEngine().replay(frame)[-1]
    incremental = HamDashboardDecisionEngine()
    for row in frame.to_dict("records"):
        incremental.update(row)
    assert incremental.snapshot == replay_final


def test_future_tail_does_not_change_historical_tur2_snapshot() -> None:
    frame = _frame(150)
    cutoff = 120
    prefix = HamDashboardDecisionEngine().replay(frame.iloc[:cutoff])[-1]
    full = HamDashboardDecisionEngine().replay(frame)
    assert full[cutoff - 1] == prefix


def test_warmup_waits_for_raw_and_family_coverage() -> None:
    result = HamDashboardDecisionEngine().replay(_frame(8))[-1]
    assert result.system_state == SystemState.DATA_WAIT
    assert result.valid_family_count < 3 or result.raw.valid_evidence_count < 6
    assert result.export.momentum_state is None or result.momentum_family.ready


def test_public_import_surface_exposes_tur2_engine() -> None:
    from financial_dashboard.engines import HamDashboardDecisionEngine as PublicEngine

    assert PublicEngine is HamDashboardDecisionEngine
