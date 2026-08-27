from __future__ import annotations

from dataclasses import dataclass, field

from financial_dashboard.context.axes import evaluate_context_axes
from financial_dashboard.context.envelope import ContextDataQuality, FactRef
from financial_dashboard.context.permissions import PermissionEnvelope, resolve_permission_axes
from financial_dashboard.decision_input import DecisionInputSnapshot

from .composer import ActionPolicy, FinalDecision, compose_final_decision
from .conflict import ConflictAssessment, assess_conflict
from .coverage import CoverageAssessment, CoverageFamily, assess_coverage
from .durability import DurabilityAssessment, assess_durability
from .eligibility import EligibilityAssessment, assess_eligibility
from .environment import EnvironmentAssessment, assess_environment
from .execution import (
    ExecutionTriggerAssessment,
    ExecutionTriggerEvent,
    assess_execution_trigger,
)
from .opportunity import OpportunityAssessment, OpportunityCalibration, assess_opportunity
from .participation import ParticipationAssessment, assess_participation
from .reaction import ReactionAssessment, assess_reaction
from .structural import (
    DecisionHorizon,
    HorizonStructuralSnapshot,
    StructuralAssessment,
    build_horizon_structural_snapshot,
)
from .timing import TimingAssessment, assess_timing


_LT_REACTION_TIMEFRAMES = ("1d", "4h", "2h", "1h")
_ST_REACTION_TIMEFRAMES = ("4h", "2h", "1h", "30m")


@dataclass(frozen=True, slots=True)
class DecisionEngineConfig:
    """V1 policy/calibration inputs; no hidden market thresholds."""

    opportunity_calibration: OpportunityCalibration | None = None
    action_policy: ActionPolicy = field(default_factory=ActionPolicy)
    execution_timeframe: str = "30m"

    def __post_init__(self) -> None:
        if self.execution_timeframe.strip().lower() != "30m":
            raise ValueError("v1 execution timeframe is architecturally fixed to 30m")


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


def _timeframe_policy(horizon: DecisionHorizon) -> tuple[tuple[str, ...], str, str, str]:
    if horizon is DecisionHorizon.LONG_TERM:
        # LT Structure remains 1D-owned. 4H describes supporting participation/regime,
        # while 1H is the immediate LT setup-timing context. 30m remains execution.
        return _LT_REACTION_TIMEFRAMES, "4h", "4h", "1h"
    # ST Structure remains 1H-owned. 30m is setup timing / trigger context.
    return _ST_REACTION_TIMEFRAMES, "1h", "1h", "30m"


def _permission_policy(horizon: DecisionHorizon) -> tuple[str, tuple[str, ...]]:
    """Return the structural anchor and subordinate context TFs for permission.

    Permission is derived cheaply from already-frozen read models. It must follow the
    horizon's actual structural authority rather than reusing the workspace's generic
    4H context anchor for both LT and ST decisions.
    """

    if horizon is DecisionHorizon.LONG_TERM:
        return "1d", ("4h", "2h", "1h")
    return "1h", ("30m",)


def _horizon_permission(
    snapshot: DecisionInputSnapshot,
    horizon: DecisionHorizon,
) -> PermissionEnvelope:
    anchor_timeframe, trigger_timeframes = _permission_policy(horizon)
    axes = evaluate_context_axes(
        structural=snapshot.structure,
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
        return projection.for_timeframe(timeframe).ref.data_quality
    except KeyError:
        return ContextDataQuality.UNAVAILABLE


def _ham_quality(snapshot: DecisionInputSnapshot, timeframe: str) -> ContextDataQuality:
    if snapshot.ham is None:
        return ContextDataQuality.UNAVAILABLE
    normalized = timeframe.strip().lower()
    for row in snapshot.ham.timeframe_facts:
        if row.timeframe == normalized:
            return row.data_quality
    return ContextDataQuality.UNAVAILABLE


def _liquidity_quality(snapshot: DecisionInputSnapshot, timeframe: str) -> ContextDataQuality:
    projection = snapshot.liquidity_landscape
    if projection is None:
        return ContextDataQuality.UNAVAILABLE
    try:
        return projection.for_timeframe(timeframe).ref.data_quality
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


def assess_horizon_decision(
    snapshot: DecisionInputSnapshot,
    horizon: DecisionHorizon,
    *,
    config: DecisionEngineConfig | None = None,
    execution_event: ExecutionTriggerEvent | None = None,
) -> HorizonDecisionAssessment:
    """Build one fully typed LT or ST v1 decision assessment.

    The engine does not search history or infer fresh execution edges from sticky
    snapshots. A BUY/SELL can occur only when ``execution_event`` is supplied as a
    causal event for the current ``as_of``. Without it, an otherwise eligible path
    stops at READY.
    """

    cfg = config or DecisionEngineConfig()
    structural_snapshot = build_horizon_structural_snapshot(snapshot.structure)
    structural = (
        structural_snapshot.long_term
        if horizon is DecisionHorizon.LONG_TERM
        else structural_snapshot.short_term
    )
    reaction_timeframes, participation_tf, environment_tf, timing_tf = _timeframe_policy(horizon)
    permission = _horizon_permission(snapshot, horizon)

    durability = assess_durability(snapshot.stabil_support)
    reaction = assess_reaction(
        structural.direction,
        order_blocks=snapshot.order_block_behavior,
        fvg_engulfing=snapshot.fvg_engulfing_lifecycle,
        timeframes=reaction_timeframes,
    )
    timing_reaction = assess_reaction(
        structural.direction,
        order_blocks=snapshot.order_block_behavior,
        fvg_engulfing=snapshot.fvg_engulfing_lifecycle,
        timeframes=(timing_tf,),
    )
    participation = assess_participation(
        structural.direction,
        snapshot.participation_behavior,
        timeframe=participation_tf,
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
    )
    execution = assess_execution_trigger(
        structural.direction,
        as_of=snapshot.as_of,
        timeframe=cfg.execution_timeframe,
        data_quality=snapshot.quality_for_timeframe(cfg.execution_timeframe),
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
    )


__all__ = [
    "DecisionEngineConfig",
    "HorizonDecisionAssessment",
    "assess_horizon_decision",
]
