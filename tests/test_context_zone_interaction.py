from __future__ import annotations

from datetime import datetime, timezone

from financial_dashboard.context.zone_interaction import (
    ZoneInteractionState,
    classify_zone_interaction,
    interval_distance,
    transition_event,
)


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def test_interval_distance_is_zero_inside_zone() -> None:
    assert interval_distance(100.5, 100.0, 101.0) == 0.0
    assert interval_distance(99.0, 100.0, 101.0) == 1.0
    assert interval_distance(102.5, 100.0, 101.0) == 1.5


def test_current_zone_moves_from_approach_to_test_without_claiming_defence() -> None:
    approaching = classify_zone_interaction(
        side="SUPPORT",
        low=100.0,
        high=100.4,
        current_price=100.6,
        reference_atr=1.0,
        native_lifecycle="ACTIVE",
    )
    testing = classify_zone_interaction(
        side="SUPPORT",
        low=100.0,
        high=100.4,
        current_price=100.2,
        reference_atr=1.0,
        native_lifecycle="ACTIVE",
    )
    assert approaching is ZoneInteractionState.APPROACHING
    assert testing is ZoneInteractionState.TESTING


def test_repeated_test_can_be_marked_consuming_but_not_accepted_through() -> None:
    state = classify_zone_interaction(
        side="SUPPORT",
        low=100.0,
        high=100.4,
        current_price=100.1,
        reference_atr=1.0,
        native_lifecycle="ACTIVE",
        prior_state=ZoneInteractionState.TESTING,
    )
    assert state is ZoneInteractionState.BEING_CONSUMED


def test_native_break_failure_and_reclaim_remain_distinct_interactions() -> None:
    defended = classify_zone_interaction(
        side="RESISTANCE",
        low=102.0,
        high=102.4,
        current_price=102.1,
        reference_atr=1.0,
        native_lifecycle="BREAK_FAILED",
    )
    reclaimed = classify_zone_interaction(
        side="SUPPORT",
        low=100.0,
        high=100.4,
        current_price=100.6,
        reference_atr=1.0,
        native_lifecycle="ACTIVE",
        native_event="SUPPORT_RECLAIMED",
    )
    assert defended is ZoneInteractionState.DEFENDED
    assert reclaimed is ZoneInteractionState.RECLAIMED


def test_broken_support_below_price_relation_is_accepted_through() -> None:
    state = classify_zone_interaction(
        side="SUPPORT",
        low=100.0,
        high=100.4,
        current_price=99.0,
        reference_atr=1.0,
        native_lifecycle="BROKEN",
    )
    assert state is ZoneInteractionState.ACCEPTED_THROUGH


def test_transition_event_is_append_only_on_actual_change() -> None:
    assert transition_event(
        zone_id="z1",
        previous_state=ZoneInteractionState.TESTING,
        state=ZoneInteractionState.TESTING,
        observed_at=NOW,
        price=100.0,
        reason="same",
    ) is None

    event = transition_event(
        zone_id="z1",
        previous_state=ZoneInteractionState.APPROACHING,
        state=ZoneInteractionState.TESTING,
        observed_at=NOW,
        price=100.0,
        reason="entered geometry",
    )
    assert event is not None
    assert event.previous_state is ZoneInteractionState.APPROACHING
    assert event.state is ZoneInteractionState.TESTING
