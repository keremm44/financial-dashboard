from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.composer import (
    ActionPolicy,
    ActionSide,
    DecisionAction,
    compose_final_decision,
)
from financial_dashboard.decision.eligibility import EligibilityAssessment, EligibilityState
from financial_dashboard.decision.execution import ExecutionTriggerAssessment, ExecutionTriggerState
from financial_dashboard.decision.structural import (
    DecisionHorizon,
    StructuralAssessment,
    StructuralDirection,
    ThesisState,
)


def _structural(side=StructuralDirection.LONG, horizon=DecisionHorizon.SHORT_TERM):
    return StructuralAssessment(
        horizon=horizon,
        authority_timeframe="1h" if horizon is DecisionHorizon.SHORT_TERM else "1d",
        direction=side,
        thesis_state=ThesisState.INTACT,
        native_state="BULLISH" if side is StructuralDirection.LONG else "BEARISH",
        transition_target=None,
        data_quality=ContextDataQuality.VALID,
        authority_as_of=10,
        protected_high=None,
        protected_low=None,
        weak_high=None,
        weak_low=None,
        source_refs=(),
        reasons=("TEST_STRUCTURE",),
    )


def _eligibility(state=EligibilityState.ELIGIBLE):
    return EligibilityAssessment(
        state=state,
        reasons=("TEST_ELIGIBILITY",),
        blockers=("TEST_BLOCK",) if state is EligibilityState.BLOCKED else (),
        waiting_for=("TEST_WAIT",) if state is EligibilityState.WAITING else (),
    )


def _execution(state=ExecutionTriggerState.ABSENT, side=StructuralDirection.LONG):
    return ExecutionTriggerAssessment(
        state=state,
        side=side,
        timeframe="30m",
        reasons=(state.value,),
        source_refs=(),
    )


def test_blocked_eligibility_produces_no_trade():
    result = compose_final_decision(
        _structural(),
        eligibility=_eligibility(EligibilityState.BLOCKED),
        execution=_execution(),
    )
    assert result.action is DecisionAction.NO_TRADE
    assert result.action_side is ActionSide.NONE


def test_eligible_without_fresh_execution_event_is_ready_not_buy():
    result = compose_final_decision(
        _structural(),
        eligibility=_eligibility(),
        execution=_execution(ExecutionTriggerState.ABSENT),
    )
    assert result.action is DecisionAction.READY
    assert result.action_side is ActionSide.LONG


def test_fresh_long_execution_event_produces_buy():
    result = compose_final_decision(
        _structural(),
        eligibility=_eligibility(),
        execution=_execution(ExecutionTriggerState.CONFIRMED),
    )
    assert result.action is DecisionAction.BUY


def test_missing_execution_data_keeps_market_eligible_path_waiting():
    result = compose_final_decision(
        _structural(),
        eligibility=_eligibility(),
        execution=_execution(ExecutionTriggerState.UNAVAILABLE),
    )
    assert result.action is DecisionAction.WAIT


def test_waiting_eligibility_cannot_be_promoted_by_execution_event():
    result = compose_final_decision(
        _structural(),
        eligibility=_eligibility(EligibilityState.WAITING),
        execution=_execution(ExecutionTriggerState.CONFIRMED),
    )
    assert result.action is DecisionAction.WAIT


def test_short_market_side_does_not_auto_short_in_long_only_cash_policy():
    result = compose_final_decision(
        _structural(StructuralDirection.SHORT),
        eligibility=_eligibility(),
        execution=_execution(ExecutionTriggerState.CONFIRMED, StructuralDirection.SHORT),
    )
    assert result.market_side is StructuralDirection.SHORT
    assert result.action is DecisionAction.NO_TRADE
    assert result.action_side is ActionSide.NONE


def test_short_action_requires_explicit_capability():
    result = compose_final_decision(
        _structural(StructuralDirection.SHORT),
        eligibility=_eligibility(),
        execution=_execution(ExecutionTriggerState.CONFIRMED, StructuralDirection.SHORT),
        policy=ActionPolicy((StructuralDirection.LONG, StructuralDirection.SHORT)),
    )
    assert result.action is DecisionAction.SELL
    assert result.action_side is ActionSide.SHORT


def test_hold_is_not_emitted_by_new_entry_composer():
    actions = {
        compose_final_decision(
            _structural(),
            eligibility=_eligibility(state),
            execution=_execution(ExecutionTriggerState.ABSENT),
        ).action
        for state in EligibilityState
    }
    assert DecisionAction.HOLD not in actions
