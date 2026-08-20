from __future__ import annotations

from statistics import mean
from typing import Iterable

from .models import Direction
from .mtf_story_models import (
    ConflictSeverity,
    ContextAssessment,
    ContextState,
    MTFStoryResult,
    MTFStoryState,
    StoryConflict,
    TimeframeStoryState,
    TriggerAssessment,
    TriggerState,
)

_COMPRESSION_STATES = {"SIKISMA_GUCLENIYOR", "KIRILIM_HAZIRLIGI"}


def _latest_timestamp(states: Iterable[TimeframeStoryState]):
    timestamps = [state.timestamp for state in states if state.timestamp is not None]
    return max(timestamps) if timestamps else None


def _quality(states: tuple[TimeframeStoryState, ...], conflicts: tuple[StoryConflict, ...]) -> float:
    structural = [
        float(state.structural_quality)
        for state in states
        if state.usable and state.structural_quality is not None
    ]
    pattern = [
        float(state.pattern_quality)
        for state in states
        if state.usable and state.pattern_quality is not None
    ]
    families = []
    if structural:
        families.append(mean(structural))
    if pattern:
        families.append(mean(pattern))
    base = min(families) if families else 0.0
    penalty = sum(
        12.0 if conflict.severity is ConflictSeverity.BLOCKING else
        6.0 if conflict.severity is ConflictSeverity.WARNING else
        2.0
        for conflict in conflicts
    )
    return round(max(0.0, min(100.0, base - min(penalty, 24.0))), 2)


def _confidence(quality: float, context: ContextAssessment, trigger: TriggerAssessment) -> float:
    coverage = min(1.0, (len(context.usable_timeframes) + len(trigger.usable_timeframes)) / 6.0)
    return round(max(0.0, min(1.0, (quality / 100.0) * coverage)), 4)


def _has_compression(states: tuple[TimeframeStoryState, ...]) -> bool:
    return any(
        state.usable and state.pattern_state in _COMPRESSION_STATES
        for state in states
        if state.timeframe.strip().lower() in {"1h", "30m", "15m"}
    )


def classify_story(
    context: ContextAssessment,
    trigger: TriggerAssessment,
    timeframe_states: tuple[TimeframeStoryState, ...] | list[TimeframeStoryState],
) -> MTFStoryResult:
    """Combine hierarchical context and tactical trigger into one explainable story."""

    states = tuple(timeframe_states)
    reasons = list(context.reasons) + list(trigger.reasons)
    conflicts = list(context.conflicts) + list(trigger.conflicts)
    macro_direction = context.direction
    dominant_direction = trigger.direction if trigger.direction is not Direction.NEUTRAL else context.direction

    if context.state is ContextState.INSUFFICIENT_DATA or trigger.state is TriggerState.INSUFFICIENT_DATA:
        story = MTFStoryState.INSUFFICIENT_DATA
        dominant_direction = Direction.NEUTRAL
        reasons.append("STORY:INSUFFICIENT_CONTEXT_OR_TRIGGER_DATA")
    elif trigger.state is TriggerState.NO_TRIGGER:
        if _has_compression(states):
            story = MTFStoryState.COMPRESSION
            reasons.append("STORY:LOWER_TF_COMPRESSION_WITHOUT_TRIGGER")
        else:
            story = MTFStoryState.RANGE_MIXED
            reasons.append("STORY:NO_ACTIVE_TRIGGER")
            dominant_direction = context.direction
    elif context.state is ContextState.TRANSITION_CONTEXT:
        if trigger.state is TriggerState.REVERSAL_TRIGGER and trigger.direction is context.direction:
            story = MTFStoryState.REVERSAL_CONFIRMED
            reasons.append("STORY:CONTEXT_TRANSITION_AND_REVERSAL_TRIGGER_ALIGN")
        elif trigger.direction is context.direction and trigger.direction is not Direction.NEUTRAL:
            story = MTFStoryState.REVERSAL_BUILDING
            reasons.append("STORY:TRANSITION_CONTEXT_WITH_ALIGNED_TRIGGER")
        elif trigger.state is TriggerState.BREAKOUT_TRIGGER:
            story = MTFStoryState.BREAKOUT_BUILDING
            reasons.append("STORY:BREAKOUT_DURING_CONTEXT_TRANSITION")
        else:
            story = MTFStoryState.STRUCTURAL_CONFLICT
            reasons.append("STORY:TRIGGER_OPPOSES_TRANSITION_CONTEXT")
            conflicts.append(
                StoryConflict(
                    code="TRIGGER_OPPOSES_TRANSITION_CONTEXT",
                    message="Tactical trigger opposes the active higher-timeframe transition",
                    severity=ConflictSeverity.WARNING,
                    timeframes=("4h", "2h", "1h", "30m", "15m"),
                )
            )
    elif context.state in {ContextState.BULLISH_CONTEXT, ContextState.BEARISH_CONTEXT}:
        aligned = trigger.direction is context.direction and trigger.direction is not Direction.NEUTRAL
        opposed = (
            trigger.direction is not Direction.NEUTRAL
            and context.direction is not Direction.NEUTRAL
            and trigger.direction is not context.direction
        )

        if trigger.state is TriggerState.REVERSAL_TRIGGER and opposed:
            story = MTFStoryState.REVERSAL_BUILDING
            reasons.append("STORY:LOWER_TF_STRUCTURAL_REVERSAL_AGAINST_CONTEXT")
        elif trigger.state is TriggerState.BREAKOUT_TRIGGER:
            if aligned:
                story = MTFStoryState.BREAKOUT_CONFIRMED
                reasons.append("STORY:BREAKOUT_ALIGNS_WITH_CONTEXT")
            else:
                story = MTFStoryState.BREAKOUT_BUILDING
                reasons.append("STORY:COUNTER_CONTEXT_BREAKOUT")
        elif aligned:
            story = MTFStoryState.TREND_CONTINUATION
            reasons.append("STORY:CONTEXT_AND_TRIGGER_ALIGN")
        elif opposed and context.direction is Direction.DOWN and trigger.direction is Direction.UP:
            story = MTFStoryState.COUNTER_TREND_RALLY
            reasons.append("STORY:BULLISH_TRIGGER_INSIDE_BEARISH_CONTEXT")
        elif opposed and context.direction is Direction.UP and trigger.direction is Direction.DOWN:
            story = MTFStoryState.COUNTER_TREND_DROP
            reasons.append("STORY:BEARISH_TRIGGER_INSIDE_BULLISH_CONTEXT")
        else:
            story = MTFStoryState.RANGE_MIXED
            reasons.append("STORY:DIRECTIONAL_CONTEXT_WITHOUT_DECISIVE_TRIGGER")
    else:
        if trigger.state is TriggerState.BREAKOUT_TRIGGER:
            story = MTFStoryState.BREAKOUT_BUILDING
            reasons.append("STORY:BREAKOUT_INSIDE_MIXED_CONTEXT")
        elif trigger.direction is not Direction.NEUTRAL:
            story = MTFStoryState.STRUCTURAL_CONFLICT
            reasons.append("STORY:DIRECTIONAL_TRIGGER_INSIDE_MIXED_CONTEXT")
        elif _has_compression(states):
            story = MTFStoryState.COMPRESSION
            reasons.append("STORY:COMPRESSION_INSIDE_MIXED_CONTEXT")
        else:
            story = MTFStoryState.RANGE_MIXED
            reasons.append("STORY:MIXED_CONTEXT")

    conflict_tuple = tuple(conflicts)
    quality = _quality(states, conflict_tuple)
    confidence = _confidence(quality, context, trigger)

    return MTFStoryResult(
        state=story,
        timestamp=_latest_timestamp(states),
        dominant_direction=dominant_direction,
        macro_direction=macro_direction,
        context_state=context.state,
        trigger_state=trigger.state,
        quality=quality,
        confidence=confidence,
        timeframe_states=states,
        reasons=tuple(reasons),
        conflicts=conflict_tuple,
        is_confirmed=True,
    )
