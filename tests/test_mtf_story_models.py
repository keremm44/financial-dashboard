from __future__ import annotations

import pytest

from financial_dashboard.data.quality import DataQualityStatus
from financial_dashboard.engines.models import Direction, EngineResult
from financial_dashboard.engines.mtf_story_models import (
    ConflictSeverity,
    ContextState,
    MTFStoryResult,
    MTFStoryState,
    RawTimeframeEvidence,
    StoryConflict,
    TimeframeRole,
    TimeframeStoryState,
    TriggerState,
    role_for_timeframe,
)


@pytest.mark.parametrize(
    ("timeframe", "role"),
    [
        ("1D", TimeframeRole.MACRO_CONTEXT),
        ("4h", TimeframeRole.STRUCTURAL_CONTEXT),
        ("2H", TimeframeRole.PRIMARY_STRUCTURE),
        ("1h", TimeframeRole.TACTICAL_STRUCTURE),
        ("30m", TimeframeRole.TRIGGER_CONTEXT),
        ("15M", TimeframeRole.REFINEMENT),
    ],
)
def test_role_for_timeframe_is_explicit_and_case_insensitive(timeframe, role) -> None:
    assert role_for_timeframe(timeframe) is role


def test_role_for_timeframe_rejects_unsupported_timeframe() -> None:
    with pytest.raises(ValueError, match="unsupported MTF Story timeframe"):
        role_for_timeframe("5m")


def test_raw_evidence_preserves_engine_contracts_without_interpreting_them() -> None:
    ms = EngineResult(
        engine="market_structure",
        state="STATE_BEARISH",
        timestamp="2026-08-19T17:00:00+03:00",
        direction=Direction.DOWN,
        score=87,
        quality=96,
    )
    pattern = EngineResult(
        engine="pattern_compression",
        state="KIRILIM_TEYITLI",
        timestamp="2026-08-19T17:00:00+03:00",
        direction=Direction.UP,
        score=72,
        quality=72,
    )
    ms_export = object()
    pattern_export = object()

    evidence = RawTimeframeEvidence(
        timeframe="1h",
        role=TimeframeRole.TACTICAL_STRUCTURE,
        data_quality=DataQualityStatus.OK,
        market_structure=ms,
        market_structure_export=ms_export,
        pattern_compression=pattern,
        pattern_export=pattern_export,
    )

    assert evidence.market_structure is ms
    assert evidence.pattern_compression is pattern
    assert evidence.market_structure_export is ms_export
    assert evidence.pattern_export is pattern_export
    assert evidence.market_structure.direction is Direction.DOWN
    assert evidence.pattern_compression.direction is Direction.UP


def test_raw_evidence_rejects_wrong_role_for_timeframe() -> None:
    with pytest.raises(ValueError, match="requires role TACTICAL_STRUCTURE"):
        RawTimeframeEvidence(
            timeframe="1h",
            role=TimeframeRole.MACRO_CONTEXT,
            data_quality=DataQualityStatus.OK,
        )


def test_normalized_state_keeps_structure_pattern_classic_and_breakout_directions_separate() -> None:
    state = TimeframeStoryState(
        timeframe="1h",
        role=TimeframeRole.TACTICAL_STRUCTURE,
        data_quality=DataQualityStatus.OK,
        structural_direction=Direction.DOWN,
        structural_state="STATE_BEARISH",
        structural_score=87,
        structural_quality=96,
        pattern_direction=Direction.UP,
        pattern_classic_direction=Direction.NEUTRAL,
        pattern_state="KIRILIM_TEYITLI",
        pattern_type="Simetrik Üçgen",
        pattern_quality=72.66,
        breakout_direction=Direction.UP,
    )

    assert state.structural_direction is Direction.DOWN
    assert state.pattern_direction is Direction.UP
    assert state.pattern_classic_direction is Direction.NEUTRAL
    assert state.breakout_direction is Direction.UP


def test_limited_timeframe_is_usable_but_invalid_is_not() -> None:
    limited = TimeframeStoryState(
        timeframe="30m",
        role=TimeframeRole.TRIGGER_CONTEXT,
        data_quality=DataQualityStatus.LIMITED,
    )
    invalid = TimeframeStoryState(
        timeframe="30m",
        role=TimeframeRole.TRIGGER_CONTEXT,
        data_quality=DataQualityStatus.INVALID,
    )

    assert limited.usable is True
    assert invalid.usable is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("structural_score", -0.01),
        ("structural_quality", 100.01),
        ("pattern_quality", 101),
    ],
)
def test_timeframe_state_rejects_out_of_range_scores(field, value) -> None:
    kwargs = {
        "timeframe": "2h",
        "role": TimeframeRole.PRIMARY_STRUCTURE,
        "data_quality": DataQualityStatus.OK,
        field: value,
    }
    with pytest.raises(ValueError, match="must be within 0..100"):
        TimeframeStoryState(**kwargs)


def test_story_result_carries_explainability_and_conflicts() -> None:
    tf_state = TimeframeStoryState(
        timeframe="4h",
        role=TimeframeRole.STRUCTURAL_CONTEXT,
        data_quality=DataQualityStatus.OK,
        structural_direction=Direction.DOWN,
    )
    conflict = StoryConflict(
        code="LOWER_TF_OPPOSES_CONTEXT",
        message="Bullish lower-TF move opposes 4H structure",
        severity=ConflictSeverity.WARNING,
        timeframes=("4h", "1h"),
    )

    result = MTFStoryResult(
        state=MTFStoryState.COUNTER_TREND_RALLY,
        timestamp="2026-08-19T17:00:00+03:00",
        dominant_direction=Direction.UP,
        macro_direction=Direction.DOWN,
        context_state=ContextState.BEARISH_CONTEXT,
        trigger_state=TriggerState.BULLISH_TRIGGER,
        quality=78,
        confidence=0.78,
        timeframe_states=(tf_state,),
        reasons=("4H structure remains bearish", "1H structure turned bullish"),
        conflicts=(conflict,),
    )

    assert result.state is MTFStoryState.COUNTER_TREND_RALLY
    assert result.macro_direction is Direction.DOWN
    assert result.dominant_direction is Direction.UP
    assert result.conflicts == (conflict,)
    assert result.is_confirmed is True


@pytest.mark.parametrize(
    ("quality", "confidence", "message"),
    [(-1, 0.5, "quality must be within 0..100"), (101, 0.5, "quality must be within 0..100"), (50, -0.1, "confidence must be within 0..1"), (50, 1.1, "confidence must be within 0..1")],
)
def test_story_result_rejects_invalid_quality_or_confidence(quality, confidence, message) -> None:
    with pytest.raises(ValueError, match=message):
        MTFStoryResult(
            state=MTFStoryState.RANGE_MIXED,
            timestamp=None,
            dominant_direction=Direction.NEUTRAL,
            macro_direction=Direction.NEUTRAL,
            context_state=ContextState.MIXED_CONTEXT,
            trigger_state=TriggerState.NO_TRIGGER,
            quality=quality,
            confidence=confidence,
        )


def test_story_result_rejects_duplicate_timeframe_states() -> None:
    first = TimeframeStoryState(
        timeframe="1h",
        role=TimeframeRole.TACTICAL_STRUCTURE,
        data_quality=DataQualityStatus.OK,
    )
    second = TimeframeStoryState(
        timeframe="1H",
        role=TimeframeRole.TACTICAL_STRUCTURE,
        data_quality=DataQualityStatus.LIMITED,
    )

    with pytest.raises(ValueError, match="duplicate timeframe state: 1H"):
        MTFStoryResult(
            state=MTFStoryState.RANGE_MIXED,
            timestamp=None,
            dominant_direction=Direction.NEUTRAL,
            macro_direction=Direction.NEUTRAL,
            context_state=ContextState.MIXED_CONTEXT,
            trigger_state=TriggerState.NO_TRIGGER,
            quality=50,
            confidence=0.5,
            timeframe_states=(first, second),
        )
