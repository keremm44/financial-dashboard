from __future__ import annotations

from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.durability import DurabilityAssessment, DurabilityState
from financial_dashboard.decision.eligibility import EligibilityAssessment, EligibilityState
from financial_dashboard.decision.market_state import StructuralRegime
from financial_dashboard.decision.opportunity import OpportunityAssessment, OpportunityState
from financial_dashboard.decision.scenario import ScenarioStage, build_entry_scenario
from financial_dashboard.decision.structural import DecisionHorizon, HorizonRelation, StructuralDirection, ThesisState
from financial_dashboard.decision.target_path import TargetPath, TargetPathStatus


def _assessment(durability_state: DurabilityState):
    return SimpleNamespace(
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
        durability=DurabilityAssessment(
            durability_state,
            ContextDataQuality.VALID,
            (f"STABIL:{durability_state.value}",),
            (),
        ),
        opportunity=OpportunityAssessment(
            OpportunityState.AMPLE,
            3.0,
            "target-1",
            "SUPPORTED",
            (),
            (),
        ),
        eligibility=EligibilityAssessment(EligibilityState.ELIGIBLE, (), (), ()),
    )


def _path() -> TargetPath:
    return TargetPath(
        symbol="ASELS",
        as_of=1,
        direction=StructuralDirection.LONG,
        current_price=100.0,
        status=TargetPathStatus.READY,
        nodes=(),
        thesis_boundaries=(),
        reasons=(),
    )


def _market():
    return SimpleNamespace(
        structural_map=SimpleNamespace(structural_regime=StructuralRegime.DIRECTIONAL)
    )


def test_broken_stabil_blocks_new_st_long_even_when_structure_is_long() -> None:
    result = build_entry_scenario(
        _assessment(DurabilityState.BROKEN),
        target_path=_path(),
        market_state=_market(),
    )

    assert result.stage is ScenarioStage.BLOCKED
    assert "STABIL_FOUNDATION_BROKEN_FOR_NEW_ST_LONG" in result.blockers


def test_fractured_stabil_keeps_scenario_developing_until_recovery() -> None:
    result = build_entry_scenario(
        _assessment(DurabilityState.FRACTURED),
        target_path=_path(),
        market_state=_market(),
    )

    assert result.stage is ScenarioStage.DEVELOPING
    assert "STABIL_FOUNDATION_TO_RECOVER" in result.waiting_for


def test_softening_stabil_does_not_veto_otherwise_qualified_st_long() -> None:
    result = build_entry_scenario(
        _assessment(DurabilityState.SOFTENING),
        target_path=_path(),
        market_state=_market(),
    )

    assert result.stage is ScenarioStage.QUALIFIED
    assert "STABIL_FOUNDATION_BROKEN_FOR_NEW_ST_LONG" not in result.blockers
    assert "STABIL_FOUNDATION_TO_RECOVER" not in result.waiting_for
