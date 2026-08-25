from __future__ import annotations

from financial_dashboard.engines.order_block_behavior import (
    OrderBlockBehaviorConfig,
    OrderBlockBehaviorState,
    OrderBlockBehaviorTracker,
    OrderBlockInteractionState,
)
from financial_dashboard.engines.order_block_engine import OrderBlockConfig, OrderBlockRecord


CORE = OrderBlockConfig(fill_cancel_threshold=0.70, minimum_tick=0.01)
CONFIG = OrderBlockBehaviorConfig(
    atr_length=2,
    near_atr=1.0,
    deep_fill_ratio=0.50,
    reaction_move_atr=0.50,
    dwell_bars=2,
    favorable_hold_bars=2,
)


def _bullish() -> OrderBlockRecord:
    return OrderBlockRecord(
        source_index=2,
        source_time="t2",
        top=110.0,
        bottom=100.0,
        bullish=True,
        base_score=2,
        has_imbalance=True,
        anchor_high=110.0,
        anchor_low=100.0,
        imbalance_end_index=6,
        fill_boundary=108.0,
    )


def _bearish() -> OrderBlockRecord:
    return OrderBlockRecord(
        source_index=2,
        source_time="t2",
        top=110.0,
        bottom=100.0,
        bullish=False,
        base_score=2,
        has_imbalance=True,
        anchor_high=110.0,
        anchor_low=100.0,
        imbalance_end_index=6,
        fill_boundary=102.0,
    )


def _one(rows):
    assert len(rows) == 1
    return rows[0]


def test_long_dwell_is_one_visit_then_favorable_acceptance_confirms_reaction() -> None:
    tracker = OrderBlockBehaviorTracker(CORE, CONFIG)
    record = _bullish()

    tracker.update((record,), bar_index=3, high=125.0, low=120.0, close=123.0)

    entered = _one(
        tracker.update((record,), bar_index=4, high=109.0, low=105.0, close=107.0)
    )
    assert entered.interaction is OrderBlockInteractionState.ENTERED
    assert entered.visit_count == 1
    assert entered.mitigation_count == 1
    assert entered.current_visit_bars == 1
    assert entered.close_inside is True

    dwell_2 = _one(
        tracker.update((record,), bar_index=5, high=108.0, low=104.0, close=106.0)
    )
    dwell_3 = _one(
        tracker.update((record,), bar_index=6, high=109.0, low=103.0, close=108.0)
    )
    assert dwell_2.interaction is OrderBlockInteractionState.DWELLING_INSIDE
    assert dwell_2.state is OrderBlockBehaviorState.DWELLING_INSIDE
    assert dwell_3.interaction is OrderBlockInteractionState.DWELLING_INSIDE
    assert dwell_3.state is OrderBlockBehaviorState.DWELLING_INSIDE
    assert dwell_3.visit_count == 1
    assert dwell_3.mitigation_count == 1
    assert dwell_3.current_visit_bars == 3
    assert dwell_3.total_inside_bars == 3
    assert dwell_3.inside_close_bars == 3

    exiting = _one(
        tracker.update((record,), bar_index=7, high=114.0, low=109.0, close=112.0)
    )
    assert exiting.interaction is OrderBlockInteractionState.EXITING_FAVORABLE
    assert exiting.visit_count == 1
    assert exiting.favorable_exit_index == 7
    assert exiting.bars_held_favorable == 1

    holding = _one(
        tracker.update((record,), bar_index=8, high=113.0, low=111.5, close=112.5)
    )
    assert holding.interaction is OrderBlockInteractionState.HOLDING_FAVORABLE
    assert holding.state is OrderBlockBehaviorState.HOLDING_FAVORABLE
    assert holding.visit_count == 1
    assert holding.bars_held_favorable == 2

    confirmed = _one(
        tracker.update((record,), bar_index=9, high=118.0, low=113.0, close=116.0)
    )
    assert confirmed.interaction is OrderBlockInteractionState.REACTION_CONFIRMED
    assert confirmed.state is OrderBlockBehaviorState.REACTION_CONFIRMED
    assert confirmed.visit_count == 1
    assert confirmed.mitigation_count == 1
    assert confirmed.max_favorable_move_atr >= CONFIG.reaction_move_atr


def test_second_mitigation_requires_exit_separation_and_reentry() -> None:
    tracker = OrderBlockBehaviorTracker(CORE, CONFIG)
    record = _bullish()

    tracker.update((record,), bar_index=3, high=125.0, low=120.0, close=123.0)
    tracker.update((record,), bar_index=4, high=109.0, low=105.0, close=107.0)
    tracker.update((record,), bar_index=5, high=108.0, low=104.0, close=106.0)
    tracker.update((record,), bar_index=6, high=114.0, low=109.0, close=112.0)
    tracker.update((record,), bar_index=7, high=116.0, low=112.0, close=114.0)

    revisited = _one(
        tracker.update((record,), bar_index=8, high=112.0, low=108.0, close=109.0)
    )
    assert revisited.visit_count == 2
    assert revisited.mitigation_count == 2
    assert revisited.current_visit_bars == 1
    assert revisited.interaction is OrderBlockInteractionState.ENTERED
    assert revisited.state is OrderBlockBehaviorState.REPEATED_MITIGATION


def test_bearish_order_block_uses_symmetric_favorable_side() -> None:
    tracker = OrderBlockBehaviorTracker(CORE, CONFIG)
    record = _bearish()

    tracker.update((record,), bar_index=3, high=90.0, low=85.0, close=87.0)
    entered = _one(
        tracker.update((record,), bar_index=4, high=106.0, low=102.0, close=104.0)
    )
    dwell = _one(
        tracker.update((record,), bar_index=5, high=108.0, low=103.0, close=105.0)
    )
    exiting = _one(
        tracker.update((record,), bar_index=6, high=101.0, low=97.0, close=98.0)
    )
    tracker.update((record,), bar_index=7, high=98.5, low=97.0, close=97.5)
    confirmed = _one(
        tracker.update((record,), bar_index=8, high=96.0, low=92.0, close=93.0)
    )

    assert entered.interaction is OrderBlockInteractionState.ENTERED
    assert dwell.interaction is OrderBlockInteractionState.DWELLING_INSIDE
    assert dwell.state is OrderBlockBehaviorState.DWELLING_INSIDE
    assert exiting.interaction is OrderBlockInteractionState.EXITING_FAVORABLE
    assert confirmed.interaction is OrderBlockInteractionState.REACTION_CONFIRMED
    assert confirmed.state is OrderBlockBehaviorState.REACTION_CONFIRMED
    assert confirmed.visit_count == 1


def test_open_or_incomplete_bar_does_not_advance_interaction_state() -> None:
    tracker = OrderBlockBehaviorTracker(CORE, CONFIG)
    record = _bullish()

    tracker.update((record,), bar_index=3, high=125.0, low=120.0, close=123.0)
    before = tracker.update(
        (record,), bar_index=4, high=109.0, low=105.0, close=107.0
    )

    open_result = tracker.update(
        (record,),
        bar_index=5,
        high=130.0,
        low=90.0,
        close=95.0,
        is_closed=False,
    )
    incomplete_result = tracker.update(
        (record,),
        bar_index=5,
        high=130.0,
        low=90.0,
        close=95.0,
        is_complete=False,
    )

    assert open_result == before
    assert incomplete_result == before
    assert tracker.snapshots == before
