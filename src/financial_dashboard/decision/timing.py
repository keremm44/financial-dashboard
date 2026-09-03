from __future__ import annotations

from dataclasses import dataclass, is_dataclass, replace
from enum import StrEnum
from types import SimpleNamespace

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
    ADVERSE = "ADVERSE"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class TimingState(StrEnum):
    EARLY = "EARLY"
    DEVELOPING = "DEVELOPING"
    READY = "READY"
    EXTENDED = "EXTENDED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class TimingEntryEffect(StrEnum):
    """Entry authority carried by short-term Timing, separate from maturity state."""

    UNKNOWN = "UNKNOWN"
    NEUTRAL = "NEUTRAL"
    SUPPORTIVE = "SUPPORTIVE"
    ADVERSE = "ADVERSE"
    FAILED = "FAILED"


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
    entry_effect: TimingEntryEffect = TimingEntryEffect.UNKNOWN


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
        normalized_ref = replace(row.ref, data_quality=ContextDataQuality.VALID)
        if is_dataclass(row):
            return replace(row, ref=normalized_ref, phase=native_phase)
        # Unit-test and plugin doubles may intentionally be opaque/simple objects.
        # Preserve that compatibility without changing the production dataclass path.
        values = dict(vars(row))
        values.update(ref=normalized_ref, phase=native_phase)
        return SimpleNamespace(**values)
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
    its native direction agrees with the Structure-owned side. A confirmed opposing
    Pattern is explicit short-term adverse evidence rather than neutral absence.
    Pattern absence never blocks an otherwise confirmed reaction path.
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
    pattern_direction = int(row.classic_direction) if pattern_available else 0
    pattern_aligned = bool(pattern_available and pattern_direction == direction)
    pattern_neutral = bool(pattern_available and pattern_direction == 0)
    pattern_opposing_confirmed = bool(
        pattern_available
        and pattern_direction == -direction
        and row.phase in {PatternBehaviorPhase.BREAK_CONFIRMED, PatternBehaviorPhase.RETEST_HELD}
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

    if pattern_opposing_confirmed:
        return SetupTriggerAssessment(
            SetupTriggerState.ADVERSE,
            normalized,
            (f"OPPOSING_PATTERN_CONFIRMED:{row.phase.value}",),
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
            elif pattern_direction not in {0, direction}:
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
    """Explicit v1 timing state machine with separate ST entry authority.

    Timing maturity remains observable for both horizons. ``entry_effect`` prevents
    ST eligibility from treating every non-READY maturity state as a veto. LT keeps
    its legacy maturity requirement in Eligibility. EXTENDED remains part of the
    public contract but is intentionally not emitted without calibration.
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
            TimingEntryEffect.UNKNOWN,
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
            TimingEntryEffect.ADVERSE,
        )

    if horizon is DecisionHorizon.LONG_TERM and relation is HorizonRelation.ST_UNRESOLVED:
        return TimingAssessment(
            TimingState.UNAVAILABLE,
            normalized,
            setup,
            ("LT_ENTRY_TIMING_REQUIRES_VALID_ST_AUTHORITY",),
            ("1h:STRUCTURAL_TIMING_CONTEXT",),
            refs,
            TimingEntryEffect.UNKNOWN,
        )

    if setup.state is SetupTriggerState.ADVERSE:
        if horizon is DecisionHorizon.LONG_TERM:
            return TimingAssessment(
                TimingState.EARLY,
                normalized,
                setup,
                ("SETUP_TRIGGER_ABSENT",),
                ("SETUP_TRIGGER",),
                refs,
                TimingEntryEffect.ADVERSE,
            )
        return TimingAssessment(
            TimingState.EARLY,
            normalized,
            setup,
            ("SHORT_TERM_PATTERN_OPPOSES_STRUCTURE",),
            ("NEW_SETUP_PATH",),
            refs,
            TimingEntryEffect.ADVERSE,
        )

    if setup.state is SetupTriggerState.FAILED:
        return TimingAssessment(
            TimingState.FAILED,
            normalized,
            setup,
            ("CURRENT_SETUP_PATH_FAILED",),
            ("NEW_SETUP_PATH",),
            refs,
            TimingEntryEffect.FAILED,
        )
    if setup.state is SetupTriggerState.FORMING:
        return TimingAssessment(
            TimingState.DEVELOPING,
            normalized,
            setup,
            ("SETUP_TRIGGER_FORMING",),
            ("SETUP_TRIGGER_CONFIRMATION",),
            refs,
            TimingEntryEffect.NEUTRAL,
        )
    if setup.state is SetupTriggerState.CONFIRMED:
        return TimingAssessment(
            TimingState.READY,
            normalized,
            setup,
            ("SETUP_TRIGGER_CONFIRMED",),
            (),
            refs,
            TimingEntryEffect.SUPPORTIVE,
        )

    return TimingAssessment(
        TimingState.EARLY,
        normalized,
        setup,
        ("SETUP_TRIGGER_ABSENT",),
        ("SETUP_TRIGGER",),
        refs,
        TimingEntryEffect.NEUTRAL,
    )


__all__ = [
    "SetupTriggerAssessment",
    "SetupTriggerState",
    "TimingAssessment",
    "TimingEntryEffect",
    "TimingState",
    "assess_setup_trigger",
    "assess_timing",
]
