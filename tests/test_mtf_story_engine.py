from financial_dashboard.data.quality import DataQualityStatus
from financial_dashboard.engines.models import Direction
from financial_dashboard.engines.mtf_story_engine import classify_story
from financial_dashboard.engines.mtf_story_models import (
    ContextAssessment,
    ContextState,
    MTFStoryState,
    TimeframeRole,
    TimeframeStoryState,
    TriggerAssessment,
    TriggerState,
)


def _tf(tf, role, direction=Direction.NEUTRAL, structural_state=None, pattern_state=None, pattern_quality=80, structural_quality=85):
    return TimeframeStoryState(
        timeframe=tf,
        role=role,
        data_quality=DataQualityStatus.OK,
        timestamp=f"2026-08-19T17:00:00+03:00",
        structural_direction=direction,
        structural_state=structural_state,
        structural_quality=structural_quality,
        pattern_state=pattern_state,
        pattern_quality=pattern_quality if pattern_state is not None else None,
    )


def _ctx(state, direction):
    return ContextAssessment(state=state, direction=direction, anchor_timeframe="4h", usable_timeframes=("1d", "4h", "2h"))


def _trg(state, direction):
    return TriggerAssessment(state=state, direction=direction, anchor_timeframe="1h", usable_timeframes=("1h", "30m", "15m"))


def _states():
    return (
        _tf("1d", TimeframeRole.MACRO_CONTEXT, Direction.DOWN),
        _tf("4h", TimeframeRole.STRUCTURAL_CONTEXT, Direction.DOWN),
        _tf("2h", TimeframeRole.PRIMARY_STRUCTURE, Direction.DOWN),
        _tf("1h", TimeframeRole.TACTICAL_STRUCTURE, Direction.UP),
        _tf("30m", TimeframeRole.TRIGGER_CONTEXT, Direction.UP),
        _tf("15m", TimeframeRole.REFINEMENT, Direction.UP),
    )


def test_aligned_directional_context_and_trigger_is_trend_continuation():
    result = classify_story(_ctx(ContextState.BEARISH_CONTEXT, Direction.DOWN), _trg(TriggerState.BEARISH_TRIGGER, Direction.DOWN), _states())
    assert result.state is MTFStoryState.TREND_CONTINUATION
    assert result.dominant_direction is Direction.DOWN


def test_bullish_trigger_inside_bearish_context_is_counter_trend_rally():
    result = classify_story(_ctx(ContextState.BEARISH_CONTEXT, Direction.DOWN), _trg(TriggerState.BULLISH_TRIGGER, Direction.UP), _states())
    assert result.state is MTFStoryState.COUNTER_TREND_RALLY
    assert result.macro_direction is Direction.DOWN
    assert result.dominant_direction is Direction.UP


def test_bearish_trigger_inside_bullish_context_is_counter_trend_drop():
    result = classify_story(_ctx(ContextState.BULLISH_CONTEXT, Direction.UP), _trg(TriggerState.BEARISH_TRIGGER, Direction.DOWN), _states())
    assert result.state is MTFStoryState.COUNTER_TREND_DROP


def test_opposing_reversal_trigger_is_reversal_building_not_confirmed():
    result = classify_story(_ctx(ContextState.BEARISH_CONTEXT, Direction.DOWN), _trg(TriggerState.REVERSAL_TRIGGER, Direction.UP), _states())
    assert result.state is MTFStoryState.REVERSAL_BUILDING


def test_transition_context_plus_aligned_reversal_trigger_confirms_reversal():
    result = classify_story(_ctx(ContextState.TRANSITION_CONTEXT, Direction.UP), _trg(TriggerState.REVERSAL_TRIGGER, Direction.UP), _states())
    assert result.state is MTFStoryState.REVERSAL_CONFIRMED


def test_aligned_breakout_is_breakout_confirmed():
    result = classify_story(_ctx(ContextState.BULLISH_CONTEXT, Direction.UP), _trg(TriggerState.BREAKOUT_TRIGGER, Direction.UP), _states())
    assert result.state is MTFStoryState.BREAKOUT_CONFIRMED


def test_counter_context_breakout_is_breakout_building():
    result = classify_story(_ctx(ContextState.BEARISH_CONTEXT, Direction.DOWN), _trg(TriggerState.BREAKOUT_TRIGGER, Direction.UP), _states())
    assert result.state is MTFStoryState.BREAKOUT_BUILDING


def test_no_trigger_with_compression_is_compression():
    states = list(_states())
    states[4] = _tf("30m", TimeframeRole.TRIGGER_CONTEXT, Direction.NEUTRAL, pattern_state="SIKISMA_GUCLENIYOR")
    result = classify_story(_ctx(ContextState.BEARISH_CONTEXT, Direction.DOWN), _trg(TriggerState.NO_TRIGGER, Direction.NEUTRAL), states)
    assert result.state is MTFStoryState.COMPRESSION


def test_no_trigger_without_compression_is_range_mixed():
    result = classify_story(_ctx(ContextState.BEARISH_CONTEXT, Direction.DOWN), _trg(TriggerState.NO_TRIGGER, Direction.NEUTRAL), _states())
    assert result.state is MTFStoryState.RANGE_MIXED


def test_insufficient_context_or_trigger_propagates_to_story():
    result = classify_story(_ctx(ContextState.INSUFFICIENT_DATA, Direction.NEUTRAL), _trg(TriggerState.BULLISH_TRIGGER, Direction.UP), _states())
    assert result.state is MTFStoryState.INSUFFICIENT_DATA
    assert result.dominant_direction is Direction.NEUTRAL


def test_quality_and_confidence_are_bounded():
    result = classify_story(_ctx(ContextState.BEARISH_CONTEXT, Direction.DOWN), _trg(TriggerState.BULLISH_TRIGGER, Direction.UP), _states())
    assert 0 <= result.quality <= 100
    assert 0 <= result.confidence <= 1
