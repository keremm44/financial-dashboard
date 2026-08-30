from __future__ import annotations

import pandas as pd

from financial_dashboard.engines.stabil_support_behavior import (
    StabilPrimaryState,
    SupportInteractionState,
    build_support_behavior,
)
from financial_dashboard.engines.stabil_support_lifecycle import (
    DailySupportObservation,
    build_support_lifecycle,
)


TZ = "Europe/Istanbul"


def _obs(
    day: int,
    *,
    close: float,
    support: float = 100.0,
    origin_day: int = 1,
    confirmed_day: int = 3,
    available_day: int = 3,
    atr: float = 2.0,
) -> DailySupportObservation:
    ts = pd.Timestamp(f"2026-01-{day:02d} 18:10", tz=TZ)
    return DailySupportObservation(
        timestamp=ts,
        high=float(close + 1.0),
        low=float(close - 1.0),
        close=float(close),
        atr=float(atr),
        support_level=float(support),
        support_floor=float(support - 2.0),
        support_origin_at=pd.Timestamp(f"2026-01-{origin_day:02d} 18:10", tz=TZ),
        support_confirmed_at=pd.Timestamp(f"2026-01-{confirmed_day:02d} 18:10", tz=TZ),
        support_available_at=pd.Timestamp(f"2026-01-{available_day:02d} 18:10", tz=TZ),
        support_origin_index=origin_day - 1,
        support_confirmed_index=confirmed_day - 1,
    )


def _behavior(items):
    lifecycle = build_support_lifecycle(items)
    return build_support_behavior(items, lifecycle)


def test_supported_advance_maps_to_bullish_progress_primary_state() -> None:
    behavior = _behavior(
        (
            _obs(3, close=103.0, support=100.0),
            _obs(4, close=105.0, support=102.0, origin_day=2, confirmed_day=4, available_day=4),
            _obs(5, close=108.0, support=102.0, origin_day=2, confirmed_day=4, available_day=4),
        )
    )
    assert behavior.interaction is SupportInteractionState.SUPPORTED_ADVANCE
    assert behavior.primary_state is StabilPrimaryState.BULLISH_PROGRESS


def test_failed_reclaim_exposes_explicit_rejection_without_replacing_legacy_interaction() -> None:
    behavior = _behavior(
        (
            _obs(3, close=101.0),
            _obs(4, close=99.0),
            _obs(5, close=101.0),
            _obs(6, close=99.0),
        )
    )
    assert behavior.interaction is SupportInteractionState.RECOVERY_FAILED
    assert behavior.reclaim_rejected is True
    assert behavior.reclaim_rejection_count == 1
    assert behavior.primary_state is StabilPrimaryState.BREAKDOWN_DEVELOPING


def test_recovery_confirmed_maps_to_recovery_primary_state() -> None:
    behavior = _behavior(
        (
            _obs(3, close=102.0, support=100.0),
            _obs(4, close=94.0, support=96.0, origin_day=2, confirmed_day=4, available_day=4),
            _obs(5, close=94.5, support=96.0, origin_day=2, confirmed_day=4, available_day=4),
            _obs(6, close=95.0, support=96.0, origin_day=2, confirmed_day=4, available_day=4),
            _obs(7, close=95.0, support=96.0, origin_day=2, confirmed_day=4, available_day=4),
            _obs(8, close=97.0, support=96.0, origin_day=2, confirmed_day=4, available_day=4),
            _obs(9, close=98.0, support=96.0, origin_day=2, confirmed_day=4, available_day=4),
        )
    )
    assert behavior.interaction is SupportInteractionState.RECOVERY_CONFIRMED
    assert behavior.primary_state is StabilPrimaryState.RECOVERY_CONFIRMED
    assert behavior.reclaim_rejected is False


def test_downside_continuation_maps_to_bearish_continuation_primary_state() -> None:
    behavior = _behavior(
        (
            _obs(3, close=102.0, support=100.0),
            _obs(4, close=94.0, support=98.0, origin_day=2, confirmed_day=4, available_day=4),
            _obs(5, close=94.0, support=98.0, origin_day=2, confirmed_day=4, available_day=4),
        )
    )
    assert behavior.interaction is SupportInteractionState.DOWNSIDE_CONTINUATION
    assert behavior.primary_state is StabilPrimaryState.BEARISH_CONTINUATION
