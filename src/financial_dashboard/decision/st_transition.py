from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Iterable

import pandas as pd

from financial_dashboard.context.envelope import (
    ContextDataQuality,
    ContextDomain,
    FactRef,
    normalize_context_data_quality,
)
from financial_dashboard.context.permissions import (
    GateState,
    PermissionEnvelope,
    PermissionScope,
    PermittedSide,
)

from .conflict import ConflictAssessment, ConflictState, assess_conflict
from .environment import EnvironmentRisk, assess_environment
from .evidence_quality import normalize_decision_reaction_projections
from .opportunity import OpportunityAssessment, OpportunityCalibration, OpportunityState, assess_opportunity
from .participation import assess_participation
from .reaction import ReactionAssessment, ReactionRelevancePolicy, assess_reaction, select_relevant_zones
from .stabil_authority import StabilDecisionAssessment, assess_stabil_authority
from .structural import (
    DecisionHorizon,
    HorizonRelation,
    StructuralAssessment,
    StructuralDirection,
    ThesisState,
)
from .timing import TimingAssessment, TimingState, assess_timing

if TYPE_CHECKING:
    from financial_dashboard.decision_input import DecisionInputSnapshot


class STTransitionState(StrEnum):
    NONE = "NONE"
    WATCH = "WATCH"
    DEVELOPING = "DEVELOPING"
    STRONG = "STRONG"


@dataclass(frozen=True, slots=True)
class STLongTransitionAssessment:
    state: STTransitionState
    target_side: StructuralDirection
    canonical_transition_up: bool
    current_bullish_choch: bool
    reaction: ReactionAssessment
    timing_reaction: ReactionAssessment
    timing: TimingAssessment
    opportunity: OpportunityAssessment
    conflict: ConflictAssessment
    stabil: StabilDecisionAssessment
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    source_refs: tuple[FactRef, ...]

    @property
    def can_own_trade_thesis(self) -> bool:
        return self.state is STTransitionState.STRONG


def _event_time(event) -> pd.Timestamp | None:
    ref = getattr(event, "ref", None)
    if ref is None:
        return None
    value = getattr(ref, "available_at", None) or getattr(ref, "confirmed_at", None)
    if value is None:
        return None
    try:
        return pd.Timestamp(value)
    except (TypeError, ValueError):
        return None


def _eligible_external_events(snapshot: "DecisionInputSnapshot") -> tuple[object, ...]:
    try:
        row = snapshot.structure.for_timeframe("1h")
    except (KeyError, AttributeError, TypeError):
        return ()

    values = []
    for event in row.events:
        if str(event.scope).strip().upper() != "EXTERNAL":
            continue
        if str(event.confirmation_status).strip().upper() != "CONFIRMED":
            continue
        if str(event.validity).strip().upper() != "VALID":
            continue
        quality = normalize_context_data_quality(event.ref.data_quality)
        if quality not in {ContextDataQuality.VALID, ContextDataQuality.DATA_LIMITED}:
            continue
        timestamp = _event_time(event)
        if timestamp is None or timestamp > pd.Timestamp(snapshot.as_of):
            continue
        values.append(event)
    return tuple(values)


def _latest_event(
    events: Iterable[object],
    *,
    direction: int,
    event_type: str,
):
    token = event_type.strip().upper()
    candidates = []
    for event in events:
        if int(getattr(event, "direction", 0)) != int(direction):
            continue
        if str(getattr(event, "event_type", "")).strip().upper() != token:
            continue
        timestamp = _event_time(event)
        if timestamp is not None:
            candidates.append((timestamp, event))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _current_bullish_choch(snapshot: "DecisionInputSnapshot"):
    events = _eligible_external_events(snapshot)
    bullish_choch = _latest_event(events, direction=1, event_type="EVENT_CHOCH")
    bearish_bos = _latest_event(events, direction=-1, event_type="EVENT_BOS")
    if bullish_choch is None:
        return None
    choch_time = _event_time(bullish_choch)
    bos_time = _event_time(bearish_bos) if bearish_bos is not None else None
    if choch_time is None:
        return None
    if bos_time is not None and bos_time > choch_time:
        return None
    return bullish_choch


def _unique_refs(*groups: Iterable[FactRef]) -> tuple[FactRef, ...]:
    values = {
        ref.deterministic_key: ref
        for group in groups
        for ref in group
    }
    return tuple(sorted(values.values(), key=lambda ref: ref.deterministic_key))


def assess_st_long_transition(
    snapshot: "DecisionInputSnapshot",
    native_structural: StructuralAssessment,
    *,
    opportunity_calibration: OpportunityCalibration | None = None,
    reaction_relevance: ReactionRelevancePolicy | None = ReactionRelevancePolicy(),
    participation_conflict_max_age_bars: int | None = 24,
) -> STLongTransitionAssessment:
    """Assess a conservative 1H-owned early LONG thesis without mutating Structure.

    Native 1H Structure remains the price-structure authority, while daily Stabil can
    now prove that a bearish support regime has causally recovered before Structure
    finishes its own transition.  Stabil never creates a LONG thesis alone: a strong
    overlay still requires a current external bullish CHoCH, confirmed bullish
    reaction, READY 1H timing, usable directional room, low conflict, and no hard
    volatility/permission blocker.
    """

    reaction_ob, reaction_fvg = normalize_decision_reaction_projections(
        snapshot.order_block_behavior,
        snapshot.fvg_engulfing_lifecycle,
    )
    if reaction_relevance is not None:
        reaction_ob, reaction_fvg = select_relevant_zones(
            reaction_ob,
            reaction_fvg,
            current_price=snapshot.current_price,
            policy=reaction_relevance,
        )

    reaction = assess_reaction(
        StructuralDirection.LONG,
        order_blocks=reaction_ob,
        fvg_engulfing=reaction_fvg,
        timeframes=("4h", "2h", "1h", "30m"),
        relevance=reaction_relevance,
    )
    timing_reaction = assess_reaction(
        StructuralDirection.LONG,
        order_blocks=reaction_ob,
        fvg_engulfing=reaction_fvg,
        timeframes=("1h",),
        relevance=reaction_relevance,
    )
    timing = assess_timing(
        DecisionHorizon.SHORT_TERM,
        StructuralDirection.LONG,
        HorizonRelation.EARLY_TRANSITION,
        reaction=timing_reaction,
        pattern=snapshot.pattern_behavior,
        timeframe="1h",
    )
    opportunity = assess_opportunity(
        StructuralDirection.LONG,
        snapshot.targeting,
        calibration=opportunity_calibration,
    )
    participation = assess_participation(
        StructuralDirection.LONG,
        snapshot.participation_behavior,
        timeframe="1h",
        max_heavy_conflict_age_bars=participation_conflict_max_age_bars,
    )
    environment = assess_environment(
        StructuralDirection.LONG,
        snapshot.volatility_environment,
        timeframe="1h",
    )
    conflict = assess_conflict(
        StructuralDirection.LONG,
        reaction=reaction,
        participation=participation,
        environment=environment,
    )
    stabil = assess_stabil_authority(getattr(snapshot, "stabil_support", None))

    choch = _current_bullish_choch(snapshot)
    canonical_transition_up = bool(
        native_structural.direction is StructuralDirection.SHORT
        and native_structural.thesis_state is ThesisState.TRANSITIONING
        and native_structural.transition_target is StructuralDirection.LONG
    )
    current_bullish_choch = choch is not None
    stabil_recovery_authority = stabil.recovery_confirmed
    transition_authority = canonical_transition_up or stabil_recovery_authority

    blockers: list[str] = []
    reasons: list[str] = []
    if native_structural.data_quality is not ContextDataQuality.VALID:
        blockers.append(f"ST_NATIVE_STRUCTURE_{native_structural.data_quality.value}")
    if environment.risk is EnvironmentRisk.HARD_BLOCK:
        blockers.append("VOLATILITY_SHOCK")
    if conflict.state is ConflictState.HIGH:
        blockers.append("INDEPENDENT_FAMILY_CONFLICT_HIGH")
    if opportunity.state is OpportunityState.NONE:
        if bool(getattr(opportunity, "hard_room_constraint", True)):
            blockers.append("OPPORTUNITY_NONE")
        else:
            reasons.append("SOFT_TECHNICAL_ROOM_CONSTRAINT")
    if stabil.opposes_early_long:
        blockers.append("STABIL_BEARISH_AUTHORITY_OPPOSES_EARLY_LONG")

    if canonical_transition_up:
        reasons.append("CANONICAL_1H_TRANSITION_UP")
    if stabil.recovery_confirmed:
        reasons.append("STABIL_RECOVERY_CONFIRMED_EARLY_LONG_AUTHORITY")
    elif stabil.recovery_developing:
        reasons.append("STABIL_RECOVERY_DEVELOPING")
    elif stabil.opposes_early_long:
        reasons.append(f"STABIL_OPPOSES_EARLY_LONG:{stabil.state.value}")
    if current_bullish_choch:
        reasons.append("CURRENT_EXTERNAL_BULLISH_CHOCH")
    if reaction.confirmation_present:
        reasons.append("BULLISH_REACTION_CONFIRMED")
    elif reaction.developing_present:
        reasons.append("BULLISH_REACTION_DEVELOPING")
    if timing.state is TimingState.READY:
        reasons.append("CONFIRMED_1H_LONG_SETUP")
    elif timing.state is TimingState.DEVELOPING:
        reasons.append("DEVELOPING_1H_LONG_SETUP")
    if opportunity.state in {OpportunityState.AMPLE, OpportunityState.MODERATE}:
        reasons.append("CLEAR_ST_DIRECTIONAL_ROOM")
    if conflict.state in {ConflictState.NONE, ConflictState.LOW}:
        reasons.append("CLEAN_ST_LONG_CONFLICT_STATE")

    strong = bool(
        not blockers
        and transition_authority
        and current_bullish_choch
        and reaction.confirmation_present
        and timing.state is TimingState.READY
        and opportunity.state in {OpportunityState.AMPLE, OpportunityState.MODERATE}
        and conflict.state in {ConflictState.NONE, ConflictState.LOW}
    )
    developing = bool(
        (transition_authority or stabil.recovery_developing)
        and current_bullish_choch
        and (
            reaction.confirmation_present
            or reaction.developing_present
            or timing.state in {TimingState.DEVELOPING, TimingState.READY}
        )
    )
    watch = bool(
        native_structural.direction is StructuralDirection.SHORT
        and (
            current_bullish_choch
            or reaction.confirmation_present
            or reaction.developing_present
            or timing.state in {TimingState.DEVELOPING, TimingState.READY}
            or stabil.recovery_developing
            or stabil.recovery_confirmed
        )
    )

    if strong:
        state = STTransitionState.STRONG
        reasons.append("ST_LONG_TRANSITION_TRADE_THESIS_STRONG")
    elif developing:
        state = STTransitionState.DEVELOPING
        reasons.append("ST_LONG_TRANSITION_REQUIRES_MORE_CONFIRMATION")
    elif watch:
        state = STTransitionState.WATCH
        reasons.append("BULLISH_COUNTER_EVIDENCE_WATCH_ONLY")
    else:
        state = STTransitionState.NONE
        reasons.append("NO_QUALIFIED_ST_LONG_TRANSITION_EVIDENCE")

    structural_refs = () if choch is None else (choch.ref,)
    refs = _unique_refs(structural_refs, stabil.source_refs, reaction.source_refs, timing.source_refs)
    return STLongTransitionAssessment(
        state=state,
        target_side=StructuralDirection.LONG,
        canonical_transition_up=canonical_transition_up,
        current_bullish_choch=current_bullish_choch,
        reaction=reaction,
        timing_reaction=timing_reaction,
        timing=timing,
        opportunity=opportunity,
        conflict=conflict,
        stabil=stabil,
        reasons=tuple(dict.fromkeys(reasons)),
        blockers=tuple(dict.fromkeys(blockers)),
        source_refs=refs,
    )


def apply_strong_st_long_transition(
    native_structural: StructuralAssessment,
    transition: STLongTransitionAssessment,
) -> StructuralAssessment:
    """Create an explicit Decision trade-thesis overlay; source Structure stays native."""

    if not transition.can_own_trade_thesis:
        return native_structural
    transition_structural_refs = tuple(
        ref
        for ref in transition.source_refs
        if ref.domain is ContextDomain.MARKET_STRUCTURE
        and ref.timeframe.strip().lower() == native_structural.authority_timeframe.strip().lower()
    )
    refs = _unique_refs(native_structural.source_refs, transition_structural_refs)
    native_reason = (
        "NATIVE_1H_STRUCTURE_REMAINS_TRANSITIONING"
        if native_structural.thesis_state is ThesisState.TRANSITIONING
        else "NATIVE_1H_STRUCTURE_REMAINS_BEARISH_INTACT"
    )
    return replace(
        native_structural,
        direction=StructuralDirection.LONG,
        thesis_state=ThesisState.INTACT,
        transition_target=None,
        source_refs=refs,
        reasons=tuple(
            dict.fromkeys(
                (
                    *native_structural.reasons,
                    "DECISION_ST_TRANSITION_LONG_OVERLAY",
                    native_reason,
                    *transition.reasons,
                )
            )
        ),
    )


def reconcile_st_transition_permission(
    permission: PermissionEnvelope,
    transition: STLongTransitionAssessment,
) -> PermissionEnvelope:
    """Permit only a STRONG transition overlay; never bypass a native hard block."""

    if not transition.can_own_trade_thesis or permission.gate_state is GateState.BLOCKED:
        return permission
    refs = tuple(sorted(set((*permission.source_refs, *(ref.native_id for ref in transition.source_refs)))))
    return PermissionEnvelope(
        scope=PermissionScope.STRUCTURAL_TRANSITION,
        permitted_side=PermittedSide.LONG,
        gate_state=GateState.CONDITIONAL,
        allowed_reasons=tuple(
            dict.fromkeys(
                (*permission.allowed_reasons, "DECISION_ST_STRONG_TRANSITION_LONG")
            )
        ),
        blocking_reasons=(),
        waiting_for=("FUTURE_ACTION_LAYER_TIMING",),
        source_refs=refs,
    )


__all__ = [
    "STLongTransitionAssessment",
    "STTransitionState",
    "apply_strong_st_long_transition",
    "assess_st_long_transition",
    "reconcile_st_transition_permission",
]
