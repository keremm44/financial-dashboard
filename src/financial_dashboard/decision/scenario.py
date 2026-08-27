from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from financial_dashboard.context.envelope import ContextDataQuality

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
    SHORT_TERM_STANDALONE = "SHORT_TERM_STANDALONE"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class EntryScenarioAssessment:
    """Non-action description of one horizon's long-entry opportunity.

    Presence answers whether an observed long-entry scenario exists. Stage answers
    whether that scenario is currently qualified, developing, or blocked. Keeping
    those questions separate is important for later arbitration: a blocked LT
    scenario still exists and therefore cannot be silently bypassed by an ST setup.
    """

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

    @property
    def owns_horizon(self) -> bool:
        """True when this horizon has a real observed scenario, even if blocked."""

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
    if opportunity.state in {
        OpportunityState.AMPLE,
        OpportunityState.MODERATE,
        OpportunityState.COMPRESSED,
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
    """Classify one horizon without emitting READY/BUY/SELL.

    Direction remains Structure-owned. This layer is intentionally long-entry only:
    a valid SHORT structural assessment is preserved analytically but means that no
    long-entry scenario exists for that horizon. UNKNOWN never means ABSENT.
    """

    structural = assessment.structural
    opportunity = assessment.opportunity
    eligibility = assessment.eligibility
    lineage = tuple(sorted(_lineage_from_assessment(assessment) | _lineage_from_path(target_path)))

    if structural.data_quality is not ContextDataQuality.VALID:
        return EntryScenarioAssessment(
            assessment.horizon,
            ScenarioPresence.UNKNOWN,
            ScenarioStage.UNAVAILABLE,
            ScenarioKind.NONE,
            structural.direction,
            structural.thesis_state,
            market_state.structural_map.structural_regime,
            opportunity.state,
            target_path.status,
            None if target_path.active_node is None else target_path.active_node.identity,
            eligibility.state,
            (f"STRUCTURE_DATA_{structural.data_quality.value}",),
            (),
            ("VALID_STRUCTURAL_AUTHORITY",),
            lineage,
        )

    if structural.direction is StructuralDirection.UNRESOLVED or structural.thesis_state is ThesisState.UNRESOLVED:
        return EntryScenarioAssessment(
            assessment.horizon,
            ScenarioPresence.UNKNOWN,
            ScenarioStage.UNAVAILABLE,
            ScenarioKind.NONE,
            structural.direction,
            structural.thesis_state,
            market_state.structural_map.structural_regime,
            opportunity.state,
            target_path.status,
            None if target_path.active_node is None else target_path.active_node.identity,
            eligibility.state,
            ("STRUCTURAL_LONG_ENTRY_STATE_UNRESOLVED",),
            (),
            ("STRUCTURAL_DIRECTION_TO_RESOLVE",),
            lineage,
        )

    # The current product is long-only. A canonical SHORT thesis is analytically
    # valid, but it is not a long-entry scenario and must not be converted to BUY.
    if structural.direction is StructuralDirection.SHORT:
        return EntryScenarioAssessment(
            assessment.horizon,
            ScenarioPresence.ABSENT,
            ScenarioStage.NOT_APPLICABLE,
            ScenarioKind.NONE,
            structural.direction,
            structural.thesis_state,
            market_state.structural_map.structural_regime,
            opportunity.state,
            target_path.status,
            None,
            eligibility.state,
            ("LONG_ENTRY_REQUIRES_LONG_STRUCTURE",),
            (),
            (),
            lineage,
        )

    if structural.thesis_state is ThesisState.INVALIDATED:
        return EntryScenarioAssessment(
            assessment.horizon,
            ScenarioPresence.ABSENT,
            ScenarioStage.NOT_APPLICABLE,
            ScenarioKind.NONE,
            structural.direction,
            structural.thesis_state,
            market_state.structural_map.structural_regime,
            opportunity.state,
            target_path.status,
            None,
            eligibility.state,
            ("STRUCTURAL_LONG_THESIS_INVALIDATED",),
            (),
            (),
            lineage,
        )

    if opportunity.state is OpportunityState.NONE:
        return EntryScenarioAssessment(
            assessment.horizon,
            ScenarioPresence.ABSENT,
            ScenarioStage.NOT_APPLICABLE,
            ScenarioKind.NONE,
            structural.direction,
            structural.thesis_state,
            market_state.structural_map.structural_regime,
            opportunity.state,
            target_path.status,
            None if target_path.active_node is None else target_path.active_node.identity,
            eligibility.state,
            ("OBSERVED_DIRECTIONAL_ROOM_INSUFFICIENT",),
            (),
            (),
            lineage,
        )

    if not _observed_opportunity(assessment, target_path):
        return EntryScenarioAssessment(
            assessment.horizon,
            ScenarioPresence.UNKNOWN,
            ScenarioStage.UNAVAILABLE,
            ScenarioKind.NONE,
            structural.direction,
            structural.thesis_state,
            market_state.structural_map.structural_regime,
            opportunity.state,
            target_path.status,
            None,
            eligibility.state,
            ("NO_OBSERVED_OPPORTUNITY_DOES_NOT_PROVE_ABSENCE",),
            (),
            ("OBSERVED_DIRECTIONAL_OPPORTUNITY",),
            lineage,
        )

    reasons: list[str] = ["OBSERVED_LONG_ENTRY_SCENARIO"]
    blockers = list(eligibility.blockers)
    waiting = list(eligibility.waiting_for)
    kind = _scenario_kind(assessment)

    active = target_path.active_node
    if target_path.status is not TargetPathStatus.READY:
        waiting.append("TARGET_PATH_TO_RESOLVE")
    elif active is not None and active.state is TargetPathNodeState.DEFENDED:
        waiting.append("ACTIVE_TARGET_PATH_NODE_DEFENDED")
        reasons.append("TARGET_PATH_DEFENSE_REQUIRES_REASSESSMENT")

    if structural.thesis_state is ThesisState.TRANSITIONING:
        waiting.append("STRUCTURAL_TRANSITION_TO_RESOLVE")
        reasons.append("EXISTING_LONG_SCENARIO_IN_TRANSITION")

    if eligibility.state is EligibilityState.BLOCKED:
        stage = ScenarioStage.BLOCKED
    elif eligibility.state is EligibilityState.WAITING or waiting:
        stage = ScenarioStage.DEVELOPING
    else:
        stage = ScenarioStage.QUALIFIED

    return EntryScenarioAssessment(
        assessment.horizon,
        ScenarioPresence.PRESENT,
        stage,
        kind,
        structural.direction,
        structural.thesis_state,
        market_state.structural_map.structural_regime,
        opportunity.state,
        target_path.status,
        None if active is None else active.identity,
        eligibility.state,
        tuple(dict.fromkeys(reasons)),
        tuple(dict.fromkeys(blockers)),
        tuple(dict.fromkeys(waiting)),
        lineage,
    )


def assess_entry_scenario(
    snapshot: "DecisionInputSnapshot",
    horizon: DecisionHorizon,
    *,
    config: "DecisionEngineConfig | None" = None,
) -> EntryScenarioAssessment:
    """Build the causal non-action scenario directly from one frozen input snapshot."""

    # Local import avoids making the existing horizon engine depend on this layer.
    from .engine import assess_horizon_decision

    assessment = assess_horizon_decision(snapshot, horizon, config=config, execution_event=None)
    market = snapshot.market_state
    horizon_market = market.long_term if horizon is DecisionHorizon.LONG_TERM else market.short_term
    path = snapshot.target_path(assessment.structural.direction)
    return build_entry_scenario(assessment, target_path=path, market_state=horizon_market)


__all__ = [
    "EntryScenarioAssessment",
    "ScenarioKind",
    "ScenarioPresence",
    "ScenarioStage",
    "assess_entry_scenario",
    "build_entry_scenario",
]
