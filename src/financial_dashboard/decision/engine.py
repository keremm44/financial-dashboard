from __future__ import annotations

from dataclasses import dataclass, field, replace

from financial_dashboard.context.axes import evaluate_context_axes
from financial_dashboard.context.envelope import ContextDataQuality, FactRef
from financial_dashboard.context.permissions import PermissionEnvelope, resolve_permission_axes
from financial_dashboard.context.projections import StructuralFactsProjection
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
from .horizon_profile import horizon_evaluation_profile
from .opportunity import OpportunityAssessment, OpportunityCalibration, assess_opportunity
from .participation import ParticipationAssessment, assess_participation
from .reaction import ReactionAssessment, assess_reaction
from .stabil_interpretation import StabilHorizonAssessment, assess_stabil_horizon
from .structural import (
    DecisionHorizon,
    HorizonStructuralSnapshot,
    StructuralAssessment,
    build_horizon_structural_snapshot,
)
from .timing import TimingAssessment, assess_timing


DECISION_CONTRACT_VERSION = 1


@dataclass(frozen=True, slots=True)
class DecisionEngineConfig:
    """V1 policy/calibration inputs; no hidden market thresholds."""

    opportunity_calibration: OpportunityCalibration | None = None
    action_policy: ActionPolicy = field(default_factory=ActionPolicy)
    execution_timeframe: str = field(
        default_factory=lambda: horizon_evaluation_profile(DecisionHorizon.LONG_TERM).execution_timeframe
    )
    decision_contract_version: int = field(default=DECISION_CONTRACT_VERSION, init=False)

    def __post_init__(self) -> None:
        lt_execution = horizon_evaluation_profile(DecisionHorizon.LONG_TERM).execution_timeframe
        st_execution = horizon_evaluation_profile(DecisionHorizon.SHORT_TERM).execution_timeframe
        normalized = self.execution_timeframe.strip().lower()
        if lt_execution != st_execution or normalized != lt_execution:
            raise ValueError("v1 execution timeframe is architecturally fixed to 30m")


@dataclass(frozen=True, slots=True)
class PreparedHorizonAssessment:
    """Execution-independent LT/ST market and eligibility assessment.

    This is the single prepared result shared by scenario arbitration and the final
    execution step. It intentionally stops before execution/final action composition
    so a selected horizon never has to rebuild Structure through Eligibility.
    """

    horizon: DecisionHorizon
    as_of: object
    config: DecisionEngineConfig
    structural_snapshot: HorizonStructuralSnapshot
    structural: StructuralAssessment
    permission: PermissionEnvelope
    durability: DurabilityAssessment
    stabil: StabilHorizonAssessment
    reaction: ReactionAssessment
    timing_reaction: ReactionAssessment
    participation: ParticipationAssessment
    environment: EnvironmentAssessment
    opportunity: OpportunityAssessment
    coverage: CoverageAssessment
    conflict: ConflictAssessment
    timing: TimingAssessment
    eligibility: EligibilityAssessment


@dataclass(frozen=True, slots=True)
class HorizonDecisionAssessment:
    horizon: DecisionHorizon
    as_of: object
    structural_snapshot: HorizonStructuralSnapshot
    structural: StructuralAssessment
    permission: PermissionEnvelope
    durability: DurabilityAssessment
    stabil: StabilHorizonAssessment
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
    profile = horizon_evaluation_profile(horizon)
    return (
        profile.reaction_timeframes,
        profile.participation_timeframe,
        profile.environment_timeframe,
        profile.timing_timeframe,
    )


def _permission_policy(horizon: DecisionHorizon) -> tuple[str, tuple[str, ...]]:
    """Return the structural anchor and subordinate context TFs for permission."""

    profile = horizon_evaluation_profile(horizon)
    return profile.permission_anchor_timeframe, profile.permission_context_timeframes


def _decision_structure_projection(structural):
    """Normalize price-only Structure quality for Decision without mutating source diagnostics.

    Generic OHLCV quality marks a batch DATA_LIMITED for warnings such as zero volume
    or an open/incomplete source tail. Market Structure is price-only and its replay
    already excludes unsafe candles, so those generic limitations must not erase 1D/1H
    structural authority inside the Decision layer. Other domain projections retain
    their native quality unchanged.

    Some unit tests intentionally pass opaque pipeline doubles while monkeypatching the
    downstream structural builder. Preserve those doubles unchanged; production calls
    always provide ``StructuralFactsProjection``.
    """

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


def prepare_horizon_assessment(
    snapshot: DecisionInputSnapshot,
    horizon: DecisionHorizon,
    *,
    config: DecisionEngineConfig | None = None,
) -> PreparedHorizonAssessment:
    """Build Structure-through-Eligibility exactly once for one frozen horizon."""

    cfg = config or DecisionEngineConfig()
    decision_structure = _decision_structure_projection(snapshot.structure)
    structural_snapshot = build_horizon_structural_snapshot(decision_structure)
    structural = (
        structural_snapshot.long_term
        if horizon is DecisionHorizon.LONG_TERM
        else structural_snapshot.short_term
    )
    reaction_timeframes, participation_tf, environment_tf, timing_tf = _timeframe_policy(horizon)
    permission = _horizon_permission(snapshot, horizon)

    durability = assess_durability(snapshot.stabil_support)
    factual_market_state = getattr(snapshot, "market_state", None)
    stabil = assess_stabil_horizon(
        None if factual_market_state is None else factual_market_state.stabil,
        horizon,
    )
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

    return PreparedHorizonAssessment(
        horizon=horizon,
        as_of=snapshot.as_of,
        config=cfg,
        structural_snapshot=structural_snapshot,
        structural=structural,
        permission=permission,
        durability=durability,
        stabil=stabil,
        reaction=reaction,
        timing_reaction=timing_reaction,
        participation=participation,
        environment=environment,
        opportunity=opportunity,
        coverage=coverage,
        conflict=conflict,
        timing=timing,
        eligibility=eligibility,
    )


def finalize_horizon_assessment(
    snapshot: DecisionInputSnapshot,
    prepared: PreparedHorizonAssessment,
    *,
    execution_event: ExecutionTriggerEvent | None = None,
) -> HorizonDecisionAssessment:
    """Apply only the fresh execution event and final composer to a prepared horizon."""

    if prepared.as_of != snapshot.as_of:
        raise ValueError("prepared horizon assessment must share snapshot as_of")

    cfg = prepared.config
    execution = assess_execution_trigger(
        prepared.structural.direction,
        as_of=snapshot.as_of,
        timeframe=cfg.execution_timeframe,
        data_quality=snapshot.quality_for_timeframe(cfg.execution_timeframe),
        event=execution_event,
    )
    final = compose_final_decision(
        prepared.structural,
        eligibility=prepared.eligibility,
        execution=execution,
        policy=cfg.action_policy,
        additional_lineage=_additional_lineage(
            durability=prepared.durability,
            reaction=prepared.reaction,
            participation=prepared.participation,
            environment=prepared.environment,
            timing=prepared.timing,
            opportunity=prepared.opportunity,
            conflict=prepared.conflict,
        ),
    )

    return HorizonDecisionAssessment(
        horizon=prepared.horizon,
        as_of=prepared.as_of,
        structural_snapshot=prepared.structural_snapshot,
        structural=prepared.structural,
        permission=prepared.permission,
        durability=prepared.durability,
        stabil=prepared.stabil,
        reaction=prepared.reaction,
        timing_reaction=prepared.timing_reaction,
        participation=prepared.participation,
        environment=prepared.environment,
        opportunity=prepared.opportunity,
        coverage=prepared.coverage,
        conflict=prepared.conflict,
        timing=prepared.timing,
        execution=execution,
        eligibility=prepared.eligibility,
        final=final,
    )


def assess_horizon_decision(
    snapshot: DecisionInputSnapshot,
    horizon: DecisionHorizon,
    *,
    config: DecisionEngineConfig | None = None,
    execution_event: ExecutionTriggerEvent | None = None,
) -> HorizonDecisionAssessment:
    """Compatibility composition of prepare + execution/finalization.

    The wrapper preserves the existing public API and all callers that need one full
    assessment. Scenario/entry orchestration can share the prepared assessment to
    avoid rebuilding the market/policy chain after arbitration.
    """

    prepared = prepare_horizon_assessment(snapshot, horizon, config=config)
    return finalize_horizon_assessment(
        snapshot,
        prepared,
        execution_event=execution_event,
    )


__all__ = [
    "DECISION_CONTRACT_VERSION",
    "DecisionEngineConfig",
    "HorizonDecisionAssessment",
    "PreparedHorizonAssessment",
    "assess_horizon_decision",
    "finalize_horizon_assessment",
    "prepare_horizon_assessment",
]
