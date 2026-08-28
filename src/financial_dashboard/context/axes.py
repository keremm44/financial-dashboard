from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from .envelope import ContextDataQuality, FactRef
from .projections import (
    HamProjection,
    LiquidityProjection,
    ParticipationProjection,
    PatternProjection,
    StructuralEventProjection,
    StructuralFactsProjection,
    VolatilityProjection,
)
from .zone_interaction import ZoneInteractionState
from .zones import QualifiedZone, QualifiedZoneSide, ZoneIntelligenceSnapshot


class ContextDirection(StrEnum):
    NONE = "NONE"
    UP = "UP"
    DOWN = "DOWN"
    TWO_SIDED = "TWO_SIDED"


class StructuralThesis(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    TRANSITION_UP = "TRANSITION_UP"
    TRANSITION_DOWN = "TRANSITION_DOWN"
    UNRESOLVED = "UNRESOLVED"
    UNAVAILABLE = "UNAVAILABLE"


class ContinuationContext(StrEnum):
    ALIGNED = "ALIGNED"
    WEAK = "WEAK"
    # CONFLICTING: the latest current external event opposes the thesis but is
    # NOT a break (typically a counter-CHOCH — the natural structural trace of
    # a pullback). CONFLICTING_BREAK: the opposing event is a BOS, i.e. the
    # structural continuity itself broke against the thesis.
    CONFLICTING = "CONFLICTING"
    CONFLICTING_BREAK = "CONFLICTING_BREAK"
    ABSENT = "ABSENT"
    UNAVAILABLE = "UNAVAILABLE"


class ReactionContext(StrEnum):
    NONE = "NONE"
    DEVELOPING = "DEVELOPING"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class ReversalContext(StrEnum):
    NOT_PRESENT = "NOT_PRESENT"
    CANDIDATE = "CANDIDATE"
    STRUCTURALLY_CONFIRMED = "STRUCTURALLY_CONFIRMED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class ObjectiveContext(StrEnum):
    NONE = "NONE"
    UPSIDE = "UPSIDE"
    DOWNSIDE = "DOWNSIDE"
    TWO_SIDED = "TWO_SIDED"
    UNAVAILABLE = "UNAVAILABLE"


class ParticipationContext(StrEnum):
    SUPPORTIVE = "SUPPORTIVE"
    NEUTRAL = "NEUTRAL"
    WEAK = "WEAK"
    OPPOSING = "OPPOSING"
    MIXED = "MIXED"
    UNAVAILABLE = "UNAVAILABLE"


class VolatilityContext(StrEnum):
    BALANCED = "BALANCED"
    CONTRACTING = "CONTRACTING"
    EXPANDING_UP = "EXPANDING_UP"
    EXPANDING_DOWN = "EXPANDING_DOWN"
    WEAKENING = "WEAKENING"
    SHOCK = "SHOCK"
    PENDING = "PENDING"
    UNAVAILABLE = "UNAVAILABLE"


class PatternReadiness(StrEnum):
    NO_PATTERN = "NO_PATTERN"
    PATTERN_PRESENT = "PATTERN_PRESENT"
    BREAK_CONTEXT_PRESENT = "BREAK_CONTEXT_PRESENT"
    UNAVAILABLE = "UNAVAILABLE"


class MTFContext(StrEnum):
    ALIGNED = "ALIGNED"
    COUNTER_REACTION = "COUNTER_REACTION"
    MIXED = "MIXED"
    UNRESOLVED = "UNRESOLVED"
    UNAVAILABLE = "UNAVAILABLE"


class HamReadinessContext(StrEnum):
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class ConflictState(StrEnum):
    NONE = "NONE"
    LOW = "LOW"
    MATERIAL = "MATERIAL"
    HIGH = "HIGH"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class AxisReason:
    code: str
    detail: str
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ContextAxes:
    anchor_timeframe: str
    structural_thesis: StructuralThesis
    structural_direction: ContextDirection
    continuation: ContinuationContext
    reaction: ReactionContext
    reaction_direction: ContextDirection
    reversal: ReversalContext
    reversal_direction: ContextDirection
    objective: ObjectiveContext
    participation: ParticipationContext
    volatility: VolatilityContext
    pattern_readiness: PatternReadiness
    mtf: MTFContext
    ham_readiness: HamReadinessContext
    conflict: ConflictState
    reasons: tuple[AxisReason, ...] = ()


_TRANSITION_UP_STATES = {"TRANSITION_UP", "STATE_TRANSITION_UP"}
_TRANSITION_DOWN_STATES = {"TRANSITION_DOWN", "STATE_TRANSITION_DOWN"}
_TERMINAL_ZONE_INTERACTIONS = {
    ZoneInteractionState.ACCEPTED_THROUGH,
    ZoneInteractionState.INVALIDATED,
    ZoneInteractionState.HISTORICAL_REFERENCE,
}
_ACTIVE_REACTION_INTERACTIONS = {
    ZoneInteractionState.TESTING,
    ZoneInteractionState.DEFENDED,
    ZoneInteractionState.RECLAIMED,
    ZoneInteractionState.ROLE_REVERSAL_TEST,
}
_DEVELOPING_REACTION_INTERACTIONS = {
    ZoneInteractionState.APPROACHING,
}
_FAILED_REACTION_INTERACTIONS = {
    ZoneInteractionState.ACCEPTED_THROUGH,
    ZoneInteractionState.INVALIDATED,
}

# Stable typed-export codes from VolatilityState. They are normalized here instead of
# importing the native engine so this layer remains a read-model consumer only.
_VOL_PENDING = 0
_VOL_BALANCED = 1
_VOL_CONTRACTING = 2
_VOL_SQUEEZE = 3
_VOL_UP_CANDIDATE = 4
_VOL_UP_CONFIRMED = 5
_VOL_DOWN_CANDIDATE = 6
_VOL_DOWN_CONFIRMED = 7
_VOL_WEAKENING = 8
_VOL_SHOCK = 9


def _direction(value: int | None) -> ContextDirection:
    if value is None:
        return ContextDirection.NONE
    if int(value) > 0:
        return ContextDirection.UP
    if int(value) < 0:
        return ContextDirection.DOWN
    return ContextDirection.NONE


def _opposite(direction: ContextDirection) -> ContextDirection:
    if direction is ContextDirection.UP:
        return ContextDirection.DOWN
    if direction is ContextDirection.DOWN:
        return ContextDirection.UP
    return ContextDirection.NONE


def _usable_quality(value: ContextDataQuality) -> bool:
    return value is ContextDataQuality.VALID


def _event_ref(event: StructuralEventProjection) -> str:
    return event.ref.native_id


def _latest_current_external_event(
    structural: StructuralFactsProjection,
    anchor_timeframe: str,
) -> StructuralEventProjection | None:
    timeframe = structural.for_timeframe(anchor_timeframe)
    candidates = [
        event
        for event in timeframe.events
        if event.scope.upper() == "EXTERNAL"
        and event.confirmation_status == "CONFIRMED"
        and event.validity == "VALID"
        and event.relevance == "CURRENT"
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda event: event.ref.deterministic_key)


def evaluate_structural_thesis(
    structural: StructuralFactsProjection,
    *,
    anchor_timeframe: str,
) -> tuple[StructuralThesis, ContextDirection, tuple[AxisReason, ...]]:
    normalized = anchor_timeframe.strip().lower()
    try:
        item = structural.for_timeframe(normalized)
    except KeyError:
        return (
            StructuralThesis.UNAVAILABLE,
            ContextDirection.NONE,
            (AxisReason("ANCHOR_TF_UNAVAILABLE", normalized),),
        )
    if not _usable_quality(item.data_quality) or item.external is None:
        return (
            StructuralThesis.UNAVAILABLE,
            ContextDirection.NONE,
            (AxisReason("ANCHOR_STRUCTURE_UNAVAILABLE", normalized),),
        )

    state = item.external.state.strip().upper()
    direction = _direction(item.external.direction)
    if state in _TRANSITION_UP_STATES:
        thesis = StructuralThesis.TRANSITION_UP
        direction = ContextDirection.UP
    elif state in _TRANSITION_DOWN_STATES:
        thesis = StructuralThesis.TRANSITION_DOWN
        direction = ContextDirection.DOWN
    elif direction is ContextDirection.UP:
        thesis = StructuralThesis.UP
    elif direction is ContextDirection.DOWN:
        thesis = StructuralThesis.DOWN
    else:
        thesis = StructuralThesis.UNRESOLVED

    return (
        thesis,
        direction,
        (AxisReason("CANONICAL_ANCHOR_STRUCTURE", f"{normalized}:{state}"),),
    )


def evaluate_continuation(
    structural: StructuralFactsProjection,
    *,
    anchor_timeframe: str,
    structural_thesis: StructuralThesis,
    structural_direction: ContextDirection,
) -> tuple[ContinuationContext, tuple[AxisReason, ...]]:
    if structural_thesis is StructuralThesis.UNAVAILABLE:
        return ContinuationContext.UNAVAILABLE, (AxisReason("STRUCTURE_UNAVAILABLE", anchor_timeframe),)
    if structural_direction is ContextDirection.NONE or structural_thesis is StructuralThesis.UNRESOLVED:
        return ContinuationContext.ABSENT, (AxisReason("NO_ESTABLISHED_STRUCTURAL_DIRECTION", anchor_timeframe),)
    if structural_thesis in {StructuralThesis.TRANSITION_UP, StructuralThesis.TRANSITION_DOWN}:
        return ContinuationContext.ABSENT, (AxisReason("ANCHOR_IN_TRANSITION", structural_thesis.value),)

    latest = _latest_current_external_event(structural, anchor_timeframe)
    if latest is None:
        return ContinuationContext.WEAK, (AxisReason("NO_CURRENT_EXTERNAL_CONTINUATION_EVENT", anchor_timeframe),)

    event_direction = _direction(latest.direction)
    if latest.event_type.upper() == "BOS" and event_direction is structural_direction:
        return (
            ContinuationContext.ALIGNED,
            (AxisReason("CANONICAL_BOS_ALIGNED", latest.event_type, (_event_ref(latest),)),),
        )
    if event_direction is _opposite(structural_direction):
        if latest.event_type.upper() == "BOS":
            return (
                ContinuationContext.CONFLICTING_BREAK,
                (AxisReason("CURRENT_EXTERNAL_BREAK_OPPOSES_THESIS", latest.event_type, (_event_ref(latest),)),),
            )
        return (
            ContinuationContext.CONFLICTING,
            (AxisReason("CURRENT_EXTERNAL_EVENT_OPPOSES_THESIS", latest.event_type, (_event_ref(latest),)),),
        )
    return ContinuationContext.WEAK, (AxisReason("NO_ALIGNED_CANONICAL_BOS", latest.event_type, (_event_ref(latest),)),)


def _zone_reaction_direction(zone: QualifiedZone) -> ContextDirection:
    return ContextDirection.UP if zone.side is QualifiedZoneSide.SUPPORT else ContextDirection.DOWN


def evaluate_reaction(
    zones: ZoneIntelligenceSnapshot,
    *,
    structural_direction: ContextDirection,
) -> tuple[ReactionContext, ContextDirection, tuple[AxisReason, ...]]:
    candidates = [zone for zone in zones.zones if zone.reaction_refs and zone.is_currently_qualified]
    if not candidates:
        return ReactionContext.NONE, ContextDirection.NONE, (AxisReason("NO_REACTION_ZONE_CONTEXT", ""),)

    active = [zone for zone in candidates if zone.interaction in _ACTIVE_REACTION_INTERACTIONS]
    developing = [zone for zone in candidates if zone.interaction in _DEVELOPING_REACTION_INTERACTIONS]
    failed = [zone for zone in candidates if zone.interaction in _FAILED_REACTION_INTERACTIONS]

    selected: QualifiedZone | None = None
    context = ReactionContext.NONE
    if active:
        selected = min(active, key=lambda zone: (zone.distance_atr, zone.zone_id))
        context = ReactionContext.ACTIVE
    elif developing:
        selected = min(developing, key=lambda zone: (zone.distance_atr, zone.zone_id))
        context = ReactionContext.DEVELOPING
    elif failed and len(failed) == len(candidates):
        selected = min(failed, key=lambda zone: (zone.distance_atr, zone.zone_id))
        context = ReactionContext.FAILED

    if selected is None:
        return ReactionContext.NONE, ContextDirection.NONE, (AxisReason("REACTION_NOT_AT_INTERACTION_BOUNDARY", ""),)

    direction = _zone_reaction_direction(selected)
    relationship = (
        "COUNTER_TREND"
        if structural_direction in {ContextDirection.UP, ContextDirection.DOWN}
        and direction is _opposite(structural_direction)
        else "TREND_ALIGNED"
        if direction is structural_direction
        else "UNRESOLVED_RELATION"
    )
    refs = tuple(ref.native_id for ref in selected.reaction_refs)
    return (
        context,
        direction,
        (AxisReason("ZONE_REACTION_CONTEXT", f"{selected.zone_id}:{relationship}", refs),),
    )


def evaluate_reversal(
    structural: StructuralFactsProjection,
    *,
    anchor_timeframe: str,
    structural_thesis: StructuralThesis,
) -> tuple[ReversalContext, ContextDirection, tuple[AxisReason, ...]]:
    if structural_thesis is StructuralThesis.UNAVAILABLE:
        return ReversalContext.UNAVAILABLE, ContextDirection.NONE, (AxisReason("STRUCTURE_UNAVAILABLE", anchor_timeframe),)

    if structural_thesis is StructuralThesis.TRANSITION_UP:
        return ReversalContext.CANDIDATE, ContextDirection.UP, (AxisReason("ANCHOR_TRANSITION_UP", anchor_timeframe),)
    if structural_thesis is StructuralThesis.TRANSITION_DOWN:
        return ReversalContext.CANDIDATE, ContextDirection.DOWN, (AxisReason("ANCHOR_TRANSITION_DOWN", anchor_timeframe),)

    try:
        timeframe = structural.for_timeframe(anchor_timeframe)
    except KeyError:
        return ReversalContext.UNAVAILABLE, ContextDirection.NONE, (AxisReason("ANCHOR_TF_UNAVAILABLE", anchor_timeframe),)

    choch = [
        event
        for event in timeframe.events
        if event.scope.upper() == "EXTERNAL"
        and event.event_type.upper() == "CHOCH"
        and event.confirmation_status == "CONFIRMED"
    ]
    if not choch:
        return ReversalContext.NOT_PRESENT, ContextDirection.NONE, (AxisReason("NO_EXTERNAL_CHOCH", anchor_timeframe),)

    latest = max(choch, key=lambda event: event.ref.deterministic_key)
    direction = _direction(latest.direction)
    if latest.validity == "FAILED" or latest.outcome == "FAILED":
        return ReversalContext.FAILED, direction, (AxisReason("EXTERNAL_CHOCH_FAILED", latest.event_type, (_event_ref(latest),)),)
    if latest.outcome == "FOLLOW_THROUGH_CONFIRMED":
        return (
            ReversalContext.STRUCTURALLY_CONFIRMED,
            direction,
            (AxisReason("EXTERNAL_CHOCH_FOLLOW_THROUGH", latest.event_type, (_event_ref(latest),)),),
        )
    if latest.relevance == "CURRENT" and latest.validity == "VALID":
        return ReversalContext.CANDIDATE, direction, (AxisReason("CURRENT_EXTERNAL_CHOCH", latest.event_type, (_event_ref(latest),)),)
    return ReversalContext.NOT_PRESENT, ContextDirection.NONE, (AxisReason("HISTORICAL_EXTERNAL_CHOCH_ONLY", latest.event_type, (_event_ref(latest),)),)


def evaluate_objective(
    liquidity: LiquidityProjection | None,
    *,
    current_price: float,
) -> tuple[ObjectiveContext, tuple[AxisReason, ...]]:
    if liquidity is None:
        return ObjectiveContext.UNAVAILABLE, (AxisReason("LIQUIDITY_UNAVAILABLE", ""),)
    active = [item for item in liquidity.observations if item.target_eligible and _usable_quality(item.ref.data_quality)]
    if not active:
        return ObjectiveContext.NONE, (AxisReason("NO_ACTIVE_LIQUIDITY_OBJECTIVE", ""),)
    upside = any(float(item.low) > current_price for item in active)
    downside = any(float(item.high) < current_price for item in active)
    if upside and downside:
        state = ObjectiveContext.TWO_SIDED
    elif upside:
        state = ObjectiveContext.UPSIDE
    elif downside:
        state = ObjectiveContext.DOWNSIDE
    else:
        state = ObjectiveContext.NONE
    refs = tuple(sorted(item.ref.native_id for item in active))
    return state, (AxisReason("ACTIVE_LIQUIDITY_OBJECTIVES", state.value, refs),)


def evaluate_participation(
    participation: ParticipationProjection | None,
    *,
    anchor_timeframe: str,
    structural_direction: ContextDirection,
) -> tuple[ParticipationContext, tuple[AxisReason, ...]]:
    if participation is None:
        return ParticipationContext.UNAVAILABLE, (AxisReason("PARTICIPATION_UNAVAILABLE", anchor_timeframe),)
    normalized = anchor_timeframe.strip().lower()
    rows = [row for row in participation.timeframe_facts if row.timeframe == normalized]
    if not rows:
        return ParticipationContext.UNAVAILABLE, (AxisReason("PARTICIPATION_ANCHOR_TF_UNAVAILABLE", normalized),)
    row = rows[-1]
    if not _usable_quality(row.data_quality):
        return ParticipationContext.UNAVAILABLE, (AxisReason("PARTICIPATION_DATA_UNAVAILABLE", row.data_quality.value),)
    if row.status in {"VOLUME_UNAVAILABLE", "WARMUP"}:
        return ParticipationContext.UNAVAILABLE, (AxisReason("PARTICIPATION_NOT_READY", row.status),)
    if row.status == "LOW_PARTICIPATION":
        return ParticipationContext.WEAK, (AxisReason("LOW_PARTICIPATION", row.state, (row.ref.native_id,)),)

    direction = _direction(row.evidence_direction)
    if structural_direction is ContextDirection.NONE or direction is ContextDirection.NONE:
        return ParticipationContext.NEUTRAL, (AxisReason("PARTICIPATION_DIRECTION_NEUTRAL", row.state, (row.ref.native_id,)),)
    if direction is structural_direction:
        return ParticipationContext.SUPPORTIVE, (AxisReason("PARTICIPATION_ALIGNED", row.state, (row.ref.native_id,)),)
    return ParticipationContext.OPPOSING, (AxisReason("PARTICIPATION_OPPOSED", row.state, (row.ref.native_id,)),)


def evaluate_volatility(
    volatility: VolatilityProjection | None,
    *,
    anchor_timeframe: str,
) -> tuple[VolatilityContext, tuple[AxisReason, ...]]:
    if volatility is None:
        return VolatilityContext.UNAVAILABLE, (AxisReason("VOLATILITY_UNAVAILABLE", anchor_timeframe),)
    normalized = anchor_timeframe.strip().lower()
    rows = [row for row in volatility.timeframe_facts if row.timeframe == normalized]
    if not rows:
        return VolatilityContext.UNAVAILABLE, (AxisReason("VOLATILITY_ANCHOR_TF_UNAVAILABLE", normalized),)
    row = rows[-1]
    if not _usable_quality(row.data_quality) or row.regime_code is None:
        return VolatilityContext.UNAVAILABLE, (AxisReason("VOLATILITY_DATA_UNAVAILABLE", row.data_quality.value),)
    mapping = {
        _VOL_PENDING: VolatilityContext.PENDING,
        _VOL_BALANCED: VolatilityContext.BALANCED,
        _VOL_CONTRACTING: VolatilityContext.CONTRACTING,
        _VOL_SQUEEZE: VolatilityContext.CONTRACTING,
        _VOL_UP_CANDIDATE: VolatilityContext.EXPANDING_UP,
        _VOL_UP_CONFIRMED: VolatilityContext.EXPANDING_UP,
        _VOL_DOWN_CANDIDATE: VolatilityContext.EXPANDING_DOWN,
        _VOL_DOWN_CONFIRMED: VolatilityContext.EXPANDING_DOWN,
        _VOL_WEAKENING: VolatilityContext.WEAKENING,
        _VOL_SHOCK: VolatilityContext.SHOCK,
    }
    state = mapping.get(int(row.regime_code), VolatilityContext.PENDING)
    return state, (AxisReason("VOLATILITY_REGIME_CONTEXT", str(row.regime_code), (() if row.ref is None else (row.ref.native_id,))),)


def evaluate_pattern_readiness(
    pattern: PatternProjection | None,
    *,
    anchor_timeframe: str,
) -> tuple[PatternReadiness, tuple[AxisReason, ...]]:
    if pattern is None:
        return PatternReadiness.UNAVAILABLE, (AxisReason("PATTERN_UNAVAILABLE", anchor_timeframe),)
    normalized = anchor_timeframe.strip().lower()
    rows = [row for row in pattern.timeframe_facts if row.timeframe == normalized]
    if not rows:
        return PatternReadiness.UNAVAILABLE, (AxisReason("PATTERN_ANCHOR_TF_UNAVAILABLE", normalized),)
    row = rows[-1]
    if not _usable_quality(row.data_quality):
        return PatternReadiness.UNAVAILABLE, (AxisReason("PATTERN_DATA_UNAVAILABLE", row.data_quality.value),)
    if row.ref is None or row.pattern_state_code in {None, 0}:
        return PatternReadiness.NO_PATTERN, (AxisReason("NO_PATTERN_CONTEXT", normalized),)
    if row.break_state_code not in {None, 0}:
        return PatternReadiness.BREAK_CONTEXT_PRESENT, (AxisReason("PATTERN_BREAK_CONTEXT", str(row.break_state_code), (row.ref.native_id,)),)
    return PatternReadiness.PATTERN_PRESENT, (AxisReason("PATTERN_PRESENT", str(row.pattern_state_code), (row.ref.native_id,)),)


def evaluate_mtf(
    structural: StructuralFactsProjection,
    *,
    anchor_timeframe: str,
    structural_direction: ContextDirection,
    trigger_timeframes: Iterable[str] = ("1h", "30m"),
) -> tuple[MTFContext, tuple[AxisReason, ...]]:
    if structural_direction is ContextDirection.NONE:
        return MTFContext.UNRESOLVED, (AxisReason("ANCHOR_DIRECTION_UNRESOLVED", anchor_timeframe),)
    seen: list[ContextDirection] = []
    source_tfs: list[str] = []
    for timeframe in trigger_timeframes:
        normalized = timeframe.strip().lower()
        if normalized == anchor_timeframe.strip().lower():
            continue
        try:
            row = structural.for_timeframe(normalized)
        except KeyError:
            continue
        if not _usable_quality(row.data_quality) or row.external is None:
            continue
        direction = _direction(row.external.direction)
        if direction is not ContextDirection.NONE:
            seen.append(direction)
            source_tfs.append(normalized)
    if not seen:
        return MTFContext.UNAVAILABLE, (AxisReason("NO_TRIGGER_STRUCTURE_AVAILABLE", ""),)
    aligned = all(direction is structural_direction for direction in seen)
    opposed = all(direction is _opposite(structural_direction) for direction in seen)
    if aligned:
        return MTFContext.ALIGNED, (AxisReason("LTF_STRUCTURE_ALIGNED", ",".join(source_tfs)),)
    if opposed:
        return MTFContext.COUNTER_REACTION, (AxisReason("LTF_STRUCTURE_COUNTER_TO_ANCHOR", ",".join(source_tfs)),)
    return MTFContext.MIXED, (AxisReason("LTF_STRUCTURE_MIXED", ",".join(source_tfs)),)


def evaluate_ham_readiness(
    ham: HamProjection | None,
    *,
    anchor_timeframe: str,
) -> tuple[HamReadinessContext, tuple[AxisReason, ...]]:
    if ham is None:
        return HamReadinessContext.UNAVAILABLE, (AxisReason("HAM_UNAVAILABLE", anchor_timeframe),)
    normalized = anchor_timeframe.strip().lower()
    rows = [row for row in ham.timeframe_facts if row.timeframe == normalized]
    if not rows:
        return HamReadinessContext.UNAVAILABLE, (AxisReason("HAM_ANCHOR_TF_UNAVAILABLE", normalized),)
    row = rows[-1]
    if not _usable_quality(row.data_quality):
        return HamReadinessContext.UNAVAILABLE, (AxisReason("HAM_DATA_UNAVAILABLE", row.data_quality.value),)
    if all(family.ready for family in row.families):
        return HamReadinessContext.AVAILABLE, (AxisReason("HAM_FAMILIES_AVAILABLE", normalized),)
    return HamReadinessContext.DEGRADED, (AxisReason("HAM_PARTIAL_COVERAGE", normalized),)


def evaluate_conflict(
    *,
    structural_direction: ContextDirection,
    continuation: ContinuationContext,
    reaction: ReactionContext,
    reaction_direction: ContextDirection,
    reversal: ReversalContext,
    reversal_direction: ContextDirection,
    mtf: MTFContext,
    participation: ParticipationContext,
) -> tuple[ConflictState, tuple[AxisReason, ...]]:
    if structural_direction is ContextDirection.NONE:
        return ConflictState.UNRESOLVED, (AxisReason("STRUCTURAL_DIRECTION_UNRESOLVED", ""),)
    if reversal is ReversalContext.STRUCTURALLY_CONFIRMED and reversal_direction is _opposite(structural_direction):
        return ConflictState.HIGH, (AxisReason("STRUCTURAL_REVERSAL_OPPOSES_CURRENT_THESIS", reversal_direction.value),)
    if continuation is ContinuationContext.CONFLICTING_BREAK:
        return ConflictState.HIGH, (AxisReason("CANONICAL_CONTINUATION_BREAK_CONFLICT", ""),)
    if continuation is ContinuationContext.CONFLICTING:
        # A counter-CHOCH against an intact thesis is the structural trace of a
        # pullback, not a break of continuity: in a trend it marks exactly the
        # discount the entry layer is waiting for. HIGH is reserved for opposing
        # breaks; the independent-family conflict gate owns this severity.
        return ConflictState.MATERIAL, (AxisReason("COUNTER_CHOCH_PULLBACK_CONTEXT", ""),)
    if reaction is ReactionContext.ACTIVE and reaction_direction is _opposite(structural_direction):
        if mtf is MTFContext.COUNTER_REACTION or participation is ParticipationContext.OPPOSING:
            return ConflictState.MATERIAL, (AxisReason("COUNTER_REACTION_WITH_SUPPORTING_CONFLICT", reaction_direction.value),)
        return ConflictState.LOW, (AxisReason("COUNTER_REACTION_PRESENT", reaction_direction.value),)
    if mtf is MTFContext.MIXED or participation is ParticipationContext.OPPOSING:
        return ConflictState.MATERIAL, (AxisReason("SUPPORTING_CONTEXT_CONFLICT", f"{mtf.value}:{participation.value}"),)
    return ConflictState.NONE, (AxisReason("NO_MATERIAL_CONTEXT_CONFLICT", ""),)


def evaluate_context_axes(
    *,
    structural: StructuralFactsProjection,
    zones: ZoneIntelligenceSnapshot,
    anchor_timeframe: str,
    liquidity: LiquidityProjection | None = None,
    participation: ParticipationProjection | None = None,
    pattern: PatternProjection | None = None,
    volatility: VolatilityProjection | None = None,
    ham: HamProjection | None = None,
    trigger_timeframes: Iterable[str] = ("1h", "30m"),
) -> ContextAxes:
    normalized_anchor = anchor_timeframe.strip().lower()
    thesis, structural_direction, r1 = evaluate_structural_thesis(
        structural,
        anchor_timeframe=normalized_anchor,
    )
    continuation, r2 = evaluate_continuation(
        structural,
        anchor_timeframe=normalized_anchor,
        structural_thesis=thesis,
        structural_direction=structural_direction,
    )
    reaction, reaction_direction, r3 = evaluate_reaction(
        zones,
        structural_direction=structural_direction,
    )
    reversal, reversal_direction, r4 = evaluate_reversal(
        structural,
        anchor_timeframe=normalized_anchor,
        structural_thesis=thesis,
    )
    objective, r5 = evaluate_objective(liquidity, current_price=zones.current_price)
    participation_context, r6 = evaluate_participation(
        participation,
        anchor_timeframe=normalized_anchor,
        structural_direction=structural_direction,
    )
    volatility_context, r7 = evaluate_volatility(volatility, anchor_timeframe=normalized_anchor)
    pattern_readiness, r8 = evaluate_pattern_readiness(pattern, anchor_timeframe=normalized_anchor)
    mtf, r9 = evaluate_mtf(
        structural,
        anchor_timeframe=normalized_anchor,
        structural_direction=structural_direction,
        trigger_timeframes=trigger_timeframes,
    )
    ham_readiness, r10 = evaluate_ham_readiness(ham, anchor_timeframe=normalized_anchor)
    conflict, r11 = evaluate_conflict(
        structural_direction=structural_direction,
        continuation=continuation,
        reaction=reaction,
        reaction_direction=reaction_direction,
        reversal=reversal,
        reversal_direction=reversal_direction,
        mtf=mtf,
        participation=participation_context,
    )
    return ContextAxes(
        anchor_timeframe=normalized_anchor,
        structural_thesis=thesis,
        structural_direction=structural_direction,
        continuation=continuation,
        reaction=reaction,
        reaction_direction=reaction_direction,
        reversal=reversal,
        reversal_direction=reversal_direction,
        objective=objective,
        participation=participation_context,
        volatility=volatility_context,
        pattern_readiness=pattern_readiness,
        mtf=mtf,
        ham_readiness=ham_readiness,
        conflict=conflict,
        reasons=tuple((*r1, *r2, *r3, *r4, *r5, *r6, *r7, *r8, *r9, *r10, *r11)),
    )


__all__ = [
    "AxisReason",
    "ConflictState",
    "ContextAxes",
    "ContextDirection",
    "ContinuationContext",
    "HamReadinessContext",
    "MTFContext",
    "ObjectiveContext",
    "ParticipationContext",
    "PatternReadiness",
    "ReactionContext",
    "ReversalContext",
    "StructuralThesis",
    "VolatilityContext",
    "evaluate_context_axes",
    "evaluate_continuation",
    "evaluate_reaction",
    "evaluate_reversal",
    "evaluate_structural_thesis",
]
