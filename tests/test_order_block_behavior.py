from __future__ import annotations

from dataclasses import replace

from financial_dashboard.engines.order_block_behavior import (
    OrderBlockBehaviorConfig,
    OrderBlockBehaviorState,
    OrderBlockBehaviorTracker,
)
from financial_dashboard.engines.order_block_engine import OrderBlockConfig, OrderBlockRecord


CORE = OrderBlockConfig(fill_cancel_threshold=0.70, minimum_tick=0.01)


def _record(*, fill_boundary: float = 110.0, active: bool = True) -> OrderBlockRecord:
    return OrderBlockRecord(
        source_index=2,
        source_time="t2",
        top=110.0,
        bottom=100.0,
        bullish=True,
        base_score=2,
        has_imbalance=active,
        anchor_high=110.0,
        anchor_low=100.0,
        imbalance_end_index=6,
        fill_boundary=fill_boundary,
    )


def test_candidate_then_fresh_then_approaching_are_separate_states() -> None:
    tracker = OrderBlockBehaviorTracker(
        CORE,
        OrderBlockBehaviorConfig(atr_length=2, near_atr=1.0),
    )
    candidate = _record(active=False)
    first = tracker.update((candidate,), bar_index=3, high=125.0, low=120.0, close=123.0)
    assert first[0].state is OrderBlockBehaviorState.CANDIDATE

    active = replace(candidate, has_imbalance=True)
    fresh = tracker.update((active,), bar_index=4, high=122.0, low=118.0, close=120.0)
    assert fresh[0].state is OrderBlockBehaviorState.FRESH

    near = tracker.update((active,), bar_index=5, high=114.0, low=112.0, close=112.0)
    assert near[0].state is OrderBlockBehaviorState.APPROACHING
    assert near[0].bars_since_confirmation == 1


def test_first_partial_deep_and_repeated_mitigation_progression() -> None:
    tracker = OrderBlockBehaviorTracker(
        CORE,
        OrderBlockBehaviorConfig(atr_length=2, deep_fill_ratio=0.50),
    )
    active = _record()
    tracker.update((active,), bar_index=3, high=120.0, low=115.0, close=118.0)

    first_record = replace(active, fill_boundary=109.0)
    first = tracker.update((first_record,), bar_index=4, high=112.0, low=109.0, close=111.0)
    assert first[0].mitigation_count == 1
    assert first[0].state is OrderBlockBehaviorState.PARTIALLY_MITIGATED

    deep_record = replace(active, fill_boundary=104.0)
    deep = tracker.update((deep_record,), bar_index=5, high=115.0, low=111.0, close=114.0)
    assert deep[0].state is OrderBlockBehaviorState.DEEP_MITIGATION
    assert deep[0].deepest_fill_ratio >= 0.50

    repeated = tracker.update((deep_record,), bar_index=6, high=108.0, low=104.0, close=107.0)
    assert repeated[0].mitigation_count == 2
    assert repeated[0].state is OrderBlockBehaviorState.REPEATED_MITIGATION


def test_missing_confirmed_record_becomes_consumed_without_reviving_core_record() -> None:
    tracker = OrderBlockBehaviorTracker(
        CORE,
        OrderBlockBehaviorConfig(atr_length=2, terminal_retention_bars=3),
    )
    active = _record(fill_boundary=104.0)
    tracker.update((active,), bar_index=4, high=116.0, low=112.0, close=114.0)
    terminal = tracker.update((), bar_index=5, high=109.0, low=101.0, close=103.0)

    assert terminal[0].state is OrderBlockBehaviorState.CONSUMED
    assert terminal[0].active is False
    assert terminal[0].terminal_reason is not None

    tracker.update((), bar_index=9, high=110.0, low=108.0, close=109.0)
    assert tracker.snapshots == ()


def test_unconfirmed_candidate_expiry_is_not_called_consumed() -> None:
    tracker = OrderBlockBehaviorTracker(CORE, OrderBlockBehaviorConfig(atr_length=2))
    candidate = _record(active=False)
    tracker.update((candidate,), bar_index=5, high=120.0, low=115.0, close=118.0)
    expired = tracker.update((), bar_index=7, high=121.0, low=117.0, close=119.0)

    assert expired[0].state is OrderBlockBehaviorState.EXPIRED_CANDIDATE
    assert expired[0].terminal_reason == "IMBALANCE_NOT_CONFIRMED"


def test_tracker_does_not_change_canonical_record_values() -> None:
    tracker = OrderBlockBehaviorTracker(CORE, OrderBlockBehaviorConfig(atr_length=2))
    record = _record(fill_boundary=107.0)
    before = record
    tracker.update((record,), bar_index=4, high=112.0, low=107.0, close=111.0)
    assert record == before
