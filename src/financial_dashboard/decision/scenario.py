from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from financial_dashboard.context.envelope import ContextDataQuality

from .eligibility import EligibilityAssessment, EligibilityState
from .entry_qualification import (
    EntryQualificationAssessment,
    ScenarioStage,
    assess_entry_qualification,
)
from .market_state import HorizonMarketState, StructuralRegime
from .opportunity import OpportunityState
from .structural import DecisionHorizon, HorizonRelation, StructuralDirection, ThesisState
from .target_path import TargetPath, TargetPathStatus

if TYPE_CHECKING:
    from financial_dashboard.decision_input import DecisionInputSnapshot
    from .engine import DecisionEngineConfig, HorizonDecisionAssessment, PreparedHorizonAssessment


class ScenarioPresence(StrEnum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"


class ScenarioKind(StrEnum):
    CONTINUATION = "CONTINUATION"
    PULLBACK_CONTINUATION = "PULLBACK_CONTINUATION"
    TRANSITION_CONTEXT = "TRANSITION_CONTEXT"
    SHORT_TERM_STANDALONE = "SHORT_TERM_STANDALONE"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class EntryScenarioAssessment:
    """Non-action description of one horizon's observed long-entry scenario.

    Scenario owns presence/kind and descriptive context only. Eligibility remains the
    canonical owner of technical blockers/waits; EntryQualificationAssessment adds
    only TargetPath maturity and derives the public stage for an observed scenario.
    Compatibility properties expose the existing stage/gate view without storing a
    second copy on Scenario.
    """

    horizon: DecisionHorizon
    presence: ScenarioPresence
    kind: ScenarioKind
    structural_direction: StructuralDirection
    thesis_state: ThesisState
    structural_regime: StructuralRegime
    opportunity_state: OpportunityState
    target_path_status: TargetPathStatus
    active_target_identity: str | None
    eligibility: EligibilityAssessment
    qualification: EntryQualificationAssessment | None
    reasons: tuple[str, ...]
    presence_waiting_for: tuple[str, ...]
    source_lineage: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.presence is ScenarioPresence.PRESENT and self.qualification is None:
            raise ValueError("present scenario requires entry qualification")
        if self.presence is not ScenarioPresence.PRESENT and self.qualification is not None:
            raise ValueError("non-present scenario cannot carry entry qualification")

    @property
    def stage(self) -> ScenarioStage:
        """Compatibility view; qualification owns PRESENT scenario stage."""

        if self.qualification is not None:
            return self.qualification.state
        if self.presence is ScenarioPresence.UNKNOWN:
            return ScenarioStage.UNAVAILABLE
        return ScenarioStage.NOT_APPLICABLE

    @property
    def eligibility_state(self) -> EligibilityState:
        """Compatibility view over the canonical Eligibility assessment."""

        return self.eligibility.state

    @property
    def blockers(self) -> tuple[str, ...]:
        """Expose canonical Eligibility blockers only for an observed scenario."""

        if self.qualification is None:
            return ()
        return self.qualification.blockers

    @property
    def waiting_for(self) -> tuple[str, ...]:
        """Expose qualification waits or unresolved-presence evidence requirements."""

        if self.qualification is not None:
            return self.qualification.waiting_for
        return self.presence_waiting_for

    @property
    def owns_horizon(self) -> bool:
        """True when this horizon has a real observed scenario, even if blocked."""

        return self.presence is ScenarioPresence.PRESENT


@dataclass(frozen=True, slots=True)
class PreparedEntryScenario:
    """Internal scenario plus the exact execution-independent assessment that built it."""

    scenario: EntryScenarioAssessment
    assessment: "PreparedHorizonAssessment"


def _lineage_from_assessment(
    assessment: "HorizonDecisionAssessment | PreparedHorizonAssessment",
) -> set[str]:
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


def _scenario_kind(
    assessment: "HorizonDecisionAssessment | PreparedHorizonAssessment",
) -> ScenarioKind:
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


def _observed_opportunity(
    assessment: "HorizonDecisionAssessment | PreparedHorizonAssessment",
    path: TargetPath,
) -> bool:
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


def _scenario(
    assessment: "HorizonDecisionAssessment | PreparedHorizonAssessment",
    *,
    market_state: HorizonMarketState,
    target_path: TargetPath,
    presence: ScenarioPresence,
    kind: ScenarioKind,
    active_target_identity: str | None,
    reasons: tuple[str, ...],
    presence_waiting_for: tuple[str, ...] = (),
    qualification: EntryQualificationAssessment | None = None,
    lineage: tuple[str, ...],
) -> EntryScenarioAssessment:
    return EntryScenarioAssessment(
        horizon=assessment.horizon,
        presence=presence,
        kind=kind,
        structural_direction=assessment.structural.direction,
        thesis_state=assessment.structural.thesis_state,
        structural_regime=market_state.structural_map.structural_regime,
        opportunity_state=assessment.opportunity.state,
        target_path_status=target_path.status,
        active_target_identity=active_target_identity,
        eligibility=assessment.eligibility,
        qualification=qualification,
        reasons=reasons,
        presence_waiting_for=presence_waiting_for,
        source_lineage=lineage,
    )


def build_entry_scenario(
    assessment: "HorizonDecisionAssessment | PreparedHorizonAssessment",
    *,
    target_path: TargetPath,
    market_state: HorizonMarketState,
) -> EntryScenarioAssessment:
    """Classify one horizon without emitting READY/BUY/SELL.

    Direction remains Structure-owned. Scenario decides only whether a long-entry
    scenario is observed and what kind it is. Technical gate state is referenced from
    Eligibility, while TargetPath maturity is composed by EntryQualificationAssessment.
    UNKNOWN never means ABSENT.
    """

    structural = assessment.structural
    opportunity = assessment.opportunity
    lineage = tuple(
        sorted(_lineage_from_assessment(assessment) | _lineage_from_path(target_path))
    )

    if structural.data_quality is not ContextDataQuality.VALID:
        return _scenario(
            assessment,
            market_state=market_state,
            target_path=target_path,
            presence=ScenarioPresence.UNKNOWN,
            kind=ScenarioKind.NONE,
            active_target_identity=None
            if target_path.active_node is None
            else target_path.active_node.identity,
            reasons=(f"STRUCTURE_DATA_{structural.data_quality.value}",),
            presence_waiting_for=("VALID_STRUCTURAL_AUTHORITY",),
            lineage=lineage,
        )

    if (
        structural.direction is StructuralDirection.UNRESOLVED
        or structural.thesis_state is ThesisState.UNRESOLVED
    ):
        return _scenario(
            assessment,
            market_state=market_state,
            target_path=target_path,
            presence=ScenarioPresence.UNKNOWN,
            kind=ScenarioKind.NONE,
            active_target_identity=None
            if target_path.active_node is None
            else target_path.active_node.identity,
            reasons=("STRUCTURAL_LONG_ENTRY_STATE_UNRESOLVED",),
            presence_waiting_for=("STRUCTURAL_DIRECTION_TO_RESOLVE",),
            lineage=lineage,
        )

    # The current product is long-only. A canonical SHORT thesis is analytically
    # valid, but it is not a long-entry scenario and must not be converted to BUY.
    if structural.direction is StructuralDirection.SHORT:
        return _scenario(
            assessment,
            market_state=market_state,
            target_path=target_path,
            presence=ScenarioPresence.ABSENT,
            kind=ScenarioKind.NONE,
            active_target_identity=None,
            reasons=("LONG_ENTRY_REQUIRES_LONG_STRUCTURE",),
            lineage=lineage,
        )

    if structural.thesis_state is ThesisState.INVALIDATED:
        return _scenario(
            assessment,
            market_state=market_state,
            target_path=target_path,
            presence=ScenarioPresence.ABSENT,
            kind=ScenarioKind.NONE,
            active_target_identity=None,
            reasons=("STRUCTURAL_LONG_THESIS_INVALIDATED",),
            lineage=lineage,
        )

    if opportunity.state is OpportunityState.NONE:
        return _scenario(
            assessment,
            market_state=market_state,
            target_path=target_path,
            presence=ScenarioPresence.ABSENT,
            kind=ScenarioKind.NONE,
            active_target_identity=None
            if target_path.active_node is None
            else target_path.active_node.identity,
            reasons=("OBSERVED_DIRECTIONAL_ROOM_INSUFFICIENT",),
            lineage=lineage,
        )

    if not _observed_opportunity(assessment, target_path):
        return _scenario(
            assessment,
            market_state=market_state,
            target_path=target_path,
            presence=ScenarioPresence.UNKNOWN,
            kind=ScenarioKind.NONE,
            active_target_identity=None,
            reasons=("NO_OBSERVED_OPPORTUNITY_DOES_NOT_PROVE_ABSENCE",),
            presence_waiting_for=("OBSERVED_DIRECTIONAL_OPPORTUNITY",),
            lineage=lineage,
        )

    qualification = assess_entry_qualification(
        assessment.eligibility,
        target_path=target_path,
    )
    reasons: list[str] = [
        "OBSERVED_LONG_ENTRY_SCENARIO",
        *qualification.reasons,
    ]
    kind = _scenario_kind(assessment)

    # Eligibility owns the structural-transition wait. Scenario keeps the observed
    # transition character as a reason without emitting the same wait token again.
    if structural.thesis_state is ThesisState.TRANSITIONING:
        reasons.append("EXISTING_LONG_SCENARIO_IN_TRANSITION")

    active = target_path.active_node
    return _scenario(
        assessment,
        market_state=market_state,
        target_path=target_path,
        presence=ScenarioPresence.PRESENT,
        kind=kind,
        active_target_identity=None if active is None else active.identity,
        reasons=tuple(dict.fromkeys(reasons)),
        qualification=qualification,
        lineage=lineage,
    )


def prepare_entry_scenario(
    snapshot: "DecisionInputSnapshot",
    horizon: DecisionHorizon,
    *,
    config: "DecisionEngineConfig | None" = None,
) -> PreparedEntryScenario:
    """Build one scenario and retain the exact prepared assessment behind it."""

    from .engine import prepare_horizon_assessment

    assessment = prepare_horizon_assessment(snapshot, horizon, config=config)
    market = snapshot.market_state
    horizon_market = (
        market.long_term if horizon is DecisionHorizon.LONG_TERM else market.short_term
    )
    path = snapshot.target_path(assessment.structural.direction)
    scenario = build_entry_scenario(
        assessment,
        target_path=path,
        market_state=horizon_market,
    )
    return PreparedEntryScenario(scenario=scenario, assessment=assessment)


def assess_entry_scenario(
    snapshot: "DecisionInputSnapshot",
    horizon: DecisionHorizon,
    *,
    config: "DecisionEngineConfig | None" = None,
) -> EntryScenarioAssessment:
    """Build the causal non-action scenario directly from one frozen input snapshot."""

    return prepare_entry_scenario(snapshot, horizon, config=config).scenario


__all__ = [
    "EntryScenarioAssessment",
    "ScenarioKind",
    "ScenarioPresence",
    "ScenarioStage",
    "assess_entry_scenario",
    "build_entry_scenario",
]
