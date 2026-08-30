from __future__ import annotations

from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.eligibility import EligibilityAssessment, EligibilityState
from financial_dashboard.decision.market_state import StructuralRegime
from financial_dashboard.decision.opportunity import OpportunityAssessment, OpportunityState
from financial_dashboard.decision.scenario import ScenarioKind, ScenarioPresence, ScenarioStage, build_entry_scenario
from financial_dashboard.decision.structural import DecisionHorizon, HorizonRelation, StructuralDirection, ThesisState
from financial_dashboard.decision.target_path import TargetPath, TargetPathStatus


def _assessment(*, opportunity: OpportunityAssessment, eligibility: EligibilityAssessment):
    return SimpleNamespace(
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
        durability=None,
        opportunity=opportunity,
        eligibility=eligibility,
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
        structural_map=SimpleNamespace(structural_regime=StructuralRegime.TRANSITION)
    )


def test_strong_st_transition_is_explicit_qualified_early_transition_scenario() -> None:
    assessment = _assessment(
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

    result = build_entry_scenario(assessment, target_path=_path(), market_state=_market())

    assert result.presence is ScenarioPresence.PRESENT
    assert result.stage is ScenarioStage.QUALIFIED
    assert result.kind is ScenarioKind.EARLY_TRANSITION
    assert result.structural_direction is StructuralDirection.LONG
    assert "SHORT_TERM_EARLY_TRANSITION_MARKET_THESIS" in result.reasons


def test_early_transition_thesis_remains_present_when_opportunity_is_unobserved() -> None:
    assessment = _assessment(
        opportunity=OpportunityAssessment(
            OpportunityState.UNKNOWN,
            None,
            None,
            None,
            ("NO_DIRECTIONAL_TARGET",),
            (),
        ),
        eligibility=EligibilityAssessment(
            EligibilityState.WAITING,
            ("KNOWN_CONDITIONS_INCOMPLETE",),
            (),
            ("OPPORTUNITY_EVIDENCE_OR_CALIBRATION",),
        ),
    )

    result = build_entry_scenario(assessment, target_path=_path(), market_state=_market())

    assert result.presence is ScenarioPresence.PRESENT
    assert result.stage is ScenarioStage.DEVELOPING
    assert result.kind is ScenarioKind.EARLY_TRANSITION
    assert result.unknown_reason.value == "NONE"
    assert "EARLY_TRANSITION_EXISTS_BEFORE_OPPORTUNITY_OBSERVATION" in result.reasons
    assert "OPPORTUNITY_EVIDENCE_OR_CALIBRATION" in result.waiting_for
