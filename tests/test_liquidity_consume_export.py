import pandas as pd

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


def _bar(i: int, high: float, low: float, close: float):
    return {
        "timestamp": pd.Timestamp("2026-08-20 10:00", tz="Europe/Istanbul") + pd.Timedelta(minutes=5 * i),
        "open": float(close),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "is_closed": True,
    }


def _active_pool(side: LiquiditySide, level: float):
    first = LiquidityTouch("a", level, 0)
    second = LiquidityTouch("b", level, 1)
    return add_touch(new_pool(side, first), second, atr=1.0, config=CFG)


def test_bsl_consume_is_structured_continuation_fact_without_reversal_vote():
    engine = LiquidityEngine(CFG)
    engine._pools = (_active_pool(LiquiditySide.BSL, 100.0),)

    result = engine.update(_bar(0, 100.5, 99.5, 100.3))
    export = engine.export_contract

    assert result.direction is Direction.NEUTRAL
    assert result.state == "LIQUIDITY_NEUTRAL"
    assert engine.pools[0].state is LiquidityPoolState.CONSUMED
    assert export.latest_event_state is None
    assert export.latest_consume_side == "BSL"
    assert export.latest_consume_level == 100.0
    assert export.latest_consume_identity == engine.pools[0].identity
    assert export.latest_consume_direction == 1


def test_ssl_consume_is_structured_down_continuation_fact_without_reversal_vote():
    engine = LiquidityEngine(CFG)
    engine._pools = (_active_pool(LiquiditySide.SSL, 99.0),)

    result = engine.update(_bar(0, 99.5, 98.5, 98.7))
    export = engine.export_contract

    assert result.direction is Direction.NEUTRAL
    assert result.state == "LIQUIDITY_NEUTRAL"
    assert engine.pools[0].state is LiquidityPoolState.CONSUMED
    assert export.latest_event_state is None
    assert export.latest_consume_side == "SSL"
    assert export.latest_consume_level == 99.0
    assert export.latest_consume_identity == engine.pools[0].identity
    assert export.latest_consume_direction == -1


def test_consume_export_is_current_bar_event_memory_not_permanent_vote():
    engine = LiquidityEngine(CFG)
    engine._pools = (_active_pool(LiquiditySide.BSL, 100.0),)
    engine.update(_bar(0, 100.5, 99.5, 100.3))
    assert engine.export_contract.latest_consume_side == "BSL"

    engine.update(_bar(1, 101.0, 100.2, 100.7))
    assert engine.export_contract.latest_consume_side is None
    assert engine.export_contract.latest_consume_level is None
    assert engine.export_contract.latest_consume_identity is None
    assert engine.export_contract.latest_consume_direction == 0
