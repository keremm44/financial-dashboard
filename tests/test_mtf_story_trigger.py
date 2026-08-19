from __future__ import annotations

from financial_dashboard.data.quality import DataQualityStatus
from financial_dashboard.engines.models import Direction
from financial_dashboard.engines.mtf_story_models import TimeframeRole, TimeframeStoryState, TriggerState
from financial_dashboard.engines.mtf_story_trigger import MTFStoryTriggerError, classify_trigger


def _state(
    timeframe: str,
    direction: Direction,
    *,
    structural_state: str | None = None,
    breakout: Direction = Direction.NEUTRAL,
    pattern_state: str | None = None,
    quality: DataQualityStatus = DataQualityStatus.OK,
) -> TimeframeStoryState:
    role = {
        "1h": TimeframeRole.TACTICAL_STRUCTURE,
        "30m": TimeframeRole.TRIGGER_CONTEXT,
        "15m": TimeframeRole.REFINEMENT,
    }[timeframe]
    return TimeframeStoryState(
        timeframe=timeframe,
        role=role,
        data_quality=quality,
        structural_direction=direction,
        structural_state=structural_state,
        breakout_direction=breakout,
        pattern_state=pattern_state,
    )


def test_aligned_bullish_structure_yields_bullish_trigger() -> None:
    result = classify_trigger([
        _state("1h", Direction.UP),
        _state("30m", Direction.UP),
        _state("15m", Direction.UP),
    ])
    assert result.state is TriggerState.BULLISH_TRIGGER
    assert result.direction is Direction.UP


def test_aligned_bearish_structure_yields_bearish_trigger() -> None:
    result = classify_trigger([
        _state("1h", Direction.DOWN),
        _state("30m", Direction.DOWN),
        _state("15m", Direction.DOWN),
    ])
    assert result.state is TriggerState.BEARISH_TRIGGER
    assert result.direction is Direction.DOWN


def test_counter_structure_breakout_is_breakout_not_reversal() -> None:
    result = classify_trigger([
        _state("1h", Direction.DOWN),
        _state("30m", Direction.DOWN, breakout=Direction.UP, pattern_state="KIRILIM_TEYITLI"),
        _state("15m", Direction.UP),
    ])
    assert result.state is TriggerState.BREAKOUT_TRIGGER
    assert result.direction is Direction.UP
    assert any(conflict.code == "LOWER_TF_BREAKOUT_OPPOSES_1H_STRUCTURE" for conflict in result.conflicts)


def test_1h_transition_with_lower_confirmation_yields_reversal_trigger() -> None:
    result = classify_trigger([
        _state("1h", Direction.NEUTRAL, structural_state="STATE_TRANSITION_UP"),
        _state("30m", Direction.UP),
        _state("15m", Direction.UP, breakout=Direction.UP, pattern_state="KIRILIM_TEYITLI"),
    ])
    assert result.state is TriggerState.REVERSAL_TRIGGER
    assert result.direction is Direction.UP


def test_1h_transition_without_lower_confirmation_does_not_promote_reversal() -> None:
    result = classify_trigger([
        _state("1h", Direction.NEUTRAL, structural_state="STATE_TRANSITION_UP"),
        _state("30m", Direction.DOWN),
        _state("15m", Direction.DOWN),
    ])
    assert result.state is TriggerState.NO_TRIGGER
    assert result.direction is Direction.NEUTRAL


def test_unconfirmed_break_attempt_does_not_count_as_breakout_trigger() -> None:
    result = classify_trigger([
        _state("1h", Direction.DOWN),
        _state("30m", Direction.NEUTRAL, breakout=Direction.UP, pattern_state="KIRILIM_DENEMESI"),
        _state("15m", Direction.NEUTRAL),
    ])
    assert result.state is TriggerState.NO_TRIGGER


def test_without_1h_lower_timeframes_need_structural_alignment() -> None:
    result = classify_trigger([
        _state("30m", Direction.UP),
        _state("15m", Direction.UP),
    ])
    assert result.state is TriggerState.BULLISH_TRIGGER
    assert result.anchor_timeframe is None


def test_without_1h_aligned_confirmed_breakouts_can_yield_breakout_trigger() -> None:
    result = classify_trigger([
        _state("30m", Direction.NEUTRAL, breakout=Direction.UP, pattern_state="KIRILIM_TEYITLI"),
        _state("15m", Direction.NEUTRAL, breakout=Direction.UP, pattern_state="RETEST_BASARILI"),
    ])
    assert result.state is TriggerState.BREAKOUT_TRIGGER
    assert result.direction is Direction.UP


def test_invalid_timeframe_is_excluded_and_two_usable_are_enough() -> None:
    result = classify_trigger([
        _state("1h", Direction.UP),
        _state("30m", Direction.UP),
        _state("15m", Direction.DOWN, quality=DataQualityStatus.INVALID),
    ])
    assert result.state is TriggerState.BULLISH_TRIGGER
    assert "15M:DATA_INVALID" in result.reasons


def test_fewer_than_two_usable_timeframes_is_insufficient() -> None:
    result = classify_trigger([
        _state("1h", Direction.UP),
        _state("30m", Direction.UP, quality=DataQualityStatus.INVALID),
    ])
    assert result.state is TriggerState.INSUFFICIENT_DATA


def test_rejects_context_timeframe() -> None:
    bad = TimeframeStoryState(
        timeframe="2h",
        role=TimeframeRole.PRIMARY_STRUCTURE,
        data_quality=DataQualityStatus.OK,
    )
    try:
        classify_trigger([bad])
    except MTFStoryTriggerError as exc:
        assert "unsupported trigger timeframe" in str(exc)
    else:
        raise AssertionError("expected MTFStoryTriggerError")


def test_rejects_duplicate_trigger_timeframe() -> None:
    one = _state("1h", Direction.UP)
    try:
        classify_trigger([one, one])
    except MTFStoryTriggerError as exc:
        assert "duplicate trigger timeframe" in str(exc)
    else:
        raise AssertionError("expected MTFStoryTriggerError")
