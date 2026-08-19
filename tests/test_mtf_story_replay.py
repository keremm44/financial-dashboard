from dataclasses import replace

from financial_dashboard.engines.models import Direction
from financial_dashboard.engines.mtf_story_models import ContextState, MTFStoryResult, MTFStoryState, TriggerState
from financial_dashboard.engines.mtf_story_replay import replay_story_results
from financial_dashboard.engines.mtf_story_state_machine import MTFStoryStateMachine


def _story(i, state, direction=Direction.UP, *, confirmed=True):
    return MTFStoryResult(
        state=state,
        timestamp=f"2026-08-19T{10+i:02d}:00:00+03:00",
        dominant_direction=direction,
        macro_direction=Direction.DOWN,
        context_state=ContextState.BEARISH_CONTEXT,
        trigger_state=TriggerState.BULLISH_TRIGGER if direction is Direction.UP else TriggerState.BEARISH_TRIGGER,
        quality=80 + min(i, 10),
        confidence=0.8,
        is_confirmed=confirmed,
    )


def _sequence():
    return [
        _story(0, MTFStoryState.COUNTER_TREND_RALLY),
        _story(1, MTFStoryState.REVERSAL_BUILDING),
        _story(2, MTFStoryState.REVERSAL_BUILDING),
        _story(3, MTFStoryState.REVERSAL_CONFIRMED),
        _story(4, MTFStoryState.TREND_CONTINUATION, Direction.DOWN),
        _story(5, MTFStoryState.TREND_CONTINUATION, Direction.DOWN),
    ]


def test_replay_matches_incremental_state_machine_exactly():
    candidates = _sequence()
    replayed = replay_story_results(candidates)
    machine = MTFStoryStateMachine()
    incremental = [result for candidate in candidates if (result := machine.update(candidate)) is not None]
    assert replayed == incremental


def test_replay_is_prefix_safe_no_lookahead():
    candidates = _sequence()
    full = replay_story_results(candidates)
    for end in range(1, len(candidates) + 1):
        assert replay_story_results(candidates[:end]) == full[:end]


def test_future_change_cannot_mutate_earlier_replay_results():
    candidates = _sequence()
    prefix = replay_story_results(candidates[:3])
    frozen = tuple(prefix)
    altered_future = candidates[:3] + [
        _story(3, MTFStoryState.BREAKOUT_BUILDING, Direction.DOWN),
        _story(4, MTFStoryState.BREAKOUT_BUILDING, Direction.DOWN),
    ]
    replay_story_results(altered_future)
    assert tuple(prefix) == frozen


def test_unconfirmed_candidate_is_ignored_by_replay_state():
    first = _story(0, MTFStoryState.COUNTER_TREND_RALLY)
    preview = _story(1, MTFStoryState.REVERSAL_CONFIRMED, confirmed=False)
    after = _story(2, MTFStoryState.COUNTER_TREND_RALLY)
    output = replay_story_results([first, preview, after])
    assert output[0].state is MTFStoryState.COUNTER_TREND_RALLY
    assert output[1].state is MTFStoryState.COUNTER_TREND_RALLY
    assert output[2].state is MTFStoryState.COUNTER_TREND_RALLY


def test_replay_is_deterministic():
    candidates = _sequence()
    assert replay_story_results(candidates) == replay_story_results(candidates)


def test_confirmations_required_one_disables_hysteresis():
    candidates = [
        _story(0, MTFStoryState.COUNTER_TREND_RALLY),
        _story(1, MTFStoryState.REVERSAL_BUILDING),
    ]
    output = replay_story_results(candidates, confirmations_required=1)
    assert output[-1].state is MTFStoryState.REVERSAL_BUILDING
