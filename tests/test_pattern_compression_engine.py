from __future__ import annotations

import random

import pandas as pd
import pytest

from financial_dashboard.engines import PatternCompressionEngine
from financial_dashboard.engines.pattern_compression_core import (
    PATTERN_ASCENDING_TRIANGLE,
    PROFILE_BALANCED,
    ST_BREAK_CANDIDATE,
    ST_BREAK_CONFIRMED,
    PatternCompressionConfig,
)
from financial_dashboard.engines.pattern_compression_engine import PatternExport


def _config() -> PatternCompressionConfig:
    return PatternCompressionConfig(
        profile=PROFILE_BALANCED,
        use_manual=True,
        manual_pivot_len=2,
        manual_min_age=8,
        manual_min_touch_gap=2,
        manual_touch_atr=0.30,
        manual_min_contraction_pct=20.0,
        manual_min_break_strength=25.0,
        min_tick=0.01,
    )


def _bar(index: int, *, high: float, low: float, close: float | None = None, open_: float | None = None, volume: float = 1000.0, is_closed: bool = True) -> dict:
    resolved_close = (high + low) * 0.5 if close is None else close
    resolved_open = resolved_close - 0.10 if open_ is None else open_
    return {
        "timestamp": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=index),
        "open": resolved_open,
        "high": high,
        "low": low,
        "close": resolved_close,
        "volume": volume,
        "is_closed": is_closed,
    }


def _pivot_delay_bars() -> list[dict]:
    highs = [10.0, 11.0, 15.0, 11.5, 10.5, 10.8, 10.6, 10.9, 11.0]
    lows = [8.0, 8.5, 9.0, 8.7, 8.2, 7.5, 5.0, 7.8, 8.0]
    return [_bar(index, high=high, low=low) for index, (high, low) in enumerate(zip(highs, lows, strict=True))]


def _ascending_triangle_bars(*, include_breakout: bool = False) -> list[dict]:
    bars: list[dict] = []
    for index in range(24 if include_breakout else 21):
        lower_boundary = 96.8 + 0.4 * index
        high = 108.0 + 0.01 * index
        low = lower_boundary + 0.50
        if index in {4, 12}:
            high = 110.0
        if index == 8:
            low = 100.0
        if index == 16:
            low = 103.2
        close = (high + low) * 0.5
        open_ = close - 0.15
        if include_breakout and index == 22:
            high, low, open_, close = 112.4, 109.0, 109.6, 112.0
        if include_breakout and index == 23:
            high, low, open_, close = 112.8, 110.7, 111.0, 112.4
        bars.append(_bar(index, high=high, low=low, close=close, open_=open_, volume=1100.0 + index * 5.0))
    return bars


def test_centered_pivot_is_not_visible_before_right_side_confirmation() -> None:
    engine = PatternCompressionEngine(_config())
    bars = _pivot_delay_bars()

    for bar in bars[:4]:
        engine.update(bar)
    assert engine.pivot_store.high_bars == []

    engine.update(bars[4])
    store = engine.pivot_store
    assert store.high_bars == [2]
    assert store.high_confirm_bars == [4]


def test_accepting_opposite_pivot_locks_previous_opposite_swing() -> None:
    engine = PatternCompressionEngine(_config())
    for bar in _pivot_delay_bars():
        engine.update(bar)

    store = engine.pivot_store
    assert store.high_bars == [2]
    assert store.low_bars == [6]
    assert store.high_locked == [True]
    assert store.low_locked == [False]


def test_four_confirmed_pivots_create_natural_pattern_identity() -> None:
    engine = PatternCompressionEngine(_config())
    for bar in _ascending_triangle_bars():
        engine.update(bar)

    candidate = engine.active_candidate
    assert candidate.valid
    assert candidate.identity == 1
    assert candidate.pattern_type == PATTERN_ASCENDING_TRIANGLE
    assert candidate.hb1 == 4
    assert candidate.hb2 == 12
    assert candidate.lb1 == 8
    assert candidate.lb2 == 16
    assert candidate.known_bar == 18
    assert candidate.raw_quality >= engine.profile.min_raw_quality
    assert engine.snapshot() is not None
    assert engine.snapshot().state != "FORMASYON_YOK"


def test_strong_breakout_exports_candidate_then_confirmed_break() -> None:
    engine = PatternCompressionEngine(_config())
    bars = _ascending_triangle_bars(include_breakout=True)
    for bar in bars[:23]:
        engine.update(bar)

    first = engine.export_contract
    assert first is not None
    assert engine.pattern_state == ST_BREAK_CANDIDATE
    assert first.break_state == 2
    assert first.break_level == pytest.approx(110.0)
    assert first.break_strength is not None and first.break_strength >= 25.0
    assert first.classic_direction == 1
    assert engine.break_direction == 1

    engine.update(bars[23])
    confirmed = engine.export_contract
    assert confirmed is not None
    assert engine.pattern_state == ST_BREAK_CONFIRMED
    assert confirmed.break_state == 3
    assert confirmed.retest_state == 1
    assert confirmed.retest_tolerance is not None


def test_open_bar_is_strictly_non_mutating_even_with_extreme_prices() -> None:
    engine = PatternCompressionEngine(_config())
    for bar in _ascending_triangle_bars():
        engine.update(bar)

    before_snapshot = engine.snapshot()
    before_candidate = engine.active_candidate
    before_store = engine.pivot_store
    open_bar = _bar(
        999,
        high=999.0,
        low=1.0,
        close=900.0,
        open_=100.0,
        is_closed=False,
    )
    returned = engine.update(open_bar)

    assert returned == before_snapshot
    assert engine.snapshot() == before_snapshot
    assert engine.active_candidate == before_candidate
    assert engine.pivot_store == before_store


def test_replay_and_incremental_updates_are_exactly_equal() -> None:
    bars = _ascending_triangle_bars(include_breakout=True)
    frame = pd.DataFrame(bars)

    replay_engine = PatternCompressionEngine(_config())
    replay_results = replay_engine.replay(frame)

    incremental_engine = PatternCompressionEngine(_config())
    incremental_results = [incremental_engine.update(bar) for bar in bars]

    assert replay_results == incremental_results
    assert replay_engine.active_candidate == incremental_engine.active_candidate
    assert replay_engine.pivot_store == incremental_engine.pivot_store
    assert replay_engine.export_contract == incremental_engine.export_contract


def test_export_contract_has_exact_ten_pattern_ports() -> None:
    fields = tuple(PatternExport.__dataclass_fields__)
    assert fields == (
        "state",
        "pattern_type",
        "quality",
        "classic_direction",
        "break_state",
        "break_level",
        "break_strength",
        "retest_state",
        "retest_tolerance",
        "identity",
    )


def test_randomized_closed_bar_stress_is_deterministic_and_finite() -> None:
    random.seed(20260819)
    price = 100.0
    bars: list[dict] = []
    for index in range(250):
        move = random.uniform(-1.4, 1.4)
        open_ = price
        close = max(1.0, open_ + move)
        high = max(open_, close) + random.uniform(0.05, 0.9)
        low = min(open_, close) - random.uniform(0.05, 0.9)
        volume = random.uniform(500.0, 2500.0)
        bars.append(_bar(index, high=high, low=low, close=close, open_=open_, volume=volume))
        price = close

    first = PatternCompressionEngine()
    first_results = [first.update(bar) for bar in bars]
    second = PatternCompressionEngine()
    second_results = [second.update(bar) for bar in bars]

    assert len(first_results) == 250
    assert first_results == second_results
    assert first.export_contract == second.export_contract
    snapshot = first.snapshot()
    assert snapshot is not None
    if snapshot.score is not None:
        assert 0.0 <= snapshot.score <= 100.0
