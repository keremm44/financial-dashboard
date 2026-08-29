from financial_dashboard.decision.event_stream import ExecutionEventQueue
from financial_dashboard.decision.execution import (
    ExecutionEventKind,
    ExecutionTriggerEvent,
    ExecutionTriggerState,
)
from financial_dashboard.decision.structural import StructuralDirection


def _event(observed_at, *, available_at=None, reason="PATTERN"):
    if available_at is None:
        available_at = observed_at
    return ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=StructuralDirection.LONG,
        timeframe="30m",
        observed_at=observed_at,
        available_at=available_at,
        reason=reason,
        kind=ExecutionEventKind.PATTERN_CONFIRMATION,
    )


def test_half_hour_event_is_offered_to_next_hourly_decision_once():
    queue = ExecutionEventQueue((_event(10.5),))
    assert queue.take_fresh(10.0, previous_as_of=None) is None
    event = queue.take_fresh(11.0, previous_as_of=10.0)
    assert event is not None
    assert event.observed_at == 10.5
    queue.record_consumed()
    assert queue.take_fresh(12.0, previous_as_of=11.0) is None
    assert queue.ledger.consumed == 1
    assert queue.ledger.pending == 0


def test_event_available_after_observation_waits_until_available():
    queue = ExecutionEventQueue((_event(10.25, available_at=10.75),))
    assert queue.take_fresh(10.5, previous_as_of=10.0) is None
    event = queue.take_fresh(11.0, previous_as_of=10.5)
    assert event is not None
    assert event.available_at == 10.75


def test_stale_event_is_not_carried_into_later_window():
    queue = ExecutionEventQueue((_event(9.5),))
    assert queue.take_fresh(11.0, previous_as_of=10.0) is None
    assert queue.ledger.expired == 1


def test_duplicate_events_collapse_deterministically():
    first = _event(10.5)
    queue = ExecutionEventQueue((first, first))
    assert queue.ledger.total == 1
    assert queue.take_fresh(11.0, previous_as_of=10.0) is not None


def test_freshest_event_wins_same_decision_window():
    queue = ExecutionEventQueue((_event(10.25, reason="A"), _event(10.5, reason="B")))
    event = queue.take_fresh(11.0, previous_as_of=10.0)
    assert event is not None
    assert event.reason == "B"
    assert queue.ledger.expired == 1
