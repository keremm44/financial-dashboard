from __future__ import annotations

import pandas as pd
import pytest

from financial_dashboard.engines import (
    EngulfingDirection,
    EngulfingLifecycleRecord,
    EngulfingState,
    FvgDirection,
    FvgEngulfingConfig,
    FvgEngulfingEngine,
    FvgLifecycleRecord,
    FvgState,
)
from financial_dashboard.engines.fvg_engulfing_final import _LifecycleMetrics

TZ = "Europe/Istanbul"


def _m(**overrides) -> _LifecycleMetrics:
    base = dict(
        close=105.0,
        low=104.0,
        high=106.0,
        candle_bullish=True,
        candle_bearish=False,
        close_location=0.80,
        body_to_prior_atr=0.8,
        net_progress_atr=0.8,
        directional_efficiency=0.8,
        higher_close_share=0.75,
        lower_close_share=0.0,
        buy_continuation_candidate=True,
        sell_continuation_candidate=False,
        buy_continuation_confirmed=True,
        sell_continuation_confirmed=False,
        lower_rejection=False,
        upper_rejection=False,
        bullish_engulfing=False,
        bearish_engulfing=False,
    )
    base.update(overrides)
    return _LifecycleMetrics(**base)


def _fvg(*, bullish=True, state=FvgState.ACTIVE, formation_index=100, quality=70.0) -> FvgLifecycleRecord:
    direction = FvgDirection.BULLISH if bullish else FvgDirection.BEARISH
    return FvgLifecycleRecord(
        direction=direction,
        state=state,
        lower_boundary=100.0,
        upper_boundary=110.0,
        gap_size=10.0,
        gap_atr=1.0,
        formation_atr=10.0,
        invalidation_buffer=0.5,
        formation_index=formation_index,
        formation_time=pd.Timestamp("2026-01-01", tz=TZ),
        quality=quality,
        evidence_count=8,
    )


def _engulf(*, bullish=True, state=EngulfingState.ACTIVE, formation_index=100, quality=75.0) -> EngulfingLifecycleRecord:
    direction = EngulfingDirection.BULLISH if bullish else EngulfingDirection.BEARISH
    return EngulfingLifecycleRecord(
        direction=direction,
        state=state,
        lower_boundary=100.0,
        upper_boundary=110.0,
        body_size=10.0,
        body_atr=1.0,
        formation_high=112.0,
        formation_low=98.0,
        formation_index=formation_index,
        formation_time=pd.Timestamp("2026-01-01", tz=TZ),
        quality=quality,
    )


def test_bullish_fvg_partial_and_deep_fill_states_follow_source_thresholds() -> None:
    engine = FvgEngulfingEngine()
    engine._bull_fvg = _fvg()
    engine._update_fvg_record(True, 101, _m(close=108.0, low=107.0, high=109.0, buy_continuation_candidate=False, buy_continuation_confirmed=False))
    assert engine.active_bullish_fvg is not None
    assert engine.active_bullish_fvg.state is FvgState.PARTIAL_FILL
    assert engine.active_bullish_fvg.maximum_fill_ratio == pytest.approx(0.30)

    engine._update_fvg_record(True, 102, _m(close=106.0, low=104.0, high=108.0, buy_continuation_candidate=False, buy_continuation_confirmed=False, net_progress_atr=0.0))
    assert engine.active_bullish_fvg is not None
    assert engine.active_bullish_fvg.state is FvgState.DEEP_TEST
    assert engine.active_bullish_fvg.maximum_fill_ratio == pytest.approx(0.60)


def test_bullish_fvg_full_fill_is_terminal_and_directional_event_is_kept() -> None:
    engine = FvgEngulfingEngine()
    engine._bull_fvg = _fvg()
    engine._update_fvg_record(True, 101, _m(close=103.0, low=99.0, high=106.0, buy_continuation_candidate=False, buy_continuation_confirmed=False))
    assert engine.active_bullish_fvg is None
    assert engine.completed_fvg[-1].state is FvgState.FULL_FILL
    assert engine._bull_fvg_event == (101, FvgState.FULL_FILL)


def test_fvg_close_invalidation_uses_frozen_formation_buffer_and_profile_count() -> None:
    engine = FvgEngulfingEngine(FvgEngulfingConfig())
    engine._bull_fvg = _fvg()
    bad = _m(close=99.0, low=98.5, high=101.0, candle_bullish=False, candle_bearish=True, net_progress_atr=-0.5, buy_continuation_candidate=False, buy_continuation_confirmed=False)
    engine._update_fvg_record(True, 101, bad)
    assert engine.active_bullish_fvg is not None
    engine._update_fvg_record(True, 102, bad)
    assert engine.active_bullish_fvg is None
    assert engine.completed_fvg[-1].state is FvgState.INVALID
    assert engine.completed_fvg[-1].invalid_reason == "Kapanışla geçersizlik"


def test_candidate_promotion_can_preserve_same_bar_first_test() -> None:
    engine = FvgEngulfingEngine()
    engine._bull_fvg = _fvg(state=FvgState.CANDIDATE)
    engine._update_fvg_record(True, 101, _m(close=111.0, low=108.0, high=112.0))
    rec = engine.active_bullish_fvg
    assert rec is not None
    assert rec.state is FvgState.FIRST_TEST
    assert rec.tested
    assert rec.first_test_index == 101
    assert rec.maximum_fill_ratio == pytest.approx(0.20)


def test_bullish_fvg_reaction_is_terminal_after_zone_hold_and_away_move() -> None:
    engine = FvgEngulfingEngine()
    rec = _fvg()
    rec.tested = True
    rec.first_test_index = 100
    rec.maximum_fill_ratio = 0.30
    engine._bull_fvg = rec
    engine._update_fvg_record(True, 102, _m(close=112.0, low=111.0, high=113.0))
    assert engine.active_bullish_fvg is None
    assert engine.completed_fvg[-1].state is FvgState.REACTION
    assert engine.completed_fvg[-1].reaction_confirmed


def test_fvg_takeover_marks_existing_record_superseded_and_freezes_new_quality() -> None:
    from financial_dashboard.engines.fvg_engulfing_engine import FvgFormation

    engine = FvgEngulfingEngine()
    engine._bull_fvg = _fvg(quality=60.0)
    formation = FvgFormation(
        direction=FvgDirection.BULLISH,
        state=FvgState.ACTIVE,
        formation_index=110,
        timestamp=pd.Timestamp("2026-01-02", tz=TZ),
        lower_boundary=105.0,
        upper_boundary=115.0,
        gap_size=10.0,
        gap_atr=1.0,
        formation_atr=10.0,
        quality=70.0,
        embedded_candle_contribution=10.0,
        evidence_count=9,
    )
    engine._accept_fvg_formation(formation, 110, _m(close=116.0))
    assert engine.completed_fvg[-1].state is FvgState.SUPERSEDED
    assert engine.active_bullish_fvg is not None
    assert engine.active_bullish_fvg.quality == 70.0
    assert engine.active_bullish_fvg.invalidation_buffer == pytest.approx(0.5)


def test_engulfing_retrace_then_weakened_then_grace_expiry() -> None:
    engine = FvgEngulfingEngine()
    engine._bull_engulf = _engulf()
    engine._update_engulfing_record(True, 101, _m(close=108.0, low=107.0, high=109.0, net_progress_atr=0.1, buy_continuation_candidate=False))
    assert engine.active_bullish_engulfing is not None
    assert engine.active_bullish_engulfing.state is EngulfingState.PARTIAL_RETRACE

    engine._update_engulfing_record(True, 102, _m(close=104.0, low=104.0, high=107.0, candle_bullish=False, candle_bearish=True, close_location=0.2, net_progress_atr=-0.3, buy_continuation_candidate=False))
    assert engine.active_bullish_engulfing is not None
    assert engine.active_bullish_engulfing.state is EngulfingState.WEAKENED

    engine._update_engulfing_record(True, 103, _m(close=105.0, low=104.0, high=107.0, net_progress_atr=0.0, buy_continuation_candidate=False))
    assert engine.active_bullish_engulfing is None
    assert engine.completed_engulfing[-1].state is EngulfingState.EXPIRED


def test_engulfing_continuation_confirmation_is_terminal() -> None:
    engine = FvgEngulfingEngine()
    engine._bull_engulf = _engulf()
    engine._update_engulfing_record(True, 101, _m(close=113.0, low=111.0, high=114.0))
    assert engine.active_bullish_engulfing is None
    assert engine.completed_engulfing[-1].state is EngulfingState.CONTINUATION_CONFIRMED
    assert engine.completed_engulfing[-1].continuation_evidence_count >= 3


def test_export_contract_uses_remaining_fvg_zone_original_engulf_zone_and_signed_bear_states() -> None:
    engine = FvgEngulfingEngine()
    bull = _fvg(); bull.maximum_fill_ratio = 0.40; bull.state = FvgState.PARTIAL_FILL
    bear = _fvg(bullish=False); bear.maximum_fill_ratio = 0.30; bear.state = FvgState.PARTIAL_FILL
    be = _engulf(bullish=False); be.maximum_retrace_ratio = 0.35; be.state = EngulfingState.PARTIAL_RETRACE
    engine._bull_fvg, engine._bear_fvg, engine._bear_engulf = bull, bear, be
    engine._bull_fvg_event = (120, FvgState.REACTION)
    engine._bear_fvg_event = (120, FvgState.FULL_FILL)
    engine._bear_engulf_event = (120, EngulfingState.INVALID)

    ex = engine._build_export(120)
    assert ex.bull_fvg.state == int(FvgState.PARTIAL_FILL)
    assert ex.bull_fvg.top == pytest.approx(106.0)
    assert ex.bull_fvg.bottom == 100.0
    assert ex.bull_fvg.event == int(FvgState.REACTION)
    assert ex.bear_fvg.state == -int(FvgState.PARTIAL_FILL)
    assert ex.bear_fvg.top == 110.0
    assert ex.bear_fvg.bottom == pytest.approx(103.0)
    assert ex.bear_fvg.event == -int(FvgState.FULL_FILL)
    assert ex.bear_engulf.state == -int(EngulfingState.PARTIAL_RETRACE)
    assert ex.bear_engulf.top == 110.0 and ex.bear_engulf.bottom == 100.0
    assert ex.bear_engulf.retrace == pytest.approx(0.35)
    assert ex.bear_engulf.event == -int(EngulfingState.INVALID)


def test_directional_terminal_events_do_not_overwrite_each_other_same_bar() -> None:
    engine = FvgEngulfingEngine()
    engine._bull_fvg_event = (50, FvgState.REACTION)
    engine._bear_fvg_event = (50, FvgState.FULL_FILL)
    ex = engine._build_export(50)
    assert ex.bull_fvg.event == int(FvgState.REACTION)
    assert ex.bear_fvg.event == -int(FvgState.FULL_FILL)


def test_event_ports_disappear_after_most_recent_closed_snapshot_bar() -> None:
    engine = FvgEngulfingEngine()
    engine._bull_fvg_event = (50, FvgState.REACTION)
    assert engine._build_export(50).bull_fvg.event == int(FvgState.REACTION)
    assert engine._build_export(51).bull_fvg.event is None


def test_public_engine_is_final_lifecycle_facade() -> None:
    assert FvgEngulfingEngine.__module__ == "financial_dashboard.engines.fvg_engulfing_final"
