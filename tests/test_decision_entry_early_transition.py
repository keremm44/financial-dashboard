from __future__ import annotations

from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.eligibility import EligibilityAssessment, EligibilityState
from financial_dashboard.decision.market_state import StructuralRegime
from financial_dashboard.decision.opportunity import OpportunityAssessment, OpportunityState
from financial_dashboard.decision.scenario import ScenarioKind, ScenarioPresence, ScenarioStage, build_entry_scenario
from financial_dashboard.decision.structural import DecisionHorizon, HorizonRelation, StructuralDirection, ThesisState
from financial_dashboard.decision.target_path import TargetPath, TargetPathStatus


def test_strong_st_transition_is_explicit_qualified_early_transition_scenario() -> None:
    assessment = SimpleNamespace(
        horizon=DecisionHorizon.SHORT_TERM,
        structural=SimpleNamespace(
            direction=StructuralDirection.LONG,
            thesis_state=ThesisState.INTACT,
            data_quality=ContextDataQuality.VALID,
            source_refs=(),
        ),
        structural_snapshot=SimpleNamespace(
            relation=HorizonRelation.EARLY_TRANSITION,
            long_term=SimpleNamespace(direction=StructuralDirection.SHORT),
        ),
        st_transition=SimpleNamespace(can_own_trade_thesis=True),
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
        structural_map=SimpleNamespace(structural_regime=StructuralRegime.TRANSITION)
    )

    result = build_entry_scenario(assessment, target_path=path, market_state=market)

    assert result.presence is ScenarioPresence.PRESENT
    assert result.stage is ScenarioStage.QUALIFIED
    assert result.kind is ScenarioKind.EARLY_TRANSITION
    assert result.structural_direction is StructuralDirection.LONG
    assert "SHORT_TERM_EARLY_TRANSITION_TRADE_THESIS" in result.reasons
