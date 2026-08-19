from __future__ import annotations

import pytest

from financial_dashboard.data.quality import DataQualityStatus
from financial_dashboard.engines.models import Direction
from financial_dashboard.engines.mtf_story_context import MTFStoryContextError, classify_context
from financial_dashboard.engines.mtf_story_models import (
    ContextState,
    TimeframeRole,
    TimeframeStoryState,
)


def tf(
    timeframe: str,
    direction: Direction,
    *,
    state: str | None = None,
    quality: DataQualityStatus = DataQualityStatus.OK,
) -> TimeframeStoryState:
    roles = {
        "1d": TimeframeRole.MACRO_CONTEXT,
        "4h": TimeframeRole.STRUCTURAL_CONTEXT,
        "2h": TimeframeRole.PRIMARY_STRUCTURE,
    }
    if state is None:
        state = (
            "STATE_BULLISH"
            if direction is Direction.UP
            else "STATE_BEARISH"
            if direction is Direction.DOWN
            else "STATE_NEUTRAL"
        )
    return TimeframeStoryState(
        timeframe=timeframe,
        role=roles[timeframe],
        data_quality=quality,
        structural_direction=direction,
        structural_state=state,
    )


def test_all_bearish_is_bearish_context() -> None:
    result = classify_context([
        tf("1d", Direction.DOWN),
        tf("4h", Direction.DOWN),
        tf("2h", Direction.DOWN),
    ])
    assert result.state is ContextState.BEARISH_CONTEXT
    assert result.direction is Direction.DOWN
    assert result.anchor_timeframe == "4h"
    assert not result.conflicts


def test_all_bullish_is_bullish_context() -> None:
    result = classify_context([
        tf("1d", Direction.UP),
        tf("4h", Direction.UP),
        tf("2h", Direction.UP),
    ])
    assert result.state is ContextState.BULLISH_CONTEXT
    assert result.direction is Direction.UP


def test_2h_reaction_cannot_flip_established_bearish_context() -> None:
    result = classify_context([
        tf("1d", Direction.DOWN),
        tf("4h", Direction.DOWN),
        tf("2h", Direction.UP),
    ])
    assert result.state is ContextState.BEARISH_CONTEXT
    assert result.direction is Direction.DOWN
    assert any(c.code == "2H_REACTION_OPPOSES_4H" for c in result.conflicts)


def test_2h_reaction_cannot_flip_established_bullish_context() -> None:
    result = classify_context([
        tf("1d", Direction.UP),
        tf("4h", Direction.UP),
        tf("2h", Direction.DOWN),
    ])
    assert result.state is ContextState.BULLISH_CONTEXT
    assert result.direction is Direction.UP


def test_4h_transition_is_always_transition_context() -> None:
    result = classify_context([
        tf("1d", Direction.DOWN),
        tf("4h", Direction.DOWN, state="STATE_TRANSITION_UP"),
        tf("2h", Direction.UP),
    ])
    assert result.state is ContextState.TRANSITION_CONTEXT
    assert result.direction is Direction.UP
    assert result.anchor_timeframe == "4h"


def test_4h_and_2h_alignment_against_1d_is_transition_not_completed_flip() -> None:
    result = classify_context([
        tf("1d", Direction.DOWN),
        tf("4h", Direction.UP),
        tf("2h", Direction.UP),
    ])
    assert result.state is ContextState.TRANSITION_CONTEXT
    assert result.direction is Direction.UP
    assert any(c.code == "4H_2H_OPPOSE_MACRO" for c in result.conflicts)


def test_4h_isolated_against_aligned_1d_and_2h_is_mixed() -> None:
    result = classify_context([
        tf("1d", Direction.DOWN),
        tf("4h", Direction.UP),
        tf("2h", Direction.DOWN),
    ])
    assert result.state is ContextState.MIXED_CONTEXT
    assert result.direction is Direction.NEUTRAL
    assert any(c.code == "4H_ISOLATED_AGAINST_1D_2H" for c in result.conflicts)


def test_neutral_4h_anchor_stays_mixed_even_if_other_timeframes_align() -> None:
    result = classify_context([
        tf("1d", Direction.UP),
        tf("4h", Direction.NEUTRAL),
        tf("2h", Direction.UP),
    ])
    assert result.state is ContextState.MIXED_CONTEXT
    assert result.direction is Direction.NEUTRAL


def test_two_invalid_upper_context_timeframes_is_insufficient() -> None:
    result = classify_context([
        tf("1d", Direction.DOWN, quality=DataQualityStatus.INVALID),
        tf("4h", Direction.DOWN, quality=DataQualityStatus.INVALID),
        tf("2h", Direction.UP),
    ])
    assert result.state is ContextState.INSUFFICIENT_DATA
    assert result.direction is Direction.NEUTRAL
    assert result.usable_timeframes == ("2h",)


def test_data_limited_is_usable_but_explicitly_reported() -> None:
    result = classify_context([
        tf("1d", Direction.DOWN, quality=DataQualityStatus.LIMITED),
        tf("4h", Direction.DOWN),
        tf("2h", Direction.DOWN),
    ])
    assert result.state is ContextState.BEARISH_CONTEXT
    assert "1D:DATA_LIMITED" in result.reasons


def test_missing_2h_can_still_classify_when_1d_and_4h_align() -> None:
    result = classify_context([
        tf("1d", Direction.UP),
        tf("4h", Direction.UP),
    ])
    assert result.state is ContextState.BULLISH_CONTEXT
    assert result.direction is Direction.UP
    assert "2H:MISSING" in result.reasons


def test_missing_4h_requires_1d_2h_alignment() -> None:
    aligned = classify_context([
        tf("1d", Direction.DOWN),
        tf("2h", Direction.DOWN),
    ])
    assert aligned.state is ContextState.BEARISH_CONTEXT
    assert aligned.anchor_timeframe is None

    mixed = classify_context([
        tf("1d", Direction.DOWN),
        tf("2h", Direction.UP),
    ])
    assert mixed.state is ContextState.MIXED_CONTEXT
    assert mixed.direction is Direction.NEUTRAL


def test_missing_4h_with_transition_fallback_surfaces_transition() -> None:
    result = classify_context([
        tf("1d", Direction.DOWN),
        tf("2h", Direction.DOWN, state="STATE_TRANSITION_UP"),
    ])
    assert result.state is ContextState.TRANSITION_CONTEXT
    assert result.direction is Direction.UP


def test_2h_transition_against_bearish_4h_remains_bearish_context() -> None:
    result = classify_context([
        tf("1d", Direction.DOWN),
        tf("4h", Direction.DOWN),
        tf("2h", Direction.DOWN, state="STATE_TRANSITION_UP"),
    ])
    assert result.state is ContextState.BEARISH_CONTEXT
    assert result.direction is Direction.DOWN
    assert any("2H:TRANSITION_WITHIN_4H_CONTEXT:UP" == reason for reason in result.reasons)


def test_context_rejects_trigger_timeframe() -> None:
    bad = TimeframeStoryState(
        timeframe="1h",
        role=TimeframeRole.TACTICAL_STRUCTURE,
        data_quality=DataQualityStatus.OK,
        structural_direction=Direction.UP,
        structural_state="STATE_BULLISH",
    )
    with pytest.raises(MTFStoryContextError, match="unsupported context timeframe"):
        classify_context([tf("4h", Direction.UP), bad])


def test_context_rejects_duplicate_timeframes_case_insensitively() -> None:
    duplicate = TimeframeStoryState(
        timeframe="4H",
        role=TimeframeRole.STRUCTURAL_CONTEXT,
        data_quality=DataQualityStatus.OK,
        structural_direction=Direction.UP,
        structural_state="STATE_BULLISH",
    )
    with pytest.raises(MTFStoryContextError, match="duplicate context timeframe"):
        classify_context([tf("4h", Direction.UP), duplicate])
