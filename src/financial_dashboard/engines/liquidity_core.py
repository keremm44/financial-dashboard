from __future__ import annotations

from dataclasses import replace
from hashlib import sha1

from .liquidity_models import (
    LiquidityConfig,
    LiquidityPool,
    LiquidityPoolState,
    LiquiditySide,
    LiquidityTouch,
)


class LiquidityCoreError(ValueError):
    pass


def tolerance(atr: float | None, config: LiquidityConfig) -> float:
    safe_atr = max(float(atr or 0.0), config.min_tick)
    return max(config.min_tick, safe_atr * config.atr_tolerance)


def same_pool(price: float, level: float, atr: float | None, config: LiquidityConfig) -> bool:
    return abs(float(price) - float(level)) <= tolerance(atr, config)


def pool_identity(side: LiquiditySide, first_touch: LiquidityTouch) -> str:
    raw = f"{side.value}|{first_touch.bar_index}|{float(first_touch.price):.10f}"
    return f"LQ-{sha1(raw.encode('utf-8')).hexdigest()[:12]}"


def new_pool(side: LiquiditySide, touch: LiquidityTouch) -> LiquidityPool:
    return LiquidityPool(
        identity=pool_identity(side, touch),
        side=side,
        level=float(touch.price),
        state=LiquidityPoolState.FORMING,
        touches=(touch,),
        created_at=touch.timestamp,
        updated_at=touch.timestamp,
        last_event="POOL_CREATED",
    )


def add_touch(pool: LiquidityPool, touch: LiquidityTouch, atr: float | None, config: LiquidityConfig) -> LiquidityPool:
    if pool.state in {LiquidityPoolState.CONSUMED, LiquidityPoolState.INVALIDATED}:
        raise LiquidityCoreError("terminal liquidity pool cannot accept new touches")
    if any(existing.bar_index == touch.bar_index for existing in pool.touches):
        return pool
    if not same_pool(touch.price, pool.level, atr, config):
        raise LiquidityCoreError("touch is outside pool tolerance")
    updated = pool.with_touch(touch)
    state = updated.state
    if updated.touch_count >= config.min_touches_active and state is LiquidityPoolState.FORMING:
        state = LiquidityPoolState.ACTIVE
    return replace(updated, state=state, last_event="POOL_TOUCH")


def classify_bar_event(
    pool: LiquidityPool,
    *,
    high: float,
    low: float,
    close: float,
    atr: float | None,
    config: LiquidityConfig,
) -> str | None:
    tol = tolerance(atr, config) * config.test_tolerance_factor
    level = pool.level

    if pool.side is LiquiditySide.BSL:
        if high > level + tol:
            return "SWEEP" if close <= level else "CONSUME"
        if high >= level - tol:
            return "TEST"
        if pool.state is LiquidityPoolState.SWEPT and close < level:
            return "RECLAIM"
    else:
        if low < level - tol:
            return "SWEEP" if close >= level else "CONSUME"
        if low <= level + tol:
            return "TEST"
        if pool.state is LiquidityPoolState.SWEPT and close > level:
            return "RECLAIM"
    return None


def apply_bar_event(
    pool: LiquidityPool,
    *,
    high: float,
    low: float,
    close: float,
    timestamp,
    atr: float | None,
    config: LiquidityConfig,
) -> LiquidityPool:
    if pool.state in {LiquidityPoolState.CONSUMED, LiquidityPoolState.INVALIDATED}:
        return pool

    event = classify_bar_event(pool, high=high, low=low, close=close, atr=atr, config=config)
    if event is None:
        return pool

    state = pool.state
    if event == "TEST" and state in {LiquidityPoolState.FORMING, LiquidityPoolState.ACTIVE}:
        state = LiquidityPoolState.TESTED
    elif event == "SWEEP":
        state = LiquidityPoolState.SWEPT
    elif event == "RECLAIM" and state is LiquidityPoolState.SWEPT:
        state = LiquidityPoolState.RECLAIMED
    elif event == "CONSUME":
        state = LiquidityPoolState.CONSUMED

    return replace(pool, state=state, updated_at=timestamp, last_event=event)
