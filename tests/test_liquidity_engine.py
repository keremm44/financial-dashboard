import pandas as pd
import pytest

from financial_dashboard.engines.liquidity_core import add_touch, new_pool
from financial_dashboard.engines.liquidity_engine import LiquidityEngine
from financial_dashboard.engines.liquidity_models import LiquidityConfig, LiquidityPoolState, LiquiditySide, LiquidityTouch
from financial_dashboard.engines.models import Direction


CFG = LiquidityConfig(
    atr_tolerance=0.10,
    min_tick=0.01,
    min_touches_active=2,
    test_tolerance_factor=1.0,
    pivot_span=1,
    atr_length=3,
)


def bar(i, high, low, close, open_=None, closed=True):
    return {
        "timestamp": pd.Timestamp("2026-08-19 10:00", tz="Europe/Istanbul") + pd.Timedelta(minutes=5 * i),
        "open": float(close if open_ is None else open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "is_closed": closed,
    }


def build_active_bsl_engine():
    engine = LiquidityEngine(CFG)
    rows = [
        bar(0, 99.0, 98.0, 98.5),
        bar(1, 100.0, 98.5, 99.0),
        bar(2, 99.0, 98.2, 98.7),
        bar(3, 100.0, 98.6, 99.1),
        bar(4, 99.0, 98.3, 98.8),
    ]
    for row in rows:
        engine.update(row)
    bsl = [p for p in engine.pools if p.side is LiquiditySide.BSL]
    assert len(bsl) == 1
    assert bsl[0].state is LiquidityPoolState.ACTIVE
    return engine


def active_pool(side, level):
    first = LiquidityTouch("a", level, 0)
    second = LiquidityTouch("b", level, 1)
    return add_touch(new_pool(side, first), second, atr=1.0, config=CFG)


def test_confirmed_equal_pivots_form_one_active_bsl_pool():
    engine = build_active_bsl_engine()
    pool = [p for p in engine.pools if p.side is LiquiditySide.BSL][0]
    assert pool.touch_count == 2
    assert pool.level == pytest.approx(100.0)
    assert engine.export_contract.active_bsl_count >= 1


def test_forming_single_touch_pool_cannot_emit_liquidity_signal():
    engine = LiquidityEngine(CFG)
    engine._pools = (new_pool(LiquiditySide.BSL, LiquidityTouch("x", 100.0, 0)),)
    result = engine.update(bar(0, 100.5, 99.0, 99.8))
    assert result.direction is Direction.NEUTRAL
    assert result.state == "LIQUIDITY_NEUTRAL"
    assert engine.pools[0].state is LiquidityPoolState.FORMING


def test_active_bsl_sweep_is_bearish_liquidity_event():
    engine = build_active_bsl_engine()
    result = engine.update(bar(5, 100.4, 98.7, 99.7))
    assert result.direction is Direction.DOWN
    assert result.state == "BSL_SWEEP"
    assert result.score < 0
    assert any("BSL sweep" in reason for reason in result.reasons)
    export = engine.export_contract
    assert export.latest_event_side == "BSL"
    assert export.latest_event_state == "SWEEP"
    assert export.latest_event_direction == -1


def test_active_ssl_sweep_is_bullish_liquidity_event():
    engine = LiquidityEngine(CFG)
    engine._pools = (active_pool(LiquiditySide.SSL, 99.0),)
    result = engine.update(bar(0, 100.0, 98.6, 99.3))
    assert result.direction is Direction.UP
    assert result.state == "SSL_SWEEP"
    assert result.score > 0
    assert engine.export_contract.latest_event_direction == 1


def test_close_through_consumption_is_not_directional_reversal_signal():
    engine = LiquidityEngine(CFG)
    engine._pools = (active_pool(LiquiditySide.BSL, 100.0),)
    result = engine.update(bar(0, 100.5, 99.5, 100.3))
    assert result.direction is Direction.NEUTRAL
    assert result.state == "LIQUIDITY_NEUTRAL"
    assert engine.pools[0].state is LiquidityPoolState.CONSUMED
    assert any("liquidity consumed" in reason for reason in result.reasons)


def test_opposite_side_sweeps_same_bar_surface_conflict_not_vote():
    engine = LiquidityEngine(CFG)
    engine._pools = (
        active_pool(LiquiditySide.BSL, 101.0),
        active_pool(LiquiditySide.SSL, 99.0),
    )
    result = engine.update(bar(0, 101.4, 98.6, 100.0))
    assert result.direction is Direction.NEUTRAL
    assert result.state == "LIQUIDITY_CONFLICT"
    assert result.score == 0


def test_reclaim_has_higher_quality_than_plain_sweep_for_same_pool_strength():
    engine = LiquidityEngine(CFG)
    engine._pools = (active_pool(LiquiditySide.SSL, 99.0),)
    sweep = engine.update(bar(0, 100.0, 98.6, 99.3))
    assert sweep.state == "SSL_SWEEP"
    reclaim = engine.update(bar(1, 99.8, 98.9, 99.4))
    assert reclaim.state == "SSL_RECLAIM"
    assert reclaim.quality > sweep.quality


def test_nearest_public_levels_are_exported_without_direction_signal():
    engine = LiquidityEngine(CFG)
    engine._pools = (
        active_pool(LiquiditySide.BSL, 101.0),
        active_pool(LiquiditySide.SSL, 99.0),
    )
    result = engine.update(bar(0, 100.2, 99.8, 100.0))
    assert result.direction is Direction.NEUTRAL
    assert result.levels["nearest_bsl"] == pytest.approx(101.0)
    assert result.levels["nearest_ssl"] == pytest.approx(99.0)
    assert engine.export_contract.nearest_bsl == pytest.approx(101.0)
    assert engine.export_contract.nearest_ssl == pytest.approx(99.0)


def test_replay_returns_confirmed_engine_results_and_snapshot_matches_last():
    engine = LiquidityEngine(CFG)
    frame = pd.DataFrame([
        bar(0, 99.0, 98.0, 98.5),
        bar(1, 100.0, 98.5, 99.0),
        bar(2, 99.0, 98.2, 98.7),
        bar(3, 100.0, 98.6, 99.1),
        bar(4, 99.0, 98.3, 98.8),
        bar(5, 100.4, 98.7, 99.7),
    ])
    history = engine.replay(frame)
    assert len(history) == len(frame)
    assert all(item.is_confirmed for item in history)
    assert engine.snapshot() == history[-1]
    assert history[-1].state == "BSL_SWEEP"


def test_unclosed_bar_does_not_mutate_snapshot():
    engine = build_active_bsl_engine()
    before = engine.snapshot()
    pools_before = engine.pools
    out = engine.update(bar(5, 100.5, 98.0, 99.5, closed=False))
    assert out == before
    assert engine.snapshot() == before
    assert engine.pools == pools_before


def test_missing_closed_ohlc_fails_closed():
    engine = LiquidityEngine(CFG)
    with pytest.raises(ValueError):
        engine.update({"timestamp": "x", "open": 1, "high": 2, "close": 1.5, "is_closed": True})
