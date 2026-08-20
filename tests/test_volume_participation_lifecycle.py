from __future__ import annotations

import pandas as pd

from financial_dashboard.engines import (
    AbsorptionEvent,
    AbsorptionSide,
    AbsorptionStage,
    BreakParticipationEvent,
    BreakStage,
    LifecycleStage,
    ParticipationLifecycleConfig,
    VolumeParticipationConfig,
    VolumeParticipationEngine,
)


def _bar(i: int, o: float, h: float, l: float, c: float, v: float = 1000.0, *, closed: bool = True, complete: bool = True) -> dict:
    return {
        "timestamp": pd.Timestamp("2026-02-01", tz="Europe/Istanbul") + pd.Timedelta(hours=i),
        "open": o, "high": h, "low": l, "close": c, "volume": v,
        "is_closed": closed, "is_complete": complete,
    }


def _cfg() -> VolumeParticipationConfig:
    return VolumeParticipationConfig(
        minimum_history=12,
        atr_length=3,
        volume_short_length=3,
        volume_average_length=5,
        volume_long_length=8,
        percentile_length=8,
        slope_lookback=2,
        flow_short_length=3,
        flow_medium_length=5,
        progress_lookback=3,
        minimum_nonzero_volume_share=0.5,
        minimum_directional_share=0.90,
        minimum_capital_pressure=1.0,
        participation_minimum_evidence=9,
    )


def _base(n: int = 18) -> pd.DataFrame:
    rows = []
    price = 100.0
    for i in range(n):
        o = price
        c = price + (0.08 if i % 2 == 0 else -0.04)
        rows.append(_bar(i, o, max(o, c) + 0.5, min(o, c) - 0.5, c))
        price = c
    return pd.DataFrame(rows)


def test_pivot_is_visible_only_at_known_index() -> None:
    cfg = _cfg()
    life = ParticipationLifecycleConfig(pivot_length=2, minimum_pivot_range_atr=0.0, minimum_pivot_bar_distance=1)
    frame = _base(12)
    frame.loc[6, ["high", "low", "open", "close"]] = [105.0, 100.0, 100.1, 100.2]
    engine = VolumeParticipationEngine(cfg, life)
    engine.replay(frame.iloc[:8])  # origin=6 exists, but right span is not complete yet
    assert engine.lifecycle_export.last_pivot_high is None
    engine.update(frame.iloc[8].to_dict())
    assert engine.lifecycle_export.last_pivot_high == 105.0
    assert engine.lifecycle_export.last_pivot_high_known_index == 8


def test_open_bar_cannot_mutate_lifecycle_export() -> None:
    engine = VolumeParticipationEngine(_cfg(), ParticipationLifecycleConfig(pivot_length=2))
    engine.replay(_base())
    before = engine.lifecycle_export
    snapshot = engine.snapshot()
    returned = engine.update(_bar(18, 100.0, 120.0, 99.0, 119.0, 50000.0, closed=False))
    assert returned == snapshot
    assert engine.lifecycle_export == before


def test_absorption_candidate_keeps_frozen_reference_until_resolution() -> None:
    engine = VolumeParticipationEngine(_cfg(), ParticipationLifecycleConfig(pivot_length=2, absorption_confirmation_window=4))
    engine.replay(_base())
    index = len(engine._rows) - 1
    engine._absorption = AbsorptionEvent(
        side=AbsorptionSide.UPPER,
        stage=AbsorptionStage.CANDIDATE,
        candidate_index=index,
        reference_level=101.25,
        reference_source="PIVOT",
        candidate_high=103.0,
        candidate_low=99.0,
        candidate_mid=101.0,
        frozen_atr=2.0,
        frozen_buffer=0.5,
    )
    engine.update(_bar(index + 1, 101.1, 103.2, 100.8, 101.4, 1000.0))
    out = engine.lifecycle_export
    assert out.absorption_reference_level == 101.25
    assert out.absorption_frozen_atr == 2.0
    assert out.absorption_frozen_buffer == 0.5


def test_break_reclaim_uses_frozen_level_and_buffer() -> None:
    engine = VolumeParticipationEngine(_cfg(), ParticipationLifecycleConfig(pivot_length=2))
    engine.replay(_base())
    index = len(engine._rows) - 1
    engine._break = BreakParticipationEvent(
        direction=1,
        stage=BreakStage.DEVELOPING,
        start_index=index,
        level=101.0,
        reference_source="PIVOT",
        frozen_atr=2.0,
        frozen_buffer=0.16,
    )
    engine.update(_bar(index + 1, 101.1, 101.2, 100.2, 100.5, 1000.0))
    out = engine.lifecycle_export
    assert out.break_stage == BreakStage.RECLAIMED.value
    assert out.break_level == 101.0
    assert out.break_frozen_buffer == 0.16


def test_controlled_pullback_protects_recent_up_participation() -> None:
    engine = VolumeParticipationEngine(_cfg(), ParticipationLifecycleConfig(pivot_length=2, recent_context_length=8))
    frame = _base()
    engine.replay(frame)
    index = len(engine._rows) - 1
    engine._participation_direction = 1
    engine._participation_stage = LifecycleStage.CONFIRMED
    engine._participation_confirmed_index = index
    last = float(frame.iloc[-1]["close"])
    engine.update(_bar(index + 1, last, last + 0.2, last - 0.4, last - 0.15, 700.0))
    assert engine.lifecycle_export.controlled_pullback is True
    assert engine.lifecycle_export.participation_stage == LifecycleStage.PROTECTED.value
