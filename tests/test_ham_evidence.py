from __future__ import annotations

from dataclasses import fields
import math

import pandas as pd
import pytest

from financial_dashboard.engines.ham_evidence import (
    HamEvidenceEngine,
    HamEvidenceSnapshot,
    HamFamily,
    build_ham_family_evidence,
)
from financial_dashboard.engines.raw_indicator_dashboard import (
    IndicatorEvidence,
    RawDataQuality,
    RawIndicatorConfig,
    RawIndicatorSnapshot,
    TrendProfile,
    TrendReason,
    VolumeQuality,
)
from financial_dashboard.engines.raw_indicator_dashboard_decision import (
    HamDashboardDecisionEngine,
)

TZ = "Europe/Istanbul"


def _frame(count: int = 90, *, volume_mode: str = "varied") -> pd.DataFrame:
    rows = []
    for index in range(count):
        base = 100.0 + index * 0.11 + math.sin(index / 5.0) * 1.4
        close = base + math.sin(index / 3.0) * 0.28
        open_ = close - math.sin(index / 4.0) * 0.20
        if volume_mode == "missing":
            volume = float("nan")
        elif volume_mode == "constant":
            volume = 1000.0
        else:
            volume = 1000.0 + (index % 11) * 73.0
        rows.append(
            {
                "timestamp": pd.Timestamp("2026-01-02 10:00", tz=TZ)
                + pd.Timedelta(hours=index),
                "open": open_,
                "high": max(open_, close) + 0.65,
                "low": min(open_, close) - 0.65,
                "close": close,
                "volume": volume,
                "is_closed": True,
                "is_complete": True,
            }
        )
    return pd.DataFrame(rows)


def _evidence(value: float) -> IndicatorEvidence:
    return IndicatorEvidence(
        value=value,
        valid=True,
        direction=1 if value > 0.0 else -1 if value < 0.0 else 0,
        pending_direction=0,
        reason=TrendReason.CONFIRMED,
        consistency=90.0,
        movement_strength=abs(value),
        signed_zone=value,
        evidence=value,
        relative_evidence=value,
    )


def _manual_raw() -> RawIndicatorSnapshot:
    return RawIndicatorSnapshot(
        data_quality=RawDataQuality.OK,
        volume_quality=VolumeQuality.LIMITED,
        volume_coverage=100.0,
        volume_variation=30.0,
        volume_calculable=True,
        volume_reliable=False,
        volume_trust=0.5,
        indicators={
            "PRICE_CONTEXT": _evidence(0.40),
            "MACD": _evidence(0.80),
            "MOMENTUM": _evidence(0.40),
            "RSI": _evidence(-0.20),
            "CCI": _evidence(0.20),
            "SMI": _evidence(0.00),
            "CMF": _evidence(0.80),
            "OBV": _evidence(0.40),
            "STOCHASTIC": _evidence(0.325),
            "STOCH_RSI": _evidence(0.325),
        },
        valid_evidence_count=10,
    )


def test_neutral_family_extraction_keeps_exact_tur2_math_and_decision_parity() -> None:
    raw = _manual_raw()
    families = build_ham_family_evidence(raw)

    expected_oscillator = (-0.20 * 1.05 + 0.20 * 0.90) / (1.05 + 0.90 + 0.80)
    expected_momentum = (0.60 + expected_oscillator) / 2.0 * 100.0
    assert families.price.balance == pytest.approx(40.0)
    assert families.momentum.balance == pytest.approx(expected_momentum)
    assert families.timing.balance == pytest.approx(50.0)
    assert families.flow.balance == pytest.approx(30.0)
    assert families.flow.confidence == pytest.approx(0.5)
    assert families.for_family(HamFamily.MOMENTUM) == families.momentum
    assert families.for_family("flow") == families.flow

    decision_families = HamDashboardDecisionEngine()._families(raw)
    assert decision_families == families.as_tuple()


def test_public_ham_snapshot_is_action_free_and_retains_raw_reason_contract() -> None:
    snapshot = HamEvidenceEngine().replay(_frame())[-1]
    field_names = {field.name for field in fields(HamEvidenceSnapshot)}

    assert field_names == {"raw", "families"}
    assert not hasattr(snapshot, "system_state")
    assert not hasattr(snapshot, "system_bias")
    assert not hasattr(snapshot, "family_decision_score")
    assert set(snapshot.raw.indicators) == {
        "PRICE_CONTEXT",
        "MACD",
        "MOMENTUM",
        "RSI",
        "CCI",
        "SMI",
        "CMF",
        "OBV",
        "STOCHASTIC",
        "STOCH_RSI",
    }
    assert all(
        isinstance(indicator.reason, TrendReason)
        and indicator.direction in {-1, 0, 1}
        and indicator.pending_direction in {-1, 0, 1}
        for indicator in snapshot.raw.indicators.values()
    )


def test_replay_retains_every_confirmed_bar_and_freezes_incomplete_preview() -> None:
    confirmed = _frame()
    preview = confirmed.iloc[60].to_dict()
    preview["timestamp"] += pd.Timedelta(minutes=10)
    preview["close"] *= 1.75
    preview["high"] = preview["close"] + 0.5
    preview["is_closed"] = False
    mixed = pd.concat(
        [confirmed.iloc[:61], pd.DataFrame([preview]), confirmed.iloc[61:]],
        ignore_index=True,
    )

    engine = HamEvidenceEngine(
        raw_config=RawIndicatorConfig(profile=TrendProfile.XAG_1H)
    )
    results = engine.replay(mixed)

    assert len(results) == len(mixed)
    assert len(engine.history) == len(confirmed)
    assert results[61].data_quality == RawDataQuality.INCOMPLETE_BAR
    assert engine.snapshot == engine.history[-1]
    assert tuple(snapshot.timestamp for snapshot in engine.history) == tuple(
        confirmed["timestamp"]
    )

    before = engine.snapshot
    before_count = len(engine.history)
    second_preview = confirmed.iloc[-1].to_dict()
    second_preview["timestamp"] += pd.Timedelta(minutes=10)
    second_preview["is_complete"] = False
    transient = engine.update(second_preview)
    assert transient.data_quality == RawDataQuality.SOURCE_GAP
    assert engine.snapshot == before
    assert len(engine.history) == before_count


def test_replay_incremental_restart_and_prefix_results_are_identical() -> None:
    frame = _frame(80)
    config = RawIndicatorConfig(profile=TrendProfile.XAG_2H)

    replay_engine = HamEvidenceEngine(raw_config=config)
    replay_history = replay_engine.replay(frame)

    incremental_engine = HamEvidenceEngine(raw_config=config)
    incremental_history = tuple(
        incremental_engine.update(row) for row in frame.to_dict("records")
    )
    restarted = HamEvidenceEngine(raw_config=config)
    restarted.replay(frame)

    assert incremental_history == replay_history
    assert incremental_engine.history == replay_engine.history
    assert restarted.history == replay_engine.history

    cutoff = 55
    prefix_engine = HamEvidenceEngine(raw_config=config)
    prefix = prefix_engine.replay(frame.iloc[:cutoff])
    assert prefix[-1] == replay_history[cutoff - 1]


def test_missing_and_limited_volume_do_not_suppress_non_flow_evidence() -> None:
    for mode, expected_quality in (
        ("missing", VolumeQuality.MISSING),
        ("constant", VolumeQuality.LIMITED),
    ):
        latest = HamEvidenceEngine().replay(_frame(70, volume_mode=mode))[-1]
        assert latest.raw.volume_quality == expected_quality
        assert latest.raw.volume_reliable is False
        assert latest.families.flow.ready is False
        assert latest.families.price.ready is True
        assert latest.families.momentum.ready is True
        assert latest.families.timing.ready is True
