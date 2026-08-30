from __future__ import annotations

from dataclasses import dataclass, field, replace

from financial_dashboard.context.axes import evaluate_context_axes
from financial_dashboard.context.envelope import (
    ContextDataQuality,
    FactRef,
    normalize_context_data_quality,
)
from financial_dashboard.context.permissions import PermissionEnvelope, resolve_permission_axes
from financial_dashboard.context.projections import StructuralFactsProjection
from financial_dashboard.decision_input import DecisionInputSnapshot

from .composer import ActionPolicy, FinalDecision, compose_final_decision
from .conflict import ConflictAssessment, ConflictState, assess_conflict
from .coverage import CoverageAssessment, CoverageFamily, assess_coverage
from .durability import DurabilityAssessment, assess_durability
from .eligibility import EligibilityAssessment, EligibilityState, assess_eligibility
from .environment import EnvironmentAssessment, assess_environment
from .evidence_quality import normalize_decision_reaction_projections
from .execution import (
    ExecutionTriggerAssessment,
    ExecutionTriggerEvent,
    assess_execution_trigger,
)
from .opportunity import OpportunityAssessment, OpportunityCalibration, OpportunityState, assess_opportunity
from .participation import ParticipationAssessment, assess_participation
from .reaction import (
    ReactionAssessment,
    ReactionRelevancePolicy,
    assess_reaction,
    select_relevant_zones,
)
from .st_transition import (
    STLongTransitionAssessment,
    apply_strong_st_long_transition,
    assess_st_long_transition,
    reconcile_st_transition_permission,
)
from .structural import (
    DecisionHorizon,
    HorizonStructuralSnapshot,
    StructuralAssessment,
    StructuralDirection,
    ThesisState,
    build_horizon_structural_snapshot,
)
from .timing import TimingAssessment, TimingState, assess_timing


_LT_REACTION_TIMEFRAMES = ("1d", "4h", "2h", "1h")
_ST_REACTION_TIMEFRAMES = ("4h", "2h", "1h", "30m")


@dataclass(frozen=True, slots=True)
class DecisionEngineConfig:
    """Decision policy/calibration inputs; primary execution is closed 1h."""

    opportunity_calibration: OpportunityCalibration | None = None
    reaction_relevance: ReactionRelevancePolicy | None = ReactionRelevancePolicy()
    participation_conflict_max_age_bars: int | None = 24
    action_policy: ActionPolicy = field(default_factory=ActionPolicy)
    execution_timeframe: str = "1h"

    def __post_init__(self) -> None:
        if self.execution_timeframe.strip().lower() != "1h":
            raise ValueError("primary decision execution timeframe is architecturally fixed to 1h")
        if (
            self.participation_conflict_max_age_bars is not None
            and self.participation_conflict_max_age_bars < 0
        ):
            raise ValueError("participation_conflict_max_age_bars must be >= 0 when provided")


@dataclass(frozen=True, slots=True)
class HorizonDecisionAssessment:
    horizon: DecisionHorizon
    as_of: object
    structural_snapshot: HorizonStructuralSnapshot
    structural: StructuralAssessment
    permission: PermissionEnvelope
    durability: DurabilityAssessment
    reaction: ReactionAssessment
    timing_reaction: ReactionAssessment
    participation: ParticipationAssessment
    environment: EnvironmentAssessment
    opportunity: OpportunityAssessment
    coverage: CoverageAssessment
    conflict: ConflictAssessment
    timing: TimingAssessment
    execution: ExecutionTriggerAssessment
    eligibility: EligibilityAssessment
    final: FinalDecision
    st_transition: STLongTransitionAssessment | None = None


def _timeframe_policy(horizon: DecisionHorizon) -> tuple[tuple[str, ...], str, str, str]:
    if horizon is DecisionHorizon.LONG_TERM:
        # LT remains 1D-owned; 1H is its immediate setup/execution context.
        return _LT_REACTION_TIMEFRAMES, "4h", "4h", "1h"
    # ST is a 3-9 trading-day thesis. 1H owns setup maturity and primary execution;
    # 30m stays inside the broad reaction sphere only as micro context.
    return _ST_REACTION_TIMEFRAMES, "1h", "1h", "1h"


def _permission_policy(horizon: DecisionHorizon) -> tuple[str, tuple[str, ...]]:
    """Return structural anchor and context TFs without giving 30m veto authority."""

    if horizon is DecisionHorizon.LONG_TERM:
        return "1d", ("4h", "2h", "1h")
    # 4H is context for an independently owned 1H ST thesis. 30m is intentionally
    # absent here so a noisy micro move cannot flip or suppress ST permission.
    return "1h", ("4h",)


def _decision_structure_projection(structural):
    """Normalize price-only Structure quality for Decision without mutating source diagnostics."""

    if not isinstance(structural, StructuralFactsProjection):
        return structural

    changed = False
    rows = []
    for row in structural.timeframe_facts:
        if row.data_quality is not ContextDataQuality.DATA_LIMITED:
            rows.append(row)
            continue
        changed = True
        events = tuple(
            replace(
                event,
                ref=replace(event.ref, data_quality=ContextDataQuality.VALID),
            )
            if event.ref.data_quality is ContextDataQuality.DATA_LIMITED
            else event
            for event in row.events
        )
        rows.append(
            replace(
                row,
                data_quality=ContextDataQuality.VALID,
                events=events,
            )
        )
    if not changed:
        return structural
    return replace(structural, timeframe_facts=tuple(rows))


def _horizon_permission(
    snapshot: DecisionInputSnapshot,
    horizon: DecisionHorizon,
) -> PermissionEnvelope:
    anchor_timeframe, trigger_timeframes = _permission_policy(horizon)
    structural = _decision_structure_projection(snapshot.structure)
    axes = evaluate_context_axes(
        structural=structural,
        zones=snapshot.qualified_zones,
        anchor_timeframe=anchor_timeframe,
        liquidity=snapshot.liquidity,
        participation=snapshot.participation,
        pattern=snapshot.pattern,
        volatility=snapshot.volatility,
        ham=snapshot.ham,
        trigger_timeframes=trigger_timeframes,
    )
    return resolve_permission_axes(axes)


def _pattern_quality(snapshot: DecisionInputSnapshot, timeframe: str) -> ContextDataQuality:
    projection = snapshot.pattern_behavior
    if projection is None:
        return ContextDataQuality.UNAVAILABLE
    try:
        return normalize_context_data_quality(projection.for_timeframe(timeframe).ref.data_quality)
    except KeyError:
        return ContextDataQuality.UNAVAILABLE


def _execution_channel_quality(
    snapshot: DecisionInputSnapshot,
    timeframe: str,
) -> ContextDataQuality:
    """Return quality for the native price-pattern execution channel."""

    normalized = timeframe.strip().lower()
    projection = getattr(snapshot, "pattern_behavior", None)
    if projection is not None:
        try:
            row = projection.for_timeframe(normalized)
        except (KeyError, AttributeError, TypeError):
            row = None
        if row is not None:
            quality = normalize_context_data_quality(row.ref.data_quality)
            if quality is ContextDataQuality.VALID:
                return quality
            if quality is ContextDataQuality.DATA_LIMITED:
                native_state = str(getattr(row, "native_state", "") or "").strip()
                phase = str(
                    getattr(getattr(row, "phase", None), "value", getattr(row, "phase", "")) or ""
                ).strip().upper()
                if native_state or (phase and phase != "UNAVAILABLE"):
                    return ContextDataQuality.VALID
            return quality

    return normalize_context_data_quality(snapshot.quality_for_timeframe(normalized))


def _ham_quality(snapshot: DecisionInputSnapshot, timeframe: str) -> ContextDataQuality:
    if snapshot.ham is None:
        return ContextDataQuality.UNAVAILABLE
    normalized = timeframe.strip().lower()
    for row in snapshot.ham.timeframe_facts:
        if row.timeframe == normalized:
            return normalize_context_data_quality(row.data_quality)
    return ContextDataQuality.UNAVAILABLE


def _liquidity_quality(snapshot: DecisionInputSnapshot, timeframe: str) -> ContextDataQuality:
    projection = snapshot.liquidity_landscape
    if projection is None:
        return ContextDataQuality.UNAVAILABLE
    try:
        return normalize_context_data_quality(projection.for_timeframe(timeframe).ref.data_quality)
    except KeyError:
        return ContextDataQuality.UNAVAILABLE


def _coverage(
    snapshot: DecisionInputSnapshot,
    *,
    structural: StructuralAssessment,
    durability: DurabilityAssessment,
    reaction: ReactionAssessment,
    participation: ParticipationAssessment,
    environment: EnvironmentAssessment,
    timing_timeframe: str,
) -> CoverageAssessment:
    qualities = {
        CoverageFamily.STRUCTURE: structural.data_quality,
        CoverageFamily.STABIL: durability.data_quality,
        CoverageFamily.LIQUIDITY: _liquidity_quality(snapshot, timing_timeframe),
        CoverageFamily.REACTION: reaction.data_quality,
        CoverageFamily.PARTICIPATION: participation.data_quality,
        CoverageFamily.VOLATILITY: environment.data_quality,
        CoverageFamily.PATTERN: _pattern_quality(snapshot, timing_timeframe),
        CoverageFamily.HAM: _ham_quality(snapshot, timing_timeframe),
        CoverageFamily.TARGETING: (
            ContextDataQuality.VALID
            if snapshot.targeting is not None
            else ContextDataQuality.UNAVAILABLE
        ),
    }
    expected = tuple(CoverageFamily)
    return assess_coverage(
        qualities,
        expected_families=expected,
        critical_families=(CoverageFamily.STRUCTURE,),
    )


def _lineage_from_refs(refs: tuple[FactRef, ...]) -> set[str]:
    return {
        ref.lineage_id or f"{ref.domain.value}:{ref.timeframe}:{ref.native_id}"
        for ref in refs
    }


def _additional_lineage(
    *,
    durability: DurabilityAssessment,
    reaction: ReactionAssessment,
    participation: ParticipationAssessment,
    environment: EnvironmentAssessment,
    timing: TimingAssessment,
    opportunity: OpportunityAssessment,
    conflict: ConflictAssessment,
) -> tuple[str, ...]:
    values: set[str] = set(opportunity.source_lineage)
    for refs in (
        durability.source_refs,
        reaction.source_refs,
        participation.source_refs,
        environment.source_refs,
        timing.source_refs,
    ):
        values.update(_lineage_from_refs(refs))
    for family in conflict.families:
        values.update(family.lineage_ids)
    return tuple(sorted(values))


def _apply_counter_lt_st_risk(
    horizon: DecisionHorizon,
    structural_snapshot: HorizonStructuralSnapshot,
    structural: StructuralAssessment,
    eligibility: EligibilityAssessment,
    *,
    timing: TimingAssessment,
    opportunity: OpportunityAssessment,
    conflict: ConflictAssessment,
) -> EligibilityAssessment:
    """Allow counter-LT ST trades, but require cleaner economics and conflict state."""

    if horizon is not DecisionHorizon.SHORT_TERM:
        return eligibility
    lt = structural_snapshot.long_term
    counter_lt = (
        structural.direction is StructuralDirection.LONG
        and lt.direction is StructuralDirection.SHORT
        and lt.thesis_state is ThesisState.INTACT
        and lt.data_quality in {ContextDataQuality.VALID, ContextDataQuality.DATA_LIMITED}
    )
    if not counter_lt:
        return eligibility
    if eligibility.state is EligibilityState.BLOCKED:
        return eligibility

    waiting = list(eligibility.waiting_for)
    reasons = list(eligibility.reasons)
    if timing.state is not TimingState.READY:
        waiting.append("COUNTER_LT_ST_REQUIRES_CONFIRMED_1H_SETUP")
    if opportunity.state not in {OpportunityState.MODERATE, OpportunityState.AMPLE}:
        waiting.append("COUNTER_LT_ST_REQUIRES_CLEAR_DIRECTIONAL_ROOM")
    if conflict.state not in {ConflictState.NONE, ConflictState.LOW}:
        waiting.append("COUNTER_LT_ST_REQUIRES_LOW_CONFLICT")

    if waiting:
        return EligibilityAssessment(
            EligibilityState.WAITING,
            tuple(dict.fromkeys((*reasons, "COUNTER_LT_ST_RISK_REQUIRES_STRONGER_EVIDENCE"))),
            (),
            tuple(dict.fromkeys(waiting)),
        )
    return EligibilityAssessment(
        EligibilityState.ELIGIBLE,
        tuple(dict.fromkeys((*reasons, "COUNTER_LT_ST_RISK_ACCEPTED"))),
        (),
        (),
    )


def assess_horizon_decision(
    snapshot: DecisionInputSnapshot,
    horizon: DecisionHorizon,
    *,
    config: DecisionEngineConfig | None = None,
    execution_event: ExecutionTriggerEvent | None = None,
) -> HorizonDecisionAssessment:
    """Build one fully typed LT or ST decision assessment.

    Native Structure remains immutable.  A SHORT_TERM assessment may use an explicit
    Decision-only LONG trade-thesis overlay while native 1H Structure is in a
    quality-gated canonical transition toward LONG.  The overlay cannot exist from
    an intact bearish state and cannot bypass permission, setup, opportunity,
    conflict, volatility, or execution gates.
    """

    cfg = config or DecisionEngineConfig()
    decision_structure = _decision_structure_projection(snapshot.structure)
    structural_snapshot = build_horizon_structural_snapshot(decision_structure)
    native_structural = (
        structural_snapshot.long_term
        if horizon is DecisionHorizon.LONG_TERM
        else structural_snapshot.short_term
    )
    reaction_timeframes, participation_tf, environment_tf, timing_tf = _timeframe_policy(horizon)
    permission = _horizon_permission(snapshot, horizon)

    st_transition = None
    structural = native_structural
    if horizon is DecisionHorizon.SHORT_TERM and native_structural.direction is StructuralDirection.SHORT:
        st_transition = assess_st_long_transition(
            snapshot,
            native_structural,
            opportunity_calibration=cfg.opportunity_calibration,
            reaction_relevance=cfg.reaction_relevance,
            participation_conflict_max_age_bars=cfg.participation_conflict_max_age_bars,
        )
        structural = apply_strong_st_long_transition(native_structural, st_transition)
        permission = reconcile_st_transition_permission(permission, st_transition)

    durability = assess_durability(snapshot.stabil_support)
    reaction_ob, reaction_fvg = normalize_decision_reaction_projections(
        snapshot.order_block_behavior,
        snapshot.fvg_engulfing_lifecycle,
    )
    if cfg.reaction_relevance is not None:
        reaction_ob, reaction_fvg = select_relevant_zones(
            reaction_ob,
            reaction_fvg,
            current_price=snapshot.current_price,
            policy=cfg.reaction_relevance,
        )
    reaction = assess_reaction(
        structural.direction,
        order_blocks=reaction_ob,
        fvg_engulfing=reaction_fvg,
        timeframes=reaction_timeframes,
        relevance=cfg.reaction_relevance,
    )
    timing_reaction = assess_reaction(
        structural.direction,
        order_blocks=reaction_ob,
        fvg_engulfing=reaction_fvg,
        timeframes=(timing_tf,),
        relevance=cfg.reaction_relevance,
    )
    participation = assess_participation(
        structural.direction,
        snapshot.participation_behavior,
        timeframe=participation_tf,
        max_heavy_conflict_age_bars=cfg.participation_conflict_max_age_bars,
    )
    environment = assess_environment(
        structural.direction,
        snapshot.volatility_environment,
        timeframe=environment_tf,
    )
    opportunity = assess_opportunity(
        structural.direction,
        snapshot.targeting,
        calibration=cfg.opportunity_calibration,
    )
    coverage = _coverage(
        snapshot,
        structural=structural,
        durability=durability,
        reaction=reaction,
        participation=participation,
        environment=environment,
        timing_timeframe=timing_tf,
    )
    conflict = assess_conflict(
        structural.direction,
        reaction=reaction,
        participation=participation,
        environment=environment,
    )
    timing = assess_timing(
        horizon,
        structural.direction,
        structural_snapshot.relation,
        reaction=timing_reaction,
        location_reaction=reaction if horizon is DecisionHorizon.LONG_TERM else None,
        pattern=snapshot.pattern_behavior,
        timeframe=timing_tf,
    )
    eligibility = assess_eligibility(
        structural,
        permission=permission,
        timing=timing,
        opportunity=opportunity,
        conflict=conflict,
        environment=environment,
        coverage=coverage,
        reaction=reaction,
    )
    eligibility = _apply_counter_lt_st_risk(
        horizon,
        structural_snapshot,
        structural,
        eligibility,
        timing=timing,
        opportunity=opportunity,
        conflict=conflict,
    )
    execution = assess_execution_trigger(
        structural.direction,
        as_of=snapshot.as_of,
        timeframe=cfg.execution_timeframe,
        data_quality=_execution_channel_quality(snapshot, cfg.execution_timeframe),
        event=execution_event,
    )
    final = compose_final_decision(
        structural,
        eligibility=eligibility,
        execution=execution,
        policy=cfg.action_policy,
        additional_lineage=_additional_lineage(
            durability=durability,
            reaction=reaction,
            participation=participation,
            environment=environment,
            timing=timing,
            opportunity=opportunity,
            conflict=conflict,
        ),
    )

    return HorizonDecisionAssessment(
        horizon=horizon,
        as_of=snapshot.as_of,
        structural_snapshot=structural_snapshot,
        structural=structural,
        permission=permission,
        durability=durability,
        reaction=reaction,
        timing_reaction=timing_reaction,
        participation=participation,
        environment=environment,
        opportunity=opportunity,
        coverage=coverage,
        conflict=conflict,
        timing=timing,
        execution=execution,
        eligibility=eligibility,
        final=final,
        st_transition=st_transition,
    )


__all__ = [
    "DecisionEngineConfig",
    "HorizonDecisionAssessment",
    "assess_horizon_decision",
]
