from types import SimpleNamespace

import pytest

from financial_dashboard.decision.arbiter import arbitrate_entry_scenarios
from financial_dashboard.decision.composer import ActionSide, DecisionAction, FinalDecision
from financial_dashboard.decision.eligibility import EligibilityState
from financial_dashboard.decision.entry import compose_entry_decision
from financial_dashboard.decision.execution import ExecutionTriggerState
from financial_dashboard.decision.market_state import StructuralRegime
from financial_dashboard.decision.opportunity import OpportunityState
from financial_dashboard.decision.scenario import (
    EntryScenarioAssessment,
    ScenarioKind,
    ScenarioPresence,
    ScenarioStage,
)
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection, ThesisState
from financial_dashboard.decision.target_path import TargetPathStatus


def _scenario(
    horizon,
    presence,
    *,
    stage=ScenarioStage.QUALIFIED,
    direction=StructuralDirection.LONG,
):
    if presence is ScenarioPresence.UNKNOWN:
        stage = ScenarioStage.UNAVAILABLE
    elif presence is ScenarioPresence.ABSENT:
        stage = ScenarioStage.NOT_APPLICABLE
    return EntryScenarioAssessment(
        horizon=horizon,
        presence=presence,
        stage=stage,
        kind=ScenarioKind.CONTINUATION if presence is ScenarioPresence.PRESENT else ScenarioKind.NONE,
        structural_direction=direction,
        thesis_state=ThesisState.INTACT,
        structural_regime=StructuralRegime.DIRECTIONAL,
        opportunity_state=(
            OpportunityState.MODERATE
            if presence is ScenarioPresence.PRESENT
            else OpportunityState.UNKNOWN
            if presence is ScenarioPresence.UNKNOWN
            else OpportunityState.NONE
        ),
        target_path_status=(
            TargetPathStatus.READY
            if presence is ScenarioPresence.PRESENT
            else TargetPathStatus.UNKNOWN
            if presence is ScenarioPresence.UNKNOWN
            else TargetPathStatus.NO_OBSERVED_PATH
        ),
        active_target_identity="T1" if presence is ScenarioPresence.PRESENT else None,
        eligibility_state=(
            EligibilityState.ELIGIBLE
            if stage is ScenarioStage.QUALIFIED
            else EligibilityState.BLOCKED
            if stage is ScenarioStage.BLOCKED
            else EligibilityState.WAITING
        ),
        reasons=("SCENARIO",) if presence is ScenarioPresence.PRESENT else (),
        blockers=("BLOCKED",) if stage is ScenarioStage.BLOCKED else (),
        waiting_for=("WAIT_FOR_SETUP",) if stage is ScenarioStage.DEVELOPING else (),
        source_lineage=(f"{horizon.value}:scenario",),
    )


def _assessment(horizon, action, *, execution=ExecutionTriggerState.ABSENT):
    final = FinalDecision(
        horizon=horizon,
        market_side=StructuralDirection.LONG,
        action_side=ActionSide.LONG,
        action=action,
        eligibility=EligibilityState.ELIGIBLE,
        execution_trigger=execution,
        reasons=("FINAL",),
        blockers=(),
        waiting_for=("FRESH_EXECUTION_EVENT",) if action is DecisionAction.READY else (),
        source_lineage=(f"{horizon.value}:final",),
    )
    return SimpleNamespace(
        horizon=horizon,
        final=final,
        execution=SimpleNamespace(state=execution),
    )


def test_blocked_lt_keeps_ownership_and_cannot_be_bypassed_into_buy():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT, stage=ScenarioStage.BLOCKED),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT),
    )

    result = compose_entry_decision(arbitration)

    assert result.selected_horizon is DecisionHorizon.LONG_TERM
    assert result.action is DecisionAction.NO_TRADE
    assert result.blockers == ("BLOCKED",)
    assert result.is_actionable_signal is False


def test_developing_lt_keeps_ownership_and_waits_even_when_st_is_qualified():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT, stage=ScenarioStage.DEVELOPING),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT),
    )

    result = compose_entry_decision(arbitration)

    assert result.selected_horizon is DecisionHorizon.LONG_TERM
    assert result.action is DecisionAction.WAIT
    assert "WAIT_FOR_SETUP" in result.waiting_for


def test_unresolved_lt_ownership_waits_and_does_not_consume_st():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.UNKNOWN),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT),
    )

    result = compose_entry_decision(arbitration)

    assert result.selected_horizon is None
    assert result.action is DecisionAction.WAIT
    assert result.execution_event_consumed is False


def test_both_absent_produces_no_trade_not_a_synthetic_entry():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.ABSENT),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.ABSENT),
    )

    result = compose_entry_decision(arbitration)

    assert result.action is DecisionAction.NO_TRADE
    assert result.selected_horizon is None


def test_qualified_selected_scenario_without_fresh_event_stops_at_ready():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT),
    )

    result = compose_entry_decision(
        arbitration,
        selected_assessment=_assessment(DecisionHorizon.LONG_TERM, DecisionAction.READY),
    )

    assert result.action is DecisionAction.READY
    assert result.execution_state is ExecutionTriggerState.ABSENT
    assert result.is_actionable_signal is False


def test_qualified_selected_scenario_and_confirmed_execution_can_emit_buy():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT),
    )

    result = compose_entry_decision(
        arbitration,
        selected_assessment=_assessment(
            DecisionHorizon.LONG_TERM,
            DecisionAction.BUY,
            execution=ExecutionTriggerState.CONFIRMED,
        ),
        execution_event_consumed=True,
    )

    assert result.action is DecisionAction.BUY
    assert result.selected_horizon is DecisionHorizon.LONG_TERM
    assert result.execution_event_consumed is True
    assert result.is_actionable_signal is True


def test_st_entry_is_possible_only_after_explicit_lt_absence():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.ABSENT),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT),
    )

    result = compose_entry_decision(
        arbitration,
        selected_assessment=_assessment(
            DecisionHorizon.SHORT_TERM,
            DecisionAction.BUY,
            execution=ExecutionTriggerState.CONFIRMED,
        ),
        execution_event_consumed=True,
    )

    assert result.action is DecisionAction.BUY
    assert result.selected_horizon is DecisionHorizon.SHORT_TERM


def test_non_qualified_scenario_rejects_execution_assessment_instead_of_bypassing_gate():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT, stage=ScenarioStage.DEVELOPING),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.ABSENT),
    )

    with pytest.raises(ValueError):
        compose_entry_decision(
            arbitration,
            selected_assessment=_assessment(DecisionHorizon.LONG_TERM, DecisionAction.BUY),
        )


def test_selected_assessment_must_match_arbiter_horizon():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT),
    )

    with pytest.raises(ValueError):
        compose_entry_decision(
            arbitration,
            selected_assessment=_assessment(DecisionHorizon.SHORT_TERM, DecisionAction.READY),
        )


def test_entry_layer_rejects_sell_or_hold_from_lower_level_composer():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.ABSENT),
    )

    with pytest.raises(ValueError):
        compose_entry_decision(
            arbitration,
            selected_assessment=_assessment(DecisionHorizon.LONG_TERM, DecisionAction.SELL),
        )
