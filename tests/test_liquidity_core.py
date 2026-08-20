import pytest

from financial_dashboard.engines.liquidity_core import (
    LiquidityCoreError,
    add_touch,
    apply_bar_event,
    cluster_touch,
    new_pool,
    pool_identity,
    same_pool,
    tolerance,
)
from financial_dashboard.engines.liquidity_models import (
    LiquidityConfig,
    LiquidityPoolState,
    LiquiditySide,
    LiquidityTouch,
)


CFG = LiquidityConfig(atr_tolerance=0.10, min_tick=0.01, min_touches_active=2)


def touch(price: float, bar: int) -> LiquidityTouch:
    return LiquidityTouch(timestamp=f"2026-08-19T{10 + bar:02d}:00:00+03:00", price=price, bar_index=bar)


def active_pool(side=LiquiditySide.BSL, level=100.0):
    pool = new_pool(side, touch(level, 0))
    return add_touch(pool, touch(level, 1), atr=1.0, config=CFG)


def test_tolerance_uses_atr_and_min_tick_floor():
    assert tolerance(2.0, CFG) == pytest.approx(0.2)
    assert tolerance(0.001, CFG) == pytest.approx(0.01)


def test_exact_equal_high_clusters_into_same_bsl_pool():
    pools, first = cluster_touch((), side=LiquiditySide.BSL, touch=touch(100.0, 0), atr=1.0, config=CFG)
    pools, second = cluster_touch(pools, side=LiquiditySide.BSL, touch=touch(100.0, 1), atr=1.0, config=CFG)
    assert len(pools) == 1
    assert first.identity == second.identity
    assert second.state is LiquidityPoolState.ACTIVE
    assert second.touch_count == 2


def test_nearby_high_inside_tolerance_clusters_and_updates_mean_level():
    pools, _ = cluster_touch((), side=LiquiditySide.BSL, touch=touch(100.0, 0), atr=1.0, config=CFG)
    pools, updated = cluster_touch(pools, side=LiquiditySide.BSL, touch=touch(100.08, 1), atr=1.0, config=CFG)
    assert len(pools) == 1
    assert updated.level == pytest.approx(100.04)


def test_price_outside_tolerance_creates_new_pool():
    pools, _ = cluster_touch((), side=LiquiditySide.BSL, touch=touch(100.0, 0), atr=1.0, config=CFG)
    pools, _ = cluster_touch(pools, side=LiquiditySide.BSL, touch=touch(100.11, 1), atr=1.0, config=CFG)
    assert len(pools) == 2


def test_bsl_and_ssl_never_cluster_together():
    pools, _ = cluster_touch((), side=LiquiditySide.BSL, touch=touch(100.0, 0), atr=1.0, config=CFG)
    pools, ssl = cluster_touch(pools, side=LiquiditySide.SSL, touch=touch(100.0, 1), atr=1.0, config=CFG)
    assert len(pools) == 2
    assert ssl.side is LiquiditySide.SSL


def test_duplicate_bar_touch_is_idempotent():
    pool = new_pool(LiquiditySide.BSL, touch(100.0, 0))
    unchanged = add_touch(pool, touch(100.0, 0), atr=1.0, config=CFG)
    assert unchanged == pool


def test_identity_is_deterministic_and_side_sensitive():
    t = touch(100.0, 3)
    assert pool_identity(LiquiditySide.BSL, t) == pool_identity(LiquiditySide.BSL, t)
    assert pool_identity(LiquiditySide.BSL, t) != pool_identity(LiquiditySide.SSL, t)


def test_terminal_pool_is_not_revived_by_clustering():
    pool = active_pool()
    consumed = apply_bar_event(pool, high=100.5, low=99.8, close=100.3, timestamp="x", atr=1.0, config=CFG)
    assert consumed.state is LiquidityPoolState.CONSUMED
    pools, chosen = cluster_touch((consumed,), side=LiquiditySide.BSL, touch=touch(100.0, 4), atr=1.0, config=CFG)
    assert len(pools) == 2
    assert chosen.identity != consumed.identity


def test_bsl_wick_through_and_close_back_below_is_sweep():
    pool = active_pool(LiquiditySide.BSL)
    out = apply_bar_event(pool, high=100.25, low=99.8, close=99.95, timestamp="s", atr=1.0, config=CFG)
    assert out.state is LiquidityPoolState.SWEPT
    assert out.last_event == "SWEEP"


def test_bsl_close_above_pool_is_consumption_not_sweep():
    pool = active_pool(LiquiditySide.BSL)
    out = apply_bar_event(pool, high=100.25, low=99.8, close=100.15, timestamp="c", atr=1.0, config=CFG)
    assert out.state is LiquidityPoolState.CONSUMED
    assert out.last_event == "CONSUME"


def test_ssl_wick_through_and_close_back_above_is_sweep():
    pool = active_pool(LiquiditySide.SSL)
    out = apply_bar_event(pool, high=100.2, low=99.75, close=100.05, timestamp="s", atr=1.0, config=CFG)
    assert out.state is LiquidityPoolState.SWEPT


def test_ssl_close_below_pool_is_consumption_not_sweep():
    pool = active_pool(LiquiditySide.SSL)
    out = apply_bar_event(pool, high=100.2, low=99.75, close=99.85, timestamp="c", atr=1.0, config=CFG)
    assert out.state is LiquidityPoolState.CONSUMED


def test_active_pool_can_be_tested_without_breach():
    pool = active_pool(LiquiditySide.BSL)
    out = apply_bar_event(pool, high=99.95, low=99.7, close=99.8, timestamp="t", atr=1.0, config=CFG)
    assert out.state is LiquidityPoolState.TESTED
    assert out.last_event == "TEST"


def test_swept_bsl_reclaims_on_subsequent_close_back_inside_even_with_retouch():
    pool = active_pool(LiquiditySide.BSL)
    swept = apply_bar_event(pool, high=100.25, low=99.8, close=99.95, timestamp="s", atr=1.0, config=CFG)
    reclaimed = apply_bar_event(swept, high=100.05, low=99.7, close=99.90, timestamp="r", atr=1.0, config=CFG)
    assert reclaimed.state is LiquidityPoolState.RECLAIMED
    assert reclaimed.last_event == "RECLAIM"


def test_swept_ssl_reclaims_on_subsequent_close_back_inside_even_with_retouch():
    pool = active_pool(LiquiditySide.SSL)
    swept = apply_bar_event(pool, high=100.2, low=99.75, close=100.05, timestamp="s", atr=1.0, config=CFG)
    reclaimed = apply_bar_event(swept, high=100.2, low=99.95, close=100.10, timestamp="r", atr=1.0, config=CFG)
    assert reclaimed.state is LiquidityPoolState.RECLAIMED


def test_out_of_tolerance_touch_fails_closed():
    pool = new_pool(LiquiditySide.BSL, touch(100.0, 0))
    with pytest.raises(LiquidityCoreError):
        add_touch(pool, touch(100.2, 1), atr=1.0, config=CFG)


def test_same_pool_boundary_is_inclusive():
    assert same_pool(100.1, 100.0, atr=1.0, config=CFG)
