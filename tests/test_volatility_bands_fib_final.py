from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from financial_dashboard.engines.volatility_bands_fib_engine import VolatilityBandsConfig
from financial_dashboard.engines.volatility_bands_fib_final import (
    BreakCandidate,
    CoherenceState,
    FibonacciState,
    MeaningfulPivot,
    PIVOT_HIGH,
    PIVOT_LOW,
    StructureState,
    VolatilityBandsFibEngine,
)

TZ = "Europe/Istanbul"


def _frame(n: int = 170) -> pd.DataFrame:
    ts = pd.date_range("2026-01-02 10:00", periods=n, freq="2h", tz=TZ)
    x = np.linspace(100.0, 145.0, n) + np.sin(np.arange(n) / 4.0) * 2.0
    return pd.DataFrame(
        {
            "timestamp": ts,
            "open": x - 0.25,
            "high": x + 0.9,
            "low": x - 1.0,
            "close": x,
            "volume": 1000.0,
            "is_closed": True,
            "is_complete": True,
        }
    )


def _rows(n: int = 120, close: float = 106.0) -> list[dict]:
    rows = []
    for i in range(n):
        c = close - (n - 1 - i) * 0.02
        rows.append(
            {
                "timestamp": pd.Timestamp("2026-01-01", tz=TZ) + pd.Timedelta(hours=2 * i),
                "open": c - 0.3,
                "high": c + 0.6,
                "low": c - 0.7,
                "close": c,
                "volume": 1000.0,
                "is_closed": True,
                "is_complete": True,
            }
        )
    return rows


def test_confirmed_pivot_is_known_only_after_right_side_length() -> None:
    engine = VolatilityBandsFibEngine(VolatilityBandsConfig(profile="Dengeli"))
    engine._rows = _rows(9, 100.0)
    for i, row in enumerate(engine._rows):
        row["high"] = 101.0 + i * 0.1
        row["low"] = 99.0 - i * 0.05
    engine._rows[4]["high"] = 120.0
    engine._rows[4]["low"] = 99.5
    high, low = engine._detect_confirmed_pivots([2.0] * 9)
    assert high is not None
    assert high.source_index == 4
    assert high.known_index == 8
    assert high.known_index - high.source_index == engine.pivot_length
    assert low is None


def test_dual_pivot_confirmation_is_ignored() -> None:
    engine = VolatilityBandsFibEngine()
    engine._rows = _rows(9, 100.0)
    for row in engine._rows:
        row["high"] = 105.0
        row["low"] = 95.0
    engine._rows[4]["high"] = 120.0
    engine._rows[4]["low"] = 80.0
    high, low = engine._detect_confirmed_pivots([2.0] * 9)
    assert high is not None and low is not None
    engine._accept_pivot(high, low, data_ready=True)
    assert engine._accepted_pivot is None


def test_same_type_more_extreme_pivot_replaces_without_advancing_history() -> None:
    engine = VolatilityBandsFibEngine()
    first = MeaningfulPivot(PIVOT_HIGH, 110.0, 2.0, 10, 14)
    engine._accept_pivot(first, None, data_ready=True)
    replacement = MeaningfulPivot(PIVOT_HIGH, 112.0, 2.2, 15, 19)
    engine._accept_pivot(replacement, None, data_ready=True)
    assert engine._accepted_pivot == replacement
    assert engine._last_high == replacement
    assert engine._previous_high is None


def test_opposite_pivot_requires_range_and_bar_distance() -> None:
    engine = VolatilityBandsFibEngine(VolatilityBandsConfig(profile="Dengeli"))
    high = MeaningfulPivot(PIVOT_HIGH, 110.0, 2.0, 10, 14)
    engine._accept_pivot(high, None, data_ready=True)
    too_close = MeaningfulPivot(PIVOT_LOW, 108.0, 2.0, 12, 16)
    engine._accept_pivot(None, too_close, data_ready=True)
    assert engine._accepted_pivot == high
    accepted = MeaningfulPivot(PIVOT_LOW, 106.0, 2.0, 15, 19)
    engine._accept_pivot(None, accepted, data_ready=True)
    assert engine._accepted_pivot == accepted
    assert engine._last_low == accepted


def test_break_candidate_freezes_reference_atr_buffer_and_identity() -> None:
    engine = VolatilityBandsFibEngine(VolatilityBandsConfig(profile="Dengeli"))
    engine._rows = _rows(120, 112.0)
    pivot = MeaningfulPivot(PIVOT_HIGH, 110.0, 2.0, 100, 104)
    engine._last_high = pivot
    m = {
        "close": 111.0,
        "close_location": 0.8,
        "net_atr": 0.8,
        "strong_counter_down": False,
        "strong_counter_up": False,
    }
    up_cand, _, up_conf, _ = engine._update_break_candidate(
        m=m, atr_now=20.0, up_expansion_confirmed=False, down_expansion_confirmed=False
    )
    assert up_cand and not up_conf
    first = engine._break_candidate
    assert first.reference_level == 110.0
    assert first.reference_atr == 2.0
    assert first.buffer_price == pytest.approx(2.0 * engine.structure_break_buffer_atr)
    assert first.reference_pivot_index == 100
    assert first.consecutive_bars == 1

    engine._update_break_candidate(
        m=m, atr_now=50.0, up_expansion_confirmed=False, down_expansion_confirmed=False
    )
    second = engine._break_candidate
    assert second.reference_atr == first.reference_atr
    assert second.buffer_price == first.buffer_price
    assert second.reference_pivot_index == first.reference_pivot_index
    assert second.consecutive_bars == 2


def test_new_pivot_identity_cannot_continue_old_break_candidate() -> None:
    engine = VolatilityBandsFibEngine()
    engine._rows = _rows(120, 112.0)
    engine._last_high = MeaningfulPivot(PIVOT_HIGH, 110.0, 2.0, 100, 104)
    m = {"close": 111.0, "close_location": 0.8, "net_atr": 0.8, "strong_counter_down": False, "strong_counter_up": False}
    engine._update_break_candidate(m=m, atr_now=3.0, up_expansion_confirmed=False, down_expansion_confirmed=False)
    assert engine._break_candidate.reference_pivot_index == 100
    engine._last_high = MeaningfulPivot(PIVOT_HIGH, 110.5, 2.5, 105, 109)
    engine._update_break_candidate(m=m, atr_now=30.0, up_expansion_confirmed=False, down_expansion_confirmed=False)
    assert engine._break_candidate.reference_pivot_index == 105
    assert engine._break_candidate.consecutive_bars == 1


def test_fibonacci_invalidation_uses_frozen_swing_reference_atr() -> None:
    engine = VolatilityBandsFibEngine()
    engine._rows = _rows(120, 99.0)
    engine._rows[-1].update(open=99.4, high=99.8, low=98.5, close=98.8)
    swing = {"valid": True, "direction": 1, "start": 100.0, "end": 110.0, "start_i": 90, "end_i": 100, "range": 10.0, "reference_atr": 2.0, "range_atr": 5.0, "distance": 10, "age": 19}
    m = {"open": 99.4, "high": 99.8, "low": 98.5, "close": 98.8, "net_atr": -0.5, "efficiency": 0.8, "close_location": 0.2, "body_to_prior_atr": 0.8}
    fib = engine._fibonacci(swing=swing, m=m, bull_seq=True, bear_seq=False, up_break_conf=False, dn_break_conf=False)
    assert fib["invalid_level"] == pytest.approx(100.0 - 2.0 * engine.fib_invalidation_buffer_atr)
    assert fib["state"] == FibonacciState.INVALIDATED


def test_fibonacci_reclaim_requires_same_active_swing_identity() -> None:
    engine = VolatilityBandsFibEngine()
    engine._rows = _rows(120, 104.0)
    swing = {"valid": True, "direction": 1, "start": 100.0, "end": 110.0, "start_i": 90, "end_i": 100, "range": 10.0, "reference_atr": 2.0, "range_atr": 5.0, "distance": 10, "age": 19}
    prior = {"open": 103.5, "high": 104.5, "low": 103.0, "close": 104.0, "net_atr": -0.2, "efficiency": 0.8, "close_location": 0.7, "body_to_prior_atr": 0.8}
    engine._fibonacci(swing=swing, m=prior, bull_seq=True, bear_seq=False, up_break_conf=False, dn_break_conf=False)
    engine._rows[-2]["close"] = 104.0
    current = {"open": 105.2, "high": 106.5, "low": 105.0, "close": 106.0, "net_atr": 0.7, "efficiency": 0.8, "close_location": 0.67, "body_to_prior_atr": 0.8}
    same = engine._fibonacci(swing=swing, m=current, bull_seq=True, bear_seq=False, up_break_conf=False, dn_break_conf=False)
    assert same["state"] == FibonacciState.RECLAIM

    engine._last_fib_identity = (80, 95, 1)
    engine._last_fib_ratio = 0.60
    different = engine._fibonacci(swing=swing, m=current, bull_seq=True, bear_seq=False, up_break_conf=False, dn_break_conf=False)
    assert different["state"] != FibonacciState.RECLAIM


def test_final_replay_matches_incremental_and_future_tail_is_invariant() -> None:
    frame = _frame(175)
    replay_engine = VolatilityBandsFibEngine()
    replay_results = replay_engine.replay(frame)
    incremental = VolatilityBandsFibEngine()
    for _, bar in frame.iterrows():
        incremental.update(bar)
    assert incremental.snapshot() == replay_results[-1]
    assert incremental.final_export == replay_engine.final_export

    prefix = frame.iloc[:150].copy()
    a = VolatilityBandsFibEngine()
    a.replay(prefix)
    b = VolatilityBandsFibEngine()
    results = b.replay(frame)
    assert results[149] == a.snapshot()


def test_open_and_source_gap_bars_freeze_full_final_snapshot() -> None:
    engine = VolatilityBandsFibEngine()
    frame = _frame(160)
    engine.replay(frame)
    before = engine.snapshot()
    before_export = engine.final_export
    open_bar = frame.iloc[-1].to_dict()
    open_bar["timestamp"] = pd.Timestamp("2026-09-20", tz=TZ)
    open_bar["close"] = 9999.0
    open_bar["high"] = 10000.0
    open_bar["is_closed"] = False
    assert engine.update(open_bar) == before
    assert engine.final_export == before_export

    gap_bar = frame.iloc[-1].to_dict()
    gap_bar["timestamp"] = pd.Timestamp("2026-09-21", tz=TZ)
    gap_bar["is_complete"] = False
    assert engine.update(gap_bar) == before
    assert engine.final_export == before_export


def test_final_export_keeps_official_vol_ports_and_adds_internal_audit_state() -> None:
    engine = VolatilityBandsFibEngine()
    result = engine.replay(_frame(170))[-1]
    assert engine.final_export.regime is not None
    assert engine.final_export.direction in {-2.0, -1.0, 0.0, 1.0, 2.0}
    assert engine.final_export.quality is not None
    assert engine.final_export.band_state is not None
    assert engine.final_export.band_agreement is not None
    assert engine.final_export.structure_state in {s.value for s in StructureState}
    assert engine.final_export.coherence in {s.value for s in CoherenceState}
    if engine.final_export.fib_state is not None:
        assert engine.final_export.fib_state not in {FibonacciState.PENDING.value, FibonacciState.UNAVAILABLE.value}
    assert result.state.startswith("COHERENCE_")
