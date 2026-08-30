from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.durability import DurabilityState
from financial_dashboard.decision.eligibility import EligibilityState
from financial_dashboard.decision.market_state import StructuralRegime
from financial_dashboard.decision.opportunity import OpportunityState
from financial_dashboard.decision.scenario import ScenarioStage, build_entry_scenario
from financial_dashboard.decision.structural import (
    DecisionHorizon,
    HorizonRelation,
    StructuralDirection,
    ThesisState,
)
from financial_dashboard.decision.target_path import TargetPath, TargetPathStatus


def _target_path() -> TargetPath:
    return TargetPath(
        symbol="TEST",
        as_of="2026-01-01T12:00:00+03:00",
        direction=StructuralDirection.LONG,
        current_price=100.0,
        status=TargetPathStatus.READY,
        nodes=(),
        thesis_boundaries=(),
        reasons=(),
    )


def _market_state(regime: StructuralRegime):
    return SimpleNamespace(
        structural_map=SimpleNamespace(structural_regime=regime),
    )


def _assessment(
    *,
    thesis_state,
    opportunity_state,
    eligibility_state,
    waiting_for=(),
    blockers=(),
    durability=None,
):
    return SimpleNamespace(
        horizon=DecisionHorizon.SHORT_TERM,
        structural=SimpleNamespace(
            data_quality=ContextDataQuality.VALID,
            direction=StructuralDirection.LONG,
            thesis_state=thesis_state,
            source_refs=(),
        ),
        structural_snapshot=SimpleNamespace(
            relation=HorizonRelation.ALIGNED,
            long_term=SimpleNamespace(direction=StructuralDirection.LONG),
        ),
        opportunity=SimpleNamespace(
            state=opportunity_state,
            room_atr=0.2 if opportunity_state is OpportunityState.NONE else 2.0,
            target_identity="T1",
            source_lineage=(),
            hard_room_constraint=True,
        ),
        eligibility=SimpleNamespace(
            state=eligibility_state,
            blockers=tuple(blockers),
            waiting_for=tuple(waiting_for),
        ),
        durability=durability,
        st_transition=None,
    )


def test_scenario_does_not_duplicate_structural_transition_gate():
    assessment = _assessment(
        thesis_state=ThesisState.TRANSITIONING,
        opportunity_state=OpportunityState.MODERATE,
        eligibility_state=EligibilityState.WAITING,
        waiting_for=("CANONICAL_STRUCTURAL_FOLLOW_THROUGH",),
    )

    scenario = build_entry_scenario(
        assessment,
        target_path=_target_path(),
        market_state=_market_state(StructuralRegime.TRANSITION),
    )

    assert scenario.stage is ScenarioStage.DEVELOPING
    assert "CANONICAL_STRUCTURAL_FOLLOW_THROUGH" in scenario.waiting_for
    assert "STRUCTURAL_TRANSITION_TO_RESOLVE" not in scenario.waiting_for
    assert "EXISTING_LONG_SCENARIO_IN_TRANSITION" in scenario.reasons


def test_scenario_does_not_duplicate_hard_opportunity_gate():
    assessment = _assessment(
        thesis_state=ThesisState.INTACT,
        opportunity_state=OpportunityState.NONE,
        eligibility_state=EligibilityState.BLOCKED,
        blockers=("OPPORTUNITY_NONE",),
    )

    scenario = build_entry_scenario(
        assessment,
        target_path=_target_path(),
        market_state=_market_state(StructuralRegime.DIRECTIONAL),
    )

    assert scenario.stage is ScenarioStage.BLOCKED
    assert "OPPORTUNITY_NONE" in scenario.blockers
    assert "OBSERVED_DIRECTIONAL_ROOM_INSUFFICIENT" in scenario.reasons
    assert "MORE_DIRECTIONAL_ROOM" not in scenario.waiting_for


def test_stabil_fractured_support_is_hard_block_not_recovery_wait():
    assessment = _assessment(
        thesis_state=ThesisState.INTACT,
        opportunity_state=OpportunityState.MODERATE,
        eligibility_state=EligibilityState.ELIGIBLE,
        durability=SimpleNamespace(
            state=DurabilityState.FRACTURED,
            reasons=("VALIDITY:BREACHED", "INTERACTION:BREAKDOWN_ACCEPTED"),
        ),
    )

    scenario = build_entry_scenario(
        assessment,
        target_path=_target_path(),
        market_state=_market_state(StructuralRegime.DIRECTIONAL),
    )

    assert scenario.stage is ScenarioStage.BLOCKED
    assert "STABIL_SUPPORT_NOT_HOLDING_FOR_NEW_ST_LONG" in scenario.blockers
    assert "STABIL_FOUNDATION_TO_RECOVER" not in scenario.waiting_for


def test_stabil_flat_or_ranging_softening_does_not_require_bullish_recovery():
    assessment = _assessment(
        thesis_state=ThesisState.INTACT,
        opportunity_state=OpportunityState.MODERATE,
        eligibility_state=EligibilityState.ELIGIBLE,
        durability=SimpleNamespace(
            state=DurabilityState.SOFTENING,
            reasons=("MOTION:FLAT_AFTER_FALL", "INTERACTION:RANGE_AROUND_SUPPORT"),
        ),
    )

    scenario = build_entry_scenario(
        assessment,
        target_path=_target_path(),
        market_state=_market_state(StructuralRegime.DIRECTIONAL),
    )

    assert scenario.stage is ScenarioStage.QUALIFIED
    assert "STABIL_SUPPORT_APPROACH_TO_SETTLE" not in scenario.waiting_for
    assert "STABIL_FOUNDATION_TO_RECOVER" not in scenario.waiting_for


def test_stabil_direct_falling_support_approach_waits_for_acceptance():
    assessment = _assessment(
        thesis_state=ThesisState.INTACT,
        opportunity_state=OpportunityState.MODERATE,
        eligibility_state=EligibilityState.ELIGIBLE,
        durability=SimpleNamespace(
            state=DurabilityState.SOFTENING,
            reasons=("MOTION:FALLING", "INTERACTION:APPROACHING_SUPPORT"),
        ),
    )

    scenario = build_entry_scenario(
        assessment,
        target_path=_target_path(),
        market_state=_market_state(StructuralRegime.DIRECTIONAL),
    )

    assert scenario.stage is ScenarioStage.DEVELOPING
    assert "STABIL_SUPPORT_APPROACH_TO_SETTLE" in scenario.waiting_for
    assert "STABIL_SUPPORT_UNDER_DIRECT_FALLING_PRESSURE" in scenario.reasons
