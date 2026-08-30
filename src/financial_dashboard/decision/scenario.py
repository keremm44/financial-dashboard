from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from financial_dashboard.context.envelope import ContextDataQuality

from .durability import DurabilityState
from .eligibility import EligibilityState
from .market_state import HorizonMarketState, StructuralRegime
from .opportunity import OpportunityState
from .structural import DecisionHorizon, HorizonRelation, StructuralDirection, ThesisState
from .target_path import TargetPath, TargetPathNodeState, TargetPathStatus

if TYPE_CHECKING:
    from financial_dashboard.decision_input import DecisionInputSnapshot
    from .engine import DecisionEngineConfig, HorizonDecisionAssessment


class ScenarioPresence(StrEnum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"


class ScenarioStage(StrEnum):
    QUALIFIED = "QUALIFIED"
    DEVELOPING = "DEVELOPING"
    BLOCKED = "BLOCKED"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ScenarioKind(StrEnum):
    CONTINUATION = "CONTINUATION"
    PULLBACK_CONTINUATION = "PULLBACK_CONTINUATION"
    TRANSITION_CONTEXT = "TRANSITION_CONTEXT"
    EARLY_TRANSITION = "EARLY_TRANSITION"
    SHORT_TERM_STANDALONE = "SHORT_TERM_STANDALONE"
    NONE = "NONE"


class ScenarioUnknownReason(StrEnum):
    NONE = "NONE"
    WARMUP = "WARMUP"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    STRUCTURE_UNRESOLVED = "STRUCTURE_UNRESOLVED"
    OPPORTUNITY_UNOBSERVED = "OPPORTUNITY_UNOBSERVED"


@dataclass(frozen=True, slots=True)
class EntryScenarioAssessment:
    horizon: DecisionHorizon
    presence: ScenarioPresence
    stage: ScenarioStage
    kind: ScenarioKind
    structural_direction: StructuralDirection
    thesis_state: ThesisState
    structural_regime: StructuralRegime
    opportunity_state: OpportunityState
    target_path_status: TargetPathStatus
    active_target_identity: str | None
    eligibility_state: EligibilityState
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    waiting_for: tuple[str, ...]
    source_lineage: tuple[str, ...]
    unknown_reason: ScenarioUnknownReason = ScenarioUnknownReason.NONE

    def __post_init__(self) -> None:
        if self.presence is not ScenarioPresence.UNKNOWN:
            return
        if self.unknown_reason is not ScenarioUnknownReason.NONE:
            return
        tokens = {str(reason).upper() for reason in self.reasons}
        inferred = ScenarioUnknownReason.STRUCTURE_UNRESOLVED
        if any("WARM" in token for token in tokens):
            inferred = ScenarioUnknownReason.WARMUP
        elif any("STRUCTURE_DATA_" in token for token in tokens):
            inferred = ScenarioUnknownReason.DATA_UNAVAILABLE
        elif "NO_OBSERVED_OPPORTUNITY_DOES_NOT_PROVE_ABSENCE" in tokens:
            inferred = ScenarioUnknownReason.OPPORTUNITY_UNOBSERVED
        object.__setattr__(self, "unknown_reason", inferred)

    @property
    def owns_horizon(self) -> bool:
        return self.presence is ScenarioPresence.PRESENT


def _lineage_from_assessment(assessment: "HorizonDecisionAssessment") -> set[str]:
    values = set(assessment.opportunity.source_lineage)
    for ref in assessment.structural.source_refs:
        values.add(ref.lineage_id or f"{ref.domain.value}:{ref.timeframe}:{ref.native_id}")
    return values


def _lineage_from_path(path: TargetPath) -> set[str]:
    values: set[str] = set()
    for node in path.nodes:
        values.update(node.lineage_ids)
        for ref in node.source_refs:
            values.add(ref.lineage_id or f"{ref.domain.value}:{ref.timeframe}:{ref.native_id}")
    return values


def _scenario_kind(assessment: "HorizonDecisionAssessment") -> ScenarioKind:
    relation = assessment.structural_snapshot.relation
    horizon = assessment.horizon
    transition = getattr(assessment, "st_transition", None)
    if (
        horizon is DecisionHorizon.SHORT_TERM
        and transition is not None
        and bool(getattr(transition, "can_own_trade_thesis", False))
    ):
        return ScenarioKind.EARLY_TRANSITION
    if assessment.structural.thesis_state is ThesisState.TRANSITIONING:
        return ScenarioKind.TRANSITION_CONTEXT
    if horizon is DecisionHorizon.LONG_TERM:
        if relation in {HorizonRelation.PULLBACK, HorizonRelation.COUNTER_REACTION}:
            return ScenarioKind.PULLBACK_CONTINUATION
        return ScenarioKind.CONTINUATION
    long_term = assessment.structural_snapshot.long_term
    if long_term.direction is StructuralDirection.LONG:
        return ScenarioKind.CONTINUATION
    return ScenarioKind.SHORT_TERM_STANDALONE


def _observed_opportunity(assessment: "HorizonDecisionAssessment", path: TargetPath) -> bool:
    opportunity = assessment.opportunity
    # NONE is an observed economic assessment: the engine found directional room
    # and judged it insufficient. It must not be confused with UNKNOWN/unobserved.
    if opportunity.state in {
        OpportunityState.AMPLE,
        OpportunityState.MODERATE,
        OpportunityState.COMPRESSED,
        OpportunityState.NONE,
    }:
        return True
    if opportunity.room_atr is not None or opportunity.target_identity is not None:
        return True
    return path.status is TargetPathStatus.READY and bool(path.nodes)


def build_entry_scenario(
    assessment: "HorizonDecisionAssessment",
    *,
    target_path: TargetPath,
    market_state: HorizonMarketState,
) -> EntryScenarioAssessment:
    structural = assessment.structural
    opportunity = assessment.opportunity
    eligibility = assessment.eligibility
    lineage = tuple(sorted(_lineage_from_assessment(assessment) | _lineage_from_path(target_path)))

    if structural.data_quality is not ContextDataQuality.VALID:
        reason = (
            ScenarioUnknownReason.WARMUP
            if structural.data_quality is ContextDataQuality.WARMING_UP
            else ScenarioUnknownReason.DATA_UNAVAILABLE
        )
        return EntryScenarioAssessment(
            assessment.horizon, ScenarioPresence.UNKNOWN, ScenarioStage.UNAVAILABLE,
            ScenarioKind.NONE, structural.direction, structural.thesis_state,
            market_state.structural_map.structural_regime, opportunity.state,
            target_path.status, None if target_path.active_node is None else target_path.active_node.identity,
            eligibility.state, (f"STRUCTURE_DATA_{structural.data_quality.value}",), (),
            ("VALID_STRUCTURAL_AUTHORITY",), lineage, reason,
        )

    if structural.direction is StructuralDirection.UNRESOLVED or structural.thesis_state is ThesisState.UNRESOLVED:
        return EntryScenarioAssessment(
            assessment.horizon, ScenarioPresence.UNKNOWN, ScenarioStage.UNAVAILABLE,
            ScenarioKind.NONE, structural.direction, structural.thesis_state,
            market_state.structural_map.structural_regime, opportunity.state,
            target_path.status, None if target_path.active_node is None else target_path.active_node.identity,
            eligibility.state, ("STRUCTURAL_LONG_ENTRY_STATE_UNRESOLVED",), (),
            ("STRUCTURAL_DIRECTION_TO_RESOLVE",), lineage,
            ScenarioUnknownReason.STRUCTURE_UNRESOLVED,
        )

    if structural.direction is StructuralDirection.SHORT:
        return EntryScenarioAssessment(
            assessment.horizon, ScenarioPresence.ABSENT, ScenarioStage.NOT_APPLICABLE,
            ScenarioKind.NONE, structural.direction, structural.thesis_state,
            market_state.structural_map.structural_regime, opportunity.state,
            target_path.status, None, eligibility.state,
            ("LONG_ENTRY_REQUIRES_LONG_STRUCTURE",), (), (), lineage,
        )

    if structural.thesis_state is ThesisState.INVALIDATED:
        return EntryScenarioAssessment(
            assessment.horizon, ScenarioPresence.ABSENT, ScenarioStage.NOT_APPLICABLE,
            ScenarioKind.NONE, structural.direction, structural.thesis_state,
            market_state.structural_map.structural_regime, opportunity.state,
            target_path.status, None, eligibility.state,
            ("STRUCTURAL_LONG_THESIS_INVALIDATED",), (), (), lineage,
        )

    if not _observed_opportunity(assessment, target_path):
        return EntryScenarioAssessment(
            assessment.horizon, ScenarioPresence.UNKNOWN, ScenarioStage.UNAVAILABLE,
            ScenarioKind.NONE, structural.direction, structural.thesis_state,
            market_state.structural_map.structural_regime, opportunity.state,
            target_path.status, None, eligibility.state,
            ("NO_OBSERVED_OPPORTUNITY_DOES_NOT_PROVE_ABSENCE",), (),
            ("OBSERVED_DIRECTIONAL_OPPORTUNITY",), lineage,
            ScenarioUnknownReason.OPPORTUNITY_UNOBSERVED,
        )

    kind = _scenario_kind(assessment)
    reasons: list[str] = ["OBSERVED_LONG_ENTRY_SCENARIO"]
    if kind is ScenarioKind.EARLY_TRANSITION:
        reasons.append("SHORT_TERM_EARLY_TRANSITION_TRADE_THESIS")
    blockers = list(eligibility.blockers)
    waiting = list(eligibility.waiting_for)
    active = target_path.active_node
    stabil_hard_block = False

    # Stabil is now part of the ST thesis, but only severe damage can prevent a new
    # long.  SOFTENING remains observable context rather than a veto so healthy
    # pullbacks and the existing early-entry positive controls are preserved.
    durability = getattr(assessment, "durability", None)
    if assessment.horizon is DecisionHorizon.SHORT_TERM and durability is not None:
        if durability.state is DurabilityState.BROKEN:
            stabil_hard_block = True
            blockers.append("STABIL_FOUNDATION_BROKEN_FOR_NEW_ST_LONG")
            reasons.append("STABIL_SEVERE_BEARISH_AUTHORITY")
        elif durability.state is DurabilityState.FRACTURED:
            waiting.append("STABIL_FOUNDATION_TO_RECOVER")
            reasons.append("STABIL_BREAKDOWN_NOT_YET_RECOVERED")

    # Opportunity describes economics/path quality; it does not decide whether the
    # structural long scenario exists.  A true hard economic NONE prevents
    # qualification; a reaction-only technical cluster remains visible but soft.
    if opportunity.state is OpportunityState.NONE:
        if bool(getattr(opportunity, "hard_room_constraint", True)):
            reasons.append("OBSERVED_DIRECTIONAL_ROOM_INSUFFICIENT")
            waiting.append("MORE_DIRECTIONAL_ROOM")
        else:
            reasons.append("SOFT_TECHNICAL_ROOM_CONSTRAINT")

    if target_path.status is not TargetPathStatus.READY:
        waiting.append("TARGET_PATH_TO_RESOLVE")
    elif active is not None and active.state is TargetPathNodeState.DEFENDED:
        waiting.append("ACTIVE_TARGET_PATH_NODE_DEFENDED")
        reasons.append("TARGET_PATH_DEFENSE_REQUIRES_REASSESSMENT")

    if structural.thesis_state is ThesisState.TRANSITIONING:
        waiting.append("STRUCTURAL_TRANSITION_TO_RESOLVE")
        reasons.append("EXISTING_LONG_SCENARIO_IN_TRANSITION")

    if eligibility.state is EligibilityState.BLOCKED or stabil_hard_block:
        stage = ScenarioStage.BLOCKED
    elif eligibility.state is EligibilityState.WAITING or waiting:
        stage = ScenarioStage.DEVELOPING
    else:
        stage = ScenarioStage.QUALIFIED

    return EntryScenarioAssessment(
        assessment.horizon, ScenarioPresence.PRESENT, stage, kind,
        structural.direction, structural.thesis_state,
        market_state.structural_map.structural_regime, opportunity.state,
        target_path.status, None if active is None else active.identity,
        eligibility.state, tuple(dict.fromkeys(reasons)), tuple(dict.fromkeys(blockers)),
        tuple(dict.fromkeys(waiting)), lineage,
    )


def assess_entry_scenario(
    snapshot: "DecisionInputSnapshot",
    horizon: DecisionHorizon,
    *,
    config: "DecisionEngineConfig | None" = None,
    assessment: "HorizonDecisionAssessment | None" = None,
) -> EntryScenarioAssessment:
    from .engine import assess_horizon_decision

    if assessment is None:
        assessment = assess_horizon_decision(snapshot, horizon, config=config, execution_event=None)
    elif assessment.horizon is not horizon:
        raise ValueError("injected assessment horizon must match the requested horizon")

    market = snapshot.market_state
    horizon_market = market.long_term if horizon is DecisionHorizon.LONG_TERM else market.short_term
    path = snapshot.target_path(assessment.structural.direction)
    return build_entry_scenario(assessment, target_path=path, market_state=horizon_market)


__all__ = [
    "EntryScenarioAssessment",
    "ScenarioKind",
    "ScenarioPresence",
    "ScenarioStage",
    "ScenarioUnknownReason",
    "assess_entry_scenario",
    "build_entry_scenario",
]
