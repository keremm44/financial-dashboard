from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from financial_dashboard.engines.liquidity_behavior import (
    LiquidityBehaviorConfig,
    LiquidityBehaviorTracker,
    LiquidityLandscapeState,
    LiquidityPoolMaturity,
    LiquidityPriceRelation,
    LiquidityRemovalState,
)
from financial_dashboard.engines.liquidity_models import (
    LiquidityPool,
    LiquidityPoolState,
    LiquiditySide,
    LiquidityTouch,
)


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _pool(
    identity: str,
    *,
    side: LiquiditySide,
    level: float,
    state: LiquidityPoolState,
    touch_indexes: tuple[int, ...],
) -> LiquidityPool:
    touches = tuple(
        LiquidityTouch(timestamp=NOW, price=level, bar_index=index)
        for index in touch_indexes
    )
    return LiquidityPool(
        identity=identity,
        side=side,
        level=level,
        state=state,
        touches=touches,
        created_at=NOW,
        updated_at=NOW,
        last_event=None,
    )


def test_maturity_and_approach_are_separate_dimensions() -> None:
    tracker = LiquidityBehaviorTracker(
        LiquidityBehaviorConfig(mature_touches=3, stale_bars=20)
    )
    pool = _pool(
        "BSL-1",
        side=LiquiditySide.BSL,
        level=110.0,
        state=LiquidityPoolState.ACTIVE,
        touch_indexes=(2, 5, 8),
    )

    tracker.update(
        (pool,),
        bar_index=10,
        timestamp=NOW,
        high=105.0,
        low=103.0,
        close=104.0,
        atr=4.0,
    )
    snapshot = tracker.update(
        (pool,),
        bar_index=11,
        timestamp=NOW,
        high=108.0,
        low=106.0,
        close=107.5,
        atr=4.0,
    )

    row = snapshot.for_pool("BSL-1")
    assert row.maturity is LiquidityPoolMaturity.MATURE
    assert row.relation is LiquidityPriceRelation.APPROACHING
    assert row.distance_delta_atr is not None and row.distance_delta_atr < 0


def test_sweep_reclaim_and_consume_aftermath_are_descriptive_only() -> None:
    tracker = LiquidityBehaviorTracker(
        LiquidityBehaviorConfig(acceptance_bars=2)
    )
    swept = _pool(
        "SSL-1",
        side=LiquiditySide.SSL,
        level=100.0,
        state=LiquidityPoolState.SWEPT,
        touch_indexes=(1, 3),
    )
    snapshot = tracker.update(
        (swept,),
        bar_index=5,
        timestamp=NOW,
        high=101.0,
        low=98.0,
        close=100.5,
        atr=2.0,
    )
    assert snapshot.for_pool("SSL-1").removal is LiquidityRemovalState.SWEEP_REJECTING

    reclaimed = replace(swept, state=LiquidityPoolState.RECLAIMED, last_event="RECLAIM")
    snapshot = tracker.update(
        (reclaimed,),
        bar_index=6,
        timestamp=NOW,
        high=102.0,
        low=100.0,
        close=101.0,
        atr=2.0,
    )
    assert snapshot.for_pool("SSL-1").removal is LiquidityRemovalState.SWEEP_RECLAIMED

    consumed = replace(reclaimed, state=LiquidityPoolState.CONSUMED, last_event="CONSUME")
    first = tracker.update(
        (consumed,),
        bar_index=7,
        timestamp=NOW,
        high=101.0,
        low=97.0,
        close=98.0,
        atr=2.0,
    )
    second = tracker.update(
        (consumed,),
        bar_index=8,
        timestamp=NOW,
        high=100.0,
        low=96.0,
        close=97.0,
        atr=2.0,
    )
    assert first.for_pool("SSL-1").removal is LiquidityRemovalState.ACCEPTED_BEYOND
    assert second.for_pool("SSL-1").removal is LiquidityRemovalState.CONSUMED


def test_nearby_bsl_and_ssl_are_competing_objectives_without_direction_vote() -> None:
    tracker = LiquidityBehaviorTracker()
    bsl = _pool(
        "BSL-1",
        side=LiquiditySide.BSL,
        level=104.0,
        state=LiquidityPoolState.ACTIVE,
        touch_indexes=(1, 2),
    )
    ssl = _pool(
        "SSL-1",
        side=LiquiditySide.SSL,
        level=96.0,
        state=LiquidityPoolState.TESTED,
        touch_indexes=(1, 3),
    )

    snapshot = tracker.update(
        (bsl, ssl),
        bar_index=5,
        timestamp=NOW,
        high=101.0,
        low=99.0,
        close=100.0,
        atr=3.0,
    )

    assert snapshot.landscape is LiquidityLandscapeState.COMPETING_OBJECTIVES


def test_stale_pool_is_not_treated_as_mature_just_because_it_has_many_touches() -> None:
    tracker = LiquidityBehaviorTracker(
        LiquidityBehaviorConfig(mature_touches=3, stale_bars=5)
    )
    pool = _pool(
        "BSL-old",
        side=LiquiditySide.BSL,
        level=120.0,
        state=LiquidityPoolState.ACTIVE,
        touch_indexes=(1, 2, 3),
    )

    snapshot = tracker.update(
        (pool,),
        bar_index=10,
        timestamp=NOW,
        high=111.0,
        low=109.0,
        close=110.0,
        atr=4.0,
    )

    assert snapshot.for_pool("BSL-old").maturity is LiquidityPoolMaturity.STALE
