from __future__ import annotations

import pandas as pd
import pytest

from financial_dashboard.engines.stabil_support_lifecycle import (
    DailySupportObservation,
    StabilSupportLifecycleEngine,
    SupportDynamics,
    SupportLifecycleEventType,
    SupportProgression,
    SupportValidity,
    build_daily_support_observations,
    build_support_lifecycle,
)
from financial_dashboard.engines.stabil_trend_engine import StabilTrendConfig


TZ = "Europe/Istanbul"


def _obs(
    day: int,
    *,
    close: float,
    high: float | None = None,
    low: float | None = None,
    support: float | None = 100.0,
    floor: float | None = 98.0,
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
        support_level=support,
        support_floor=floor,
        support_origin_at=(
            pd.Timestamp(f"2026-01-{origin_day:02d} 18:10", tz=TZ)
            if support is not None
            else None
        ),
        support_confirmed_at=(
            pd.Timestamp(f"2026-01-{confirmed_day:02d} 18:10", tz=TZ)
            if support is not None
            else None
        ),
        support_available_at=(
            pd.Timestamp(f"2026-01-{available_day:02d} 18:10", tz=TZ)
            if support is not None
            else None
        ),
        support_origin_index=origin_day - 1 if support is not None else None,
        support_confirmed_index=confirmed_day - 1 if support is not None else None,
    )


def _event_types(snapshot) -> list[SupportLifecycleEventType]:
    return [event.event_type for event in snapshot.events]


def test_empty_lifecycle_is_explicit_no_support() -> None:
    snapshot = build_support_lifecycle(())
    assert snapshot.validity is SupportValidity.NO_SUPPORT
    assert snapshot.dynamics is SupportDynamics.UNAVAILABLE
    assert snapshot.events == ()


def test_breach_bars_below_and_reclaim_are_factual_without_fixed_window() -> None:
    snapshot = build_support_lifecycle(
        (
            _obs(3, close=102.0, low=101.0),
            _obs(4, close=99.0, high=100.5, low=98.5),
            _obs(5, close=97.5, high=99.0, low=97.0),
            _obs(6, close=99.5, high=100.0, low=99.0),
            _obs(7, close=101.0, low=99.8),
        )
    )

    assert snapshot.validity is SupportValidity.ACTIVE
    assert snapshot.bars_below_support == 0
    assert snapshot.bars_above_support == 1
    assert snapshot.reclaim_count == 1
    assert SupportLifecycleEventType.SUPPORT_BREACHED in _event_types(snapshot)
    assert SupportLifecycleEventType.SUPPORT_FLOOR_BROKEN in _event_types(snapshot)
    assert SupportLifecycleEventType.SUPPORT_RECLAIMED in _event_types(snapshot)

    reclaim = next(
        event
        for event in snapshot.events
        if event.event_type is SupportLifecycleEventType.SUPPORT_RECLAIMED
    )
    assert reclaim.bars_below_support == 0
    assert reclaim.reclaim_count == 1


def test_lower_rebase_after_breach_marks_old_support_lost_causally() -> None:
    old = [
        _obs(3, close=102.0, support=100.0, floor=98.0),
        _obs(4, close=97.0, support=100.0, floor=98.0),
    ]
    new = _obs(
        5,
        close=96.0,
        support=95.0,
        floor=93.0,
        origin_day=2,
        confirmed_day=5,
        available_day=5,
    )
    snapshot = build_support_lifecycle((*old, new))

    types = _event_types(snapshot)
    assert SupportLifecycleEventType.SUPPORT_LOST in types
    assert SupportLifecycleEventType.SUPPORT_REBASED_LOWER in types
    assert snapshot.progression is SupportProgression.REBASED_LOWER
    assert snapshot.support_level == pytest.approx(95.0)

    lost = next(
        event
        for event in snapshot.events
        if event.event_type is SupportLifecycleEventType.SUPPORT_LOST
    )
    assert lost.event_time == new.timestamp
    assert lost.support_level == pytest.approx(100.0)
    assert lost.previous_support == pytest.approx(100.0)
    assert lost.new_support == pytest.approx(95.0)


def test_higher_rebase_is_progression_not_trend_reversal() -> None:
    snapshot = build_support_lifecycle(
        (
            _obs(3, close=104.0, support=100.0, floor=98.0),
            _obs(
                4,
                close=105.0,
                support=102.0,
                floor=100.0,
                origin_day=2,
                confirmed_day=4,
                available_day=4,
            ),
        )
    )
    assert snapshot.progression is SupportProgression.REBASED_HIGHER
    assert SupportLifecycleEventType.SUPPORT_REBASED_HIGHER in _event_types(snapshot)


def test_price_dynamics_use_distance_change_not_fixed_percent_thresholds() -> None:
    expanding = build_support_lifecycle(
        (
            _obs(3, close=102.0, support=100.0, atr=2.0),
            _obs(4, close=104.0, support=100.0, atr=2.0),
        )
    )
    contracting = build_support_lifecycle(
        (
            _obs(3, close=105.0, support=100.0, atr=2.0),
            _obs(4, close=103.0, support=100.0, atr=2.0),
        )
    )
    flat = build_support_lifecycle(
        (
            _obs(3, close=103.0, support=100.0, atr=2.0),
            _obs(4, close=103.0, support=100.0, atr=2.0),
        )
    )

    assert expanding.dynamics is SupportDynamics.EXPANDING
    assert expanding.distance_pct == pytest.approx(4.0)
    assert expanding.distance_atr == pytest.approx(2.0)
    assert expanding.distance_delta_atr == pytest.approx(1.0)

    assert contracting.dynamics is SupportDynamics.CONTRACTING
    assert contracting.distance_delta_atr == pytest.approx(-1.0)
    assert flat.dynamics is SupportDynamics.FLAT


def test_exact_support_touch_is_separate_from_close_below() -> None:
    testing = build_support_lifecycle(
        (
            _obs(3, close=102.0, support=100.0, low=101.0),
            _obs(4, close=101.0, support=100.0, low=99.8, high=102.0),
        )
    )
    assert testing.validity is SupportValidity.ACTIVE
    assert testing.dynamics is SupportDynamics.AT_SUPPORT
    assert testing.intrabar_below_support
    assert not testing.close_below_support
    assert SupportLifecycleEventType.SUPPORT_TESTED in _event_types(testing)


def test_future_tail_does_not_change_prefix_lifecycle() -> None:
    prefix = (
        _obs(3, close=102.0),
        _obs(4, close=104.0),
        _obs(5, close=103.0),
    )
    tail = (
        _obs(6, close=95.0),
        _obs(7, close=90.0),
    )
    prefix_snapshot = build_support_lifecycle(prefix)
    replayed_prefix = build_support_lifecycle((*prefix, *tail)[: len(prefix)])
    assert replayed_prefix == prefix_snapshot


def _daily_frame_with_confirmable_pivots() -> pd.DataFrame:
    rows = []
    closes = [
        100, 102, 99, 103, 98, 104, 97, 105, 96, 106,
        95, 107, 94, 108, 93, 109, 92, 110, 91, 111,
        90, 112, 89, 113, 88, 114, 87, 115, 86, 116,
    ]
    for i, close in enumerate(closes):
        ts = pd.Timestamp("2026-01-01 18:10", tz=TZ) + pd.Timedelta(days=i)
        rows.append(
            {
                "timestamp": ts,
                "open": float(close - 0.2),
                "high": float(close + 0.8),
                "low": float(close - 0.8),
                "close": float(close),
                "volume": 1000.0,
                "is_closed": True,
                "is_complete": True,
            }
        )
    return pd.DataFrame(rows)


def test_support_observation_preserves_origin_confirmation_and_availability() -> None:
    frame = _daily_frame_with_confirmable_pivots()
    cfg = StabilTrendConfig(
        daily_pivot_len=1,
        daily_ema_len=2,
        slope_lookback=1,
        acceptance_len=2,
        pullback_lookback=2,
    )
    observations = build_daily_support_observations(frame, config=cfg)
    known = [item for item in observations if item.support_level is not None]
    assert known

    first = known[0]
    assert first.support_origin_at < first.support_confirmed_at <= first.support_available_at
    assert first.timestamp == first.support_available_at


def test_open_and_incomplete_tail_cannot_change_support_lifecycle() -> None:
    frame = _daily_frame_with_confirmable_pivots()
    cfg = StabilTrendConfig(
        daily_pivot_len=1,
        daily_ema_len=2,
        slope_lookback=1,
        acceptance_len=2,
        pullback_lookback=2,
    )
    base = StabilSupportLifecycleEngine(cfg).analyze(frame)

    bad = frame.iloc[-1].copy()
    bad["timestamp"] = frame.iloc[-1].timestamp + pd.Timedelta(days=1)
    bad["high"] = 10_000.0
    bad["low"] = 0.01
    bad["close"] = 0.02
    bad["is_closed"] = False
    bad["is_complete"] = False
    with_open = StabilSupportLifecycleEngine(cfg).analyze(
        pd.concat([frame, pd.DataFrame([bad])], ignore_index=True)
    )
    assert with_open == base
