from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from financial_dashboard.context.envelope import ContextDataQuality, FactRef
from financial_dashboard.context.pattern_behavior_projection import (
    PatternBehaviorPhase,
    PatternBehaviorProjection,
    _phase as _native_pattern_phase,
)

from .reaction import ReactionAssessment, ReactionState
from .structural import DecisionHorizon, HorizonRelation, StructuralDirection


class SetupTriggerState(StrEnum):
    ABSENT = "ABSENT"
    FORMING = "FORMING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class TimingState(StrEnum):
    EARLY = "EARLY"
    DEVELOPING = "DEVELOPING"
    READY = "READY"
    EXTENDED = "EXTENDED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class SetupTriggerAssessment:
    state: SetupTriggerState
    timeframe: str
    reasons: tuple[str, ...]
    source_refs: tuple[FactRef, ...]


@dataclass(frozen=True, slots=True)
class TimingAssessment:
    state: TimingState
    timeframe: str
    setup_trigger: SetupTriggerAssessment
    reasons: tuple[str, ...]
    waiting_for: tuple[str, ...]
    source_refs: tuple[FactRef, ...]


def _direction_value(side: StructuralDirection) -> int:
    if side is StructuralDirection.LONG:
        return 1
    if side is StructuralDirection.SHORT:
        return -1
    return 0


def _pattern_row(pattern: PatternBehaviorProjection | None, timeframe: str):
    if pattern is None:
        return None
    try:
        row = pattern.for_timeframe(timeframe)
    except KeyError:
        return None

    # Pattern Compression is price-derived and consumes the already-filtered
    # closed+complete engine frame. Generic source-quality diagnostics can still be
    # DATA_LIMITED because of an open source tail or volume-only warnings. Those
    # diagnostics remain intact on the frozen projection, but must not erase a known
    # native Pattern state inside the Decision timing layer. This mirrors the
    # Decision-only Structure quality normalization and never mutates domain state.
    if row.ref.data_quality is ContextDataQuality.DATA_LIMITED:
        native_phase = _native_pattern_phase(row.native_state, unavailable=False)
        return replace(
            row,
            ref=replace(row.ref, data_quality=ContextDataQuality.VALID),
            phase=native_phase,
        )
    return row


def _unique_refs(*groups: tuple[FactRef, ...]) -> tuple[FactRef, ...]:
    by_key = {
        ref.deterministic_key: ref
        for group in groups
        for ref in group
    }
    return tuple(sorted(by_key.values(), key=lambda ref: ref.deterministic_key))


def assess_setup_trigger(
    side: StructuralDirection,
    *,
    reaction: ReactionAssessment,
    pattern: PatternBehaviorProjection | None,
    timeframe: str,
) -> SetupTriggerAssessment:
    """Classify setup maturity without creating a directional vote.

    ``reaction`` must already be restricted to the timing timeframe by the caller.
    Pattern is optional. A directional Pattern can confirm setup maturity only when
    its native direction agrees with the Structure-owned side. Pattern absence never
    blocks an otherwise confirmed reaction path.
    """

    normalized = timeframe.strip().lower()
    direction = _direction_value(side)
    if direction == 0:
        return SetupTriggerAssessment(
            SetupTriggerState.UNAVAILABLE,
            normalized,
            ("SETUP_SIDE_UNRESOLVED",),
            (),
        )

    row = _pattern_row(pattern, normalized)
    pattern_ref: tuple[FactRef, ...] = () if row is None else (row.ref,)
    pattern_available = (
        row is not None
        and row.ref.data_quality is ContextDataQuality.VALID
        and row.phase is not PatternBehaviorPhase.UNAVAILABLE
    )
    pattern_aligned = bool(
        pattern_available
        and int(row.classic_direction) == direction
    )
    pattern_neutral = bool(
        pattern_available
        and int(row.classic_direction) == 0
    )

    reaction_known = reaction.state is not ReactionState.UNKNOWN
    refs = _unique_refs(reaction.source_refs, pattern_ref)
    reasons: list[str] = []

    reaction_confirmed = reaction.state is ReactionState.CONFIRMED
    pattern_confirmed = bool(
        pattern_aligned
        and row.phase in {PatternBehaviorPhase.BREAK_CONFIRMED, PatternBehaviorPhase.RETEST_HELD}
    )
    if reaction_confirmed or pattern_confirmed:
        if reaction_confirmed:
            reasons.append("REACTION_SETUP_CONFIRMED")
        if pattern_confirmed:
            reasons.append(f"PATTERN_SETUP_CONFIRMED:{row.phase.value}")
        if reaction.failure_present:
            reasons.append("SETUP_HAS_MIXED_REACTION_LINEAGES")
        return SetupTriggerAssessment(
            SetupTriggerState.CONFIRMED,
            normalized,
            tuple(reasons),
            refs,
        )

    reaction_forming = reaction.state is ReactionState.DEVELOPING
    pattern_forming = bool(
        (pattern_aligned or pattern_neutral)
        and row.phase
        in {
            PatternBehaviorPhase.FORMING,
            PatternBehaviorPhase.MATURE_COMPRESSION,
            PatternBehaviorPhase.BREAK_ATTEMPT,
            PatternBehaviorPhase.BREAK_CONFIRMING,
            PatternBehaviorPhase.POST_BREAK_RETEST,
        }
    )
    if reaction_forming or pattern_forming:
        if reaction_forming:
            reasons.append("REACTION_SETUP_DEVELOPING")
        if pattern_forming:
            reasons.append(f"PATTERN_SETUP_DEVELOPING:{row.phase.value}")
        return SetupTriggerAssessment(
            SetupTriggerState.FORMING,
            normalized,
            tuple(reasons),
            refs,
        )

    reaction_failed = reaction.state is ReactionState.FAILED
    pattern_failed = bool(
        pattern_aligned
        and row.phase
        in {
            PatternBehaviorPhase.BREAK_FAILED,
            PatternBehaviorPhase.WEAKENING,
            PatternBehaviorPhase.INVALIDATED,
        }
    )
    if reaction_failed or pattern_failed:
        if reaction_failed:
            reasons.append("REACTION_SETUP_FAILED")
        if pattern_failed:
            reasons.append(f"PATTERN_SETUP_FAILED:{row.phase.value}")
        return SetupTriggerAssessment(
            SetupTriggerState.FAILED,
            normalized,
            tuple(reasons),
            refs,
        )

    if reaction_known or pattern_available:
        if reaction.state is ReactionState.ABSENT:
            reasons.append("REACTION_SETUP_ABSENT")
        if pattern_available:
            if row.phase is PatternBehaviorPhase.NO_PATTERN:
                reasons.append("NO_PATTERN_OBSERVED")
            elif row.phase is PatternBehaviorPhase.COMPLETED:
                reasons.append("PATTERN_ALREADY_COMPLETED")
            elif int(row.classic_direction) not in {0, direction}:
                reasons.append("PATTERN_DIRECTION_NOT_ALIGNED_WITH_STRUCTURE")
        return SetupTriggerAssessment(
            SetupTriggerState.ABSENT,
            normalized,
            tuple(reasons or ("SETUP_TRIGGER_NOT_PRESENT",)),
            refs,
        )

    return SetupTriggerAssessment(
        SetupTriggerState.UNAVAILABLE,
        normalized,
        ("SETUP_TRIGGER_EVIDENCE_UNAVAILABLE",),
        refs,
    )


def assess_timing(
    horizon: DecisionHorizon,
    side: StructuralDirection,
    relation: HorizonRelation,
    *,
    reaction: ReactionAssessment,
    pattern: PatternBehaviorProjection | None,
    timeframe: str,
) -> TimingAssessment:
    """Explicit v1 timing state machine with named guard conditions.

    EXTENDED is part of the public contract but intentionally not emitted in v1;
    no uncalibrated ATR/age threshold is introduced here.
    """

    setup = assess_setup_trigger(
        side,
        reaction=reaction,
        pattern=pattern,
        timeframe=timeframe,
    )
    refs = setup.source_refs
    normalized = timeframe.strip().lower()

    if setup.state is SetupTriggerState.UNAVAILABLE:
        return TimingAssessment(
            TimingState.UNAVAILABLE,
            normalized,
            setup,
            ("TIMING_INPUT_UNAVAILABLE",),
            (f"{normalized}:SETUP_TRIGGER_DATA",),
            refs,
        )

    if horizon is DecisionHorizon.LONG_TERM and relation in {
        HorizonRelation.COUNTER_REACTION,
        HorizonRelation.EARLY_TRANSITION,
        HorizonRelation.STRUCTURAL_CONFLICT,
    }:
        return TimingAssessment(
            TimingState.EARLY,
            normalized,
            setup,
            (f"LT_TIMING_HELD_BY_RELATION:{relation.value}",),
            ("LOWER_HORIZON_COUNTER_MOVE_TO_RESOLVE",),
            refs,
        )

    if horizon is DecisionHorizon.LONG_TERM and relation is HorizonRelation.ST_UNRESOLVED:
        return TimingAssessment(
            TimingState.UNAVAILABLE,
            normalized,
            setup,
            ("LT_ENTRY_TIMING_REQUIRES_VALID_ST_AUTHORITY",),
            ("1h:STRUCTURAL_TIMING_CONTEXT",),
            refs,
        )

    if setup.state is SetupTriggerState.FAILED:
        return TimingAssessment(
            TimingState.FAILED,
            normalized,
            setup,
            ("CURRENT_SETUP_PATH_FAILED",),
            ("NEW_SETUP_PATH",),
            refs,
        )
    if setup.state is SetupTriggerState.FORMING:
        return TimingAssessment(
            TimingState.DEVELOPING,
            normalized,
            setup,
            ("SETUP_TRIGGER_FORMING",),
            ("SETUP_TRIGGER_CONFIRMATION",),
            refs,
        )
    if setup.state is SetupTriggerState.CONFIRMED:
        return TimingAssessment(
            TimingState.READY,
            normalized,
            setup,
            ("SETUP_TRIGGER_CONFIRMED",),
            (),
            refs,
        )

    return TimingAssessment(
        TimingState.EARLY,
        normalized,
        setup,
        ("SETUP_TRIGGER_ABSENT",),
        ("SETUP_TRIGGER",),
        refs,
    )


__all__ = [
    "SetupTriggerAssessment",
    "SetupTriggerState",
    "TimingAssessment",
    "TimingState",
    "assess_setup_trigger",
    "assess_timing",
]
