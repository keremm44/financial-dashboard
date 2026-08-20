from dataclasses import replace

from financial_dashboard.engines.models import Direction
from financial_dashboard.engines.mtf_story_models import ContextState, MTFStoryResult, MTFStoryState, TriggerState
from financial_dashboard.engines.mtf_story_state_machine import MTFStoryStateMachine


def _story(state, direction=Direction.UP, *, confirmed=True, ts="2026-08-19T10:00:00+03:00"):
    return MTFStoryResult(
        state=state,
        timestamp=ts,
        dominant_direction=direction,
        macro_direction=Direction.DOWN,
        context_state=ContextState.BEARISH_CONTEXT,
        trigger_state=TriggerState.BULLISH_TRIGGER if direction is Direction.UP else TriggerState.BEARISH_TRIGGER,
        quality=80,
        confidence=0.8,
        is_confirmed=confirmed,
    )


def test_first_confirmed_story_is_accepted_immediately():
    machine = MTFStoryStateMachine()
    first = _story(MTFStoryState.COUNTER_TREND_RALLY)
    assert machine.update(first) == first
    assert machine.snapshot == first


def test_single_state_change_is_held_until_second_matching_confirmation():
    machine = MTFStoryStateMachine()
    first = _story(MTFStoryState.COUNTER_TREND_RALLY)
    changed = _story(MTFStoryState.REVERSAL_BUILDING, ts="2026-08-19T11:00:00+03:00")
    machine.update(first)
    held = machine.update(changed)
    assert held.state is MTFStoryState.COUNTER_TREND_RALLY
    assert held.reasons[-1] == "PERSISTENCE:HOLD:REVERSAL_BUILDING"
    accepted = machine.update(replace(changed, timestamp="2026-08-19T12:00:00+03:00"))
    assert accepted.state is MTFStoryState.REVERSAL_BUILDING


def test_building_to_confirmed_escalation_is_immediate():
    machine = MTFStoryStateMachine()
    machine.update(_story(MTFStoryState.REVERSAL_BUILDING))
    confirmed = _story(MTFStoryState.REVERSAL_CONFIRMED, ts="2026-08-19T11:00:00+03:00")
    assert machine.update(confirmed) == confirmed


def test_unconfirmed_preview_never_mutates_stable_story():
    machine = MTFStoryStateMachine()
    stable = _story(MTFStoryState.TREND_CONTINUATION, Direction.DOWN)
    machine.update(stable)
    preview = _story(MTFStoryState.REVERSAL_CONFIRMED, Direction.UP, confirmed=False)
    assert machine.update(preview) == stable
    assert machine.snapshot == stable


def test_same_semantic_state_refreshes_immediately():
    machine = MTFStoryStateMachine()
    first = _story(MTFStoryState.COUNTER_TREND_RALLY)
    machine.update(first)
    refreshed = replace(first, timestamp="2026-08-19T11:00:00+03:00", quality=88, confidence=0.88)
    assert machine.update(refreshed) == refreshed


def test_reset_removes_stable_and_pending_state():
    machine = MTFStoryStateMachine()
    machine.update(_story(MTFStoryState.COUNTER_TREND_RALLY))
    machine.update(_story(MTFStoryState.REVERSAL_BUILDING))
    machine.reset()
    assert machine.snapshot is None
    new = _story(MTFStoryState.TREND_CONTINUATION, Direction.DOWN)
    assert machine.update(new) == new
