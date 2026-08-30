from __future__ import annotations

from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.context.permissions import GateState, PermissionEnvelope, PermissionScope, PermittedSide
from financial_dashboard.decision.conflict import ConflictState
from financial_dashboard.decision.coverage import CoverageAssessment, CoverageFamily
from financial_dashboard.decision.eligibility import EligibilityAssessment, EligibilityState, assess_eligibility
from financial_dashboard.decision.environment import EnvironmentRisk
from financial_dashboard.decision.market_state import StructuralRegime
from financial_dashboard.decision.opportunity import OpportunityCalibration, OpportunityState, assess_opportunity
from financial_dashboard.decision.scenario import ScenarioPresence, ScenarioStage, build_entry_scenario
from financial_dashboard.decision.structural import DecisionHorizon, HorizonRelation, StructuralDirection, ThesisState
from financial_dashboard.decision.target_path import TargetPath, TargetPathStatus
from financial_dashboard.decision.timing import TimingState
from financial_dashboard.targeting.models import TargetClusterKind, TargetEvidenceType


def _calibration() -> OpportunityCalibration:
    return OpportunityCalibration(none_max_atr=0.2, compressed_max_atr=0.6, moderate_max_atr=1.2)


def _target(*types: TargetEvidenceType, liquidity_anchor=None):
    evidence = tuple(
        SimpleNamespace(origin_event_id=f"origin-{index}", evidence_type=evidence_type)
        for index, evidence_type in enumerate(types, start=1)
    )
    kind = (
        TargetClusterKind.LIQUIDITY_TARGET
        if TargetEvidenceType.LIQUIDITY in types
        else TargetClusterKind.TECHNICAL_ZONE
    )
    return SimpleNamespace(
        identity="TARGET:1",
        distance_atr=0.1,
        quality="SUPPORTED",
        evidence=evidence,
        kind=kind,
        liquidity_anchor=liquidity_anchor,
    )


def _snapshot(target):
    return SimpleNamespace(nearest_upside_target=target, nearest_downside_target=target)


def test_near_liquidity_target_remains_hard_room_constraint() -> None:
    result = assess_opportunity(
        StructuralDirection.LONG,
        _snapshot(_target(TargetEvidenceType.LIQUIDITY, liquidity_anchor=101.0)),
        calibration=_calibration(),
    )

    assert result.state is OpportunityState.NONE
    assert result.hard_room_constraint is True
    assert result.target_semantics == "LIQUIDITY_MAGNET"


def test_near_reaction_only_target_is_soft_room_context() -> None:
    result = assess_opportunity(
        StructuralDirection.LONG,
        _snapshot(_target(TargetEvidenceType.FVG, TargetEvidenceType.ORDER_BLOCK)),
        calibration=_calibration(),
    )

    assert result.state is OpportunityState.NONE
    assert result.hard_room_constraint is False
    assert result.target_semantics == "REACTION_TECHNICAL_ZONE"
    assert "REACTION_TECHNICAL_ZONE_IS_SOFT_ROOM_CONTEXT" in result.reasons


def test_structural_support_resistance_keeps_hard_constraint_even_with_fvg() -> None:
    result = assess_opportunity(
        StructuralDirection.LONG,
        _snapshot(_target(TargetEvidenceType.FVG, TargetEvidenceType.SUPPORT_RESISTANCE)),
        calibration=_calibration(),
    )

    assert result.state is OpportunityState.NONE
    assert result.hard_room_constraint is True
    assert result.target_semantics == "STRUCTURAL_SUPPORT_RESISTANCE"


def test_soft_opportunity_none_does_not_hard_block_ready_entry() -> None:
    opportunity = assess_opportunity(
        StructuralDirection.LONG,
        _snapshot(_target(TargetEvidenceType.FVG)),
        calibration=_calibration(),
    )
    structural = SimpleNamespace(
        direction=StructuralDirection.LONG,
        thesis_state=ThesisState.INTACT,
        data_quality=ContextDataQuality.VALID,
    )
    permission = PermissionEnvelope(
        scope=PermissionScope.CONTINUATION_ONLY,
        permitted_side=PermittedSide.LONG,
        gate_state=GateState.OPEN,
    )
    timing = SimpleNamespace(state=TimingState.READY, waiting_for=())
    conflict = SimpleNamespace(state=ConflictState.NONE)
    environment = SimpleNamespace(risk=EnvironmentRisk.NORMAL)
    coverage = CoverageAssessment(
        valid_fraction=1.0,
        observed_fraction=1.0,
        critical_path_missing=(),
        degraded_families=(),
        unavailable_families=(),
        valid_families=(CoverageFamily.STRUCTURE,),
    )

    result = assess_eligibility(
        structural,
        permission=permission,
        timing=timing,
        opportunity=opportunity,
        conflict=conflict,
        environment=environment,
        coverage=coverage,
    )

    assert result.state is EligibilityState.ELIGIBLE
    assert "OPPORTUNITY_NONE" not in result.blockers
    assert "SOFT_TECHNICAL_ROOM_CONSTRAINT_NOT_HARD_BLOCK" in result.reasons


def test_soft_opportunity_none_does_not_force_scenario_to_developing() -> None:
    opportunity = assess_opportunity(
        StructuralDirection.LONG,
        _snapshot(_target(TargetEvidenceType.FVG)),
        calibration=_calibration(),
    )
    assessment = SimpleNamespace(
        horizon=DecisionHorizon.SHORT_TERM,
        structural=SimpleNamespace(
            direction=StructuralDirection.LONG,
            thesis_state=ThesisState.INTACT,
            data_quality=ContextDataQuality.VALID,
            source_refs=(),
        ),
        structural_snapshot=SimpleNamespace(
            relation=HorizonRelation.ALIGNED,
            long_term=SimpleNamespace(direction=StructuralDirection.LONG),
        ),
        st_transition=None,
        opportunity=opportunity,
        eligibility=EligibilityAssessment(EligibilityState.ELIGIBLE, (), (), ()),
    )
    path = TargetPath(
        symbol="ASELS",
        as_of=1,
        direction=StructuralDirection.LONG,
        current_price=100.0,
        status=TargetPathStatus.READY,
        nodes=(),
        thesis_boundaries=(),
        reasons=(),
    )
    market = SimpleNamespace(
        structural_map=SimpleNamespace(structural_regime=StructuralRegime.DIRECTIONAL)
    )

    result = build_entry_scenario(assessment, target_path=path, market_state=market)

    assert result.presence is ScenarioPresence.PRESENT
    assert result.stage is ScenarioStage.QUALIFIED
    assert "SOFT_TECHNICAL_ROOM_CONSTRAINT" in result.reasons
