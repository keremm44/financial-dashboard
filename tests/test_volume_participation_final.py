from __future__ import annotations

import pandas as pd

from financial_dashboard.engines import (
    AbsorptionEvent,
    AbsorptionSide,
    AbsorptionStage,
    BreakParticipationEvent,
    BreakStage,
    FinalParticipationState,
    LifecycleStage,
    ParticipationLifecycleConfig,
    VolumeParticipationConfig,
    VolumeParticipationEngine,
)
from financial_dashboard.engines.models import Direction


def _bar(i: int, o: float, h: float, l: float, c: float, v: float = 1000.0, *, closed: bool = True, complete: bool = True) -> dict:
    return {
        "timestamp": pd.Timestamp("2026-03-01", tz="Europe/Istanbul") + pd.Timedelta(hours=i),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
        "is_closed": closed,
        "is_complete": complete,
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
        minimum_directional_share=0.0,
        minimum_capital_pressure=0.13,
        participation_minimum_evidence=3,
        participation_confirmation_bars=2,
        confirmation_minimum_rvol=0.0,
        confirmation_minimum_rtv=0.0,
        minimum_progress_atr=0.01,
        minimum_efficiency=0.01,
        minimum_body_atr=0.0,
        up_close_location=0.0,
        down_close_location=1.0,
        maximum_directional_wick_ratio=1.0,
        minimum_directional_close_share=0.0,
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


def test_confirmed_absorption_drives_final_state_and_direction() -> None:
    engine = VolumeParticipationEngine(_cfg(), ParticipationLifecycleConfig(pivot_length=2))
    engine.replay(_base())
    engine._absorption = AbsorptionEvent(
        side=AbsorptionSide.UPPER,
        stage=AbsorptionStage.CONFIRMED,
        candidate_index=len(engine._rows) - 2,
        reference_level=101.0,
        reference_source="PIVOT",
        candidate_high=102.0,
        candidate_low=99.0,
        candidate_mid=100.5,
        frozen_atr=1.0,
        frozen_buffer=0.08,
    )
    metrics = engine.metrics_history[-1]
    state = engine._resolve_final(metrics, False)
    assert state == FinalParticipationState.UPPER_ABSORPTION_CONFIRMED
    assert engine._support_direction(state) == -2
    assert engine._engine_direction(state) == Direction.DOWN


def test_conflict_has_priority_over_confirmed_absorption() -> None:
    engine = VolumeParticipationEngine(_cfg(), ParticipationLifecycleConfig(pivot_length=2))
    engine.replay(_base())
    metrics = engine.metrics_history[-1]
    engine._absorption = AbsorptionEvent(side=AbsorptionSide.UPPER, stage=AbsorptionStage.CONFIRMED)
    metrics = metrics.__class__(**{field: getattr(metrics, field) for field in metrics.__dataclass_fields__})
    object.__setattr__(metrics, "up_evidence_count", engine.config.participation_minimum_evidence)
    object.__setattr__(metrics, "down_evidence_count", engine.config.participation_minimum_evidence)
    object.__setattr__(metrics, "directional_value_pressure_5", 0.0)
    assert engine._resolve_final(metrics, False) == FinalParticipationState.CONFLICT


def test_supported_break_has_priority_over_confirmed_participation() -> None:
    engine = VolumeParticipationEngine(_cfg(), ParticipationLifecycleConfig(pivot_length=2))
    engine.replay(_base())
    metrics = engine.metrics_history[-1]
    object.__setattr__(metrics, "up_confirmed", True)
    engine._break = BreakParticipationEvent(
        direction=-1,
        stage=BreakStage.SUPPORTED,
        start_index=len(engine._rows) - 1,
        level=99.0,
        reference_source="PIVOT",
        frozen_atr=1.0,
        frozen_buffer=0.08,
    )
    assert engine._resolve_final(metrics, False) == FinalParticipationState.CONFLICT


def test_candidate_support_direction_does_not_become_engine_direction() -> None:
    engine = VolumeParticipationEngine(_cfg(), ParticipationLifecycleConfig(pivot_length=2))
    engine.replay(_base())
    state = FinalParticipationState.UP_CANDIDATE
    assert engine._support_direction(state) == 1
    assert engine._engine_direction(state) == Direction.NEUTRAL


def test_weakening_and_reclaimed_are_not_generic_direction_votes() -> None:
    engine = VolumeParticipationEngine(_cfg(), ParticipationLifecycleConfig(pivot_length=2))
    assert engine._support_direction(FinalParticipationState.UP_WEAKENING) == 0
    assert engine._support_direction(FinalParticipationState.UP_BREAK_RECLAIMED) == 0
    assert engine._engine_direction(FinalParticipationState.UP_WEAKENING) == Direction.NEUTRAL
    assert engine._engine_direction(FinalParticipationState.UP_BREAK_RECLAIMED) == Direction.NEUTRAL


def test_open_bar_cannot_mutate_final_snapshot_or_export() -> None:
    engine = VolumeParticipationEngine(_cfg(), ParticipationLifecycleConfig(pivot_length=2))
    engine.replay(_base())
    before_snapshot = engine.snapshot()
    before_export = engine.final_export
    returned = engine.update(_bar(18, 100.0, 130.0, 99.0, 129.0, 50000.0, closed=False))
    assert returned == before_snapshot
    assert engine.final_export == before_export


def test_final_replay_matches_incremental_and_future_tail_cannot_rewrite_prefix() -> None:
    frame = _base(30)
    cfg = _cfg()
    life = ParticipationLifecycleConfig(pivot_length=2)
    replay_engine = VolumeParticipationEngine(cfg, life)
    replay_results = replay_engine.replay(frame)
    incremental = VolumeParticipationEngine(cfg, life)
    incremental_results = [incremental.update(row) for _, row in frame.iterrows()]
    assert replay_results == incremental_results
    assert replay_engine.final_export == incremental.final_export

    prefix = frame.iloc[:22].copy()
    prefix_engine = VolumeParticipationEngine(cfg, life)
    prefix_results = prefix_engine.replay(prefix)
    full_engine = VolumeParticipationEngine(cfg, life)
    full_results = full_engine.replay(frame)
    assert full_results[: len(prefix_results)] == prefix_results
