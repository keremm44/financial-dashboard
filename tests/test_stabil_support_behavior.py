from __future__ import annotations

import pandas as pd
import pytest

from financial_dashboard.engines.stabil_support_behavior import (
    PriceSupportRelation,
    SupportInteractionState,
    SupportMotion,
    build_support_behavior,
)
from financial_dashboard.engines.stabil_support_lifecycle import (
    DailySupportObservation,
    StabilSupportLifecycleEngine,
    build_support_lifecycle,
)
from financial_dashboard.stabil_support_replay import (
    StabilSupportHistoricalReplayRunner,
    StabilSupportReplayRunner,
)
from _ui_test_data import make_ui_store


TZ = "Europe/Istanbul"


def _obs(
    day: int,
    *,
    close: float,
    high: float | None = None,
    low: float | None = None,
    support: float = 100.0,
    floor: float | None = None,
    origin_day: int = 1,
    confirmed_day: int = 3,
    available_day: int = 3,
    atr: float = 2.0,
) -> DailySupportObservation:
    ts = pd.Timestamp(f"2026-01-{day:02d} 18:10", tz=TZ)
    return DailySupportObservation(
        timestamp=ts,
        high=float(close + 1.0 if high is None else high),
        low=float(close - 1.0 if low is None else low),
        close=float(close),
        atr=float(atr),
        support_level=float(support),
        support_floor=float(support - 2.0 if floor is None else floor),
        support_origin_at=pd.Timestamp(f"2026-01-{origin_day:02d} 18:10", tz=TZ),
        support_confirmed_at=pd.Timestamp(f"2026-01-{confirmed_day:02d} 18:10", tz=TZ),
        support_available_at=pd.Timestamp(f"2026-01-{available_day:02d} 18:10", tz=TZ),
        support_origin_index=origin_day - 1,
        support_confirmed_index=confirmed_day - 1,
    )


def _behavior(items):
    lifecycle = build_support_lifecycle(items)
    return build_support_behavior(items, lifecycle)


def test_support_motion_is_separate_from_price_distance_and_flattens_after_lower_rebase() -> None:
    falling = (
        _obs(3, close=102.0, support=100.0),
        _obs(4, close=97.0, support=96.0, origin_day=2, confirmed_day=4, available_day=4),
    )
    fresh = _behavior(falling)
    assert fresh.motion is SupportMotion.FALLING
    assert fresh.last_rebase_step_atr == pytest.approx(-2.0)

    flattened = falling + tuple(
        _obs(day, close=97.0, support=96.0, origin_day=2, confirmed_day=4, available_day=4)
        for day in range(5, 9)
    )
    mature = _behavior(flattened)
    assert mature.motion is SupportMotion.FLAT_AFTER_FALL
    assert mature.bars_since_rebase == 4
    assert mature.relation in {PriceSupportRelation.AT_SUPPORT, PriceSupportRelation.ABOVE_NEAR}


def test_rising_support_with_expanding_distance_is_supported_advance() -> None:
    items = (
        _obs(3, close=103.0, low=102.0, support=100.0),
        _obs(4, close=105.0, low=104.0, support=102.0, origin_day=2, confirmed_day=4, available_day=4),
        _obs(5, close=108.0, low=107.0, support=102.0, origin_day=2, confirmed_day=4, available_day=4),
    )
    behavior = _behavior(items)
    assert behavior.motion is SupportMotion.RISING
    assert behavior.relation is PriceSupportRelation.ABOVE_FAR
    assert behavior.interaction is SupportInteractionState.SUPPORTED_ADVANCE


def test_price_approaching_flat_support_is_explicit_timing_context_not_breakdown() -> None:
    items = (
        _obs(3, close=104.0, low=103.0),
        _obs(4, close=101.0, low=100.8),
    )
    behavior = _behavior(items)
    assert behavior.motion is SupportMotion.FLAT
    assert behavior.relation is PriceSupportRelation.ABOVE_NEAR
    assert behavior.interaction is SupportInteractionState.APPROACHING_SUPPORT


def test_price_below_fresh_falling_support_with_persistence_is_downside_continuation() -> None:
    items = (
        _obs(3, close=102.0, support=100.0),
        _obs(4, close=94.0, support=98.0, origin_day=2, confirmed_day=4, available_day=4),
        _obs(5, close=94.0, support=98.0, origin_day=2, confirmed_day=4, available_day=4),
    )
    behavior = _behavior(items)
    assert behavior.motion is SupportMotion.FALLING
    assert behavior.relation is PriceSupportRelation.BELOW_FAR
    assert behavior.interaction is SupportInteractionState.DOWNSIDE_CONTINUATION


def test_reclaim_above_still_falling_support_remains_unconfirmed_attempt() -> None:
    items = (
        _obs(3, close=102.0, support=100.0),
        _obs(4, close=95.0, support=98.0, origin_day=2, confirmed_day=4, available_day=4),
        _obs(5, close=99.0, support=98.0, origin_day=2, confirmed_day=4, available_day=4),
        _obs(6, close=100.0, support=98.0, origin_day=2, confirmed_day=4, available_day=4),
    )
    behavior = _behavior(items)
    assert behavior.motion is SupportMotion.FALLING
    assert behavior.reclaim_active
    assert behavior.interaction is SupportInteractionState.RECLAIM_ATTEMPT


def test_reclaim_after_support_flattens_and_holds_becomes_recovery_confirmed() -> None:
    items = (
        _obs(3, close=102.0, support=100.0),
        _obs(4, close=94.0, support=96.0, origin_day=2, confirmed_day=4, available_day=4),
        _obs(5, close=94.5, support=96.0, origin_day=2, confirmed_day=4, available_day=4),
        _obs(6, close=95.0, support=96.0, origin_day=2, confirmed_day=4, available_day=4),
        _obs(7, close=95.0, support=96.0, origin_day=2, confirmed_day=4, available_day=4),
        _obs(8, close=97.0, support=96.0, origin_day=2, confirmed_day=4, available_day=4),
        _obs(9, close=98.0, support=96.0, origin_day=2, confirmed_day=4, available_day=4),
    )
    behavior = _behavior(items)
    assert behavior.motion is SupportMotion.FLAT_AFTER_FALL
    assert behavior.reclaim_active
    assert behavior.interaction is SupportInteractionState.RECOVERY_CONFIRMED


def test_failed_reclaim_is_distinguished_from_first_breakdown_attempt() -> None:
    items = (
        _obs(3, close=101.0),
        _obs(4, close=99.0),
        _obs(5, close=101.0),
        _obs(6, close=99.0),
    )
    behavior = _behavior(items)
    assert behavior.cross_count == 3
    assert behavior.interaction is SupportInteractionState.RECOVERY_FAILED


def test_old_flat_support_with_repeated_crosses_is_range_not_directional_recovery() -> None:
    closes = (101.0, 99.0, 101.0, 100.6, 99.5, 100.7, 100.4, 99.6, 100.5)
    items = tuple(_obs(day, close=close) for day, close in zip(range(3, 12), closes, strict=True))
    behavior = _behavior(items)
    assert behavior.motion is SupportMotion.FLAT
    assert behavior.bars_since_rebase == 8
    assert behavior.cross_count >= 2
    assert behavior.interaction is SupportInteractionState.RANGE_AROUND_SUPPORT


def test_replay_exposes_same_behavior_as_latest_historical_prefix(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    direct = StabilSupportReplayRunner(store).replay("THYAO")
    historical = StabilSupportHistoricalReplayRunner(store).replay(
        "THYAO",
        minimum_bars=20,
        step=2,
        max_points=30,
    )
    assert direct.behavior is not None
    assert historical.latest_behavior == direct.behavior


def test_new_behavior_layer_does_not_change_canonical_lifecycle_snapshot(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    direct = StabilSupportReplayRunner(store).replay("THYAO")
    canonical = StabilSupportLifecycleEngine().analyze(direct.input_batch.frame)
    assert direct.snapshot == canonical
