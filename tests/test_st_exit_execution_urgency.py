from types import SimpleNamespace

import pandas as pd

from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.execution import ExecutionTriggerEvent, ExecutionTriggerState
from financial_dashboard.decision.exit import (
    compose_position_exit_decision,
    transition_position_exit_lifecycle,
)
from financial_dashboard.decision.lifecycle import (
    ExitStage,
    PositionState,
    TradeLifecycleState,
    transition_st_exit_intent,
)
from financial_dashboard.decision.lifecycle_persistence import (
    CANONICAL_LIFECYCLE_CONTRACT_VERSION,
    TRADE_LIFECYCLE_STATE_SCHEMA_VERSION,
    deserialize_trade_lifecycle_state,
    serialize_trade_lifecycle_state,
)
from financial_dashboard.decision.position_metadata import PositionEntryMetadata, STTradeMemory
from financial_dashboard.decision.scenario import ScenarioKind
from financial_dashboard.decision.st_economic_history import STEconomicHistory
from financial_dashboard.decision.st_exit_execution import STExitExecutionUrgency
from financial_dashboard.decision.st_exit_intent import STExitFamily
from financial_dashboard.decision.st_thesis_identity import STEconomicMission, STThesisFamily
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection
from financial_dashboard.decision.trade_exit import ExitExecutionState, PositionHealth


ENTRY = pd.Timestamp("2026-01-05 10:00")
T1 = pd.Timestamp("2026-01-05 12:00")
T2 = pd.Timestamp("2026-01-05 12:30")


def _metadata() -> PositionEntryMetadata:
    return PositionEntryMetadata(
        symbol="TEST",
        entry_horizon=DecisionHorizon.SHORT_TERM,
        scenario_kind=ScenarioKind.SHORT_TERM_STANDALONE,
        entry_as_of=ENTRY,
        entry_price=100.0,
        active_target_identity=None,
        execution_timeframe="30m",
        execution_observed_at=ENTRY,
        execution_available_at=ENTRY,
        execution_reason="ENTRY_CONFIRMED",
        source_lineage=(),
        st_trade_memory=STTradeMemory(
            thesis_family=STThesisFamily.UNRESOLVED,
            economic_mission=STEconomicMission.UNRESOLVED,
            initial_defended_anchor=None,
            initial_target_context=None,
        ),
    )


def _state(*, intent=None, stage=ExitStage.MONITOR) -> TradeLifecycleState:
    return TradeLifecycleState(
        position=PositionState.OPEN,
        exit_stage=stage,
        trade_id="trade:step9",
        entry_as_of=ENTRY,
        entry_metadata=_metadata(),
        st_economic_history=STEconomicHistory(),
        st_exit_intent=intent,
    )


def _economic(family: STExitFamily | None):
    terminal = family is not None
    return SimpleNamespace(
        exit_family=family,
        stage=ExitStage.EXIT_READY if terminal else ExitStage.MONITOR,
        position_health=(
            PositionHealth.PRESSURED
            if family is STExitFamily.PROTECTIVE_EXIT
            else PositionHealth.PROTECTED
            if family is STExitFamily.PROFIT_HARVEST
            else PositionHealth.HEALTHY
        ),
        reasons=("STEP9_ECONOMIC_TERMINAL",) if terminal else ("STEP9_ECONOMIC_HOLD",),
        waiting_for=(),
        source_refs=(),
        source_lineage=("economic:step9",) if terminal else (),
    )


def _event(
    as_of,
    *,
    state=ExecutionTriggerState.CONFIRMED,
    side=StructuralDirection.SHORT,
    timeframe="30m",
    observed_at=None,
    available_at=None,
):
    return ExecutionTriggerEvent(
        state=state,
        side=side,
        timeframe=timeframe,
        observed_at=as_of if observed_at is None else observed_at,
        available_at=as_of if available_at is None else available_at,
        reason="STEP9_EXIT_EVENT",
        source_refs=(),
    )


def _compose(state, family, *, as_of=T1, event=None, channel_available=True):
    return compose_position_exit_decision(
        state,
        SimpleNamespace(),
        as_of=as_of,
        execution_event=event,
        channel_available=channel_available,
        st_economic_exit=_economic(family),
    )


def test_step9_changes_behavior_contract_not_persistent_state_schema():
    assert TRADE_LIFECYCLE_STATE_SCHEMA_VERSION == 5
    assert CANONICAL_LIFECYCLE_CONTRACT_VERSION == 8


def test_protective_exit_is_policy_mandated_without_timing_confirmation():
    state = _state()
    decision = _compose(
        state,
        STExitFamily.PROTECTIVE_EXIT,
        channel_available=False,
    )

    assert decision.action is DecisionAction.SELL
    assert decision.stage is ExitStage.EXIT_READY
    assert decision.execution_urgency is STExitExecutionUrgency.PROTECTIVE_IMMEDIATE
    assert decision.execution.state is ExitExecutionState.ABSENT
    assert decision.execution_event_consumed is False
    assert decision.waiting_for == ()

    transition = transition_position_exit_lifecycle(state, decision)
    assert transition.action is DecisionAction.SELL
    assert transition.reason == "LIFECYCLE_OPEN_EXIT_EXECUTED_POLICY_MANDATE"
    assert transition.current.position is PositionState.FLAT
    assert transition.current.last_closed_st_exit is not None
    assert transition.current.last_closed_st_exit.family is STExitFamily.PROTECTIVE_EXIT


def test_protective_exit_ignores_bad_timing_event_instead_of_making_timing_a_veto():
    state = _state()
    irrelevant_bad_event = _event(
        T1,
        state=ExecutionTriggerState.FAILED,
        side=StructuralDirection.LONG,
        timeframe="5m",
        observed_at=ENTRY,
        available_at=T2,
    )

    decision = _compose(
        state,
        STExitFamily.PROTECTIVE_EXIT,
        event=irrelevant_bad_event,
        channel_available=False,
    )

    assert decision.action is DecisionAction.SELL
    assert decision.execution_urgency is STExitExecutionUrgency.PROTECTIVE_IMMEDIATE
    assert decision.execution_event_consumed is False
    assert "STEP9_EXIT_EVENT" not in decision.reasons


def test_new_profit_harvest_gets_one_bounded_exit_quality_window():
    state = _state()
    decision = _compose(state, STExitFamily.PROFIT_HARVEST)

    assert decision.action is DecisionAction.HOLD
    assert decision.stage is ExitStage.EXIT_READY
    assert decision.execution_urgency is STExitExecutionUrgency.HARVEST_QUALITY_WINDOW
    assert decision.execution.state is ExitExecutionState.ABSENT
    assert decision.execution_event_consumed is False
    assert decision.waiting_for == ("HARVEST_EXIT_QUALITY_WINDOW",)

    transition = transition_position_exit_lifecycle(state, decision)
    assert transition.current.position is PositionState.OPEN
    assert transition.current.st_exit_intent is not None
    assert transition.current.st_exit_intent.family is STExitFamily.PROFIT_HARVEST
    assert transition.current.st_exit_intent.committed_at == T1
    assert transition.current.exit_stage is ExitStage.EXIT_READY


def test_new_profit_harvest_can_execute_on_fresh_quality_event_same_cycle():
    state = _state()
    decision = _compose(
        state,
        STExitFamily.PROFIT_HARVEST,
        event=_event(T1),
    )

    assert decision.action is DecisionAction.SELL
    assert decision.execution_urgency is STExitExecutionUrgency.HARVEST_QUALITY_WINDOW
    assert decision.execution.state is ExitExecutionState.CONFIRMED
    assert decision.execution_event_consumed is True

    closed = transition_position_exit_lifecycle(state, decision).current
    assert closed.position is PositionState.FLAT
    assert closed.last_closed_st_exit is not None
    assert closed.last_closed_st_exit.family is STExitFamily.PROFIT_HARVEST


def test_pending_harvest_releases_on_next_causal_decision_without_new_event():
    pending = transition_st_exit_intent(
        _state(stage=ExitStage.EXIT_READY),
        STExitFamily.PROFIT_HARVEST,
        as_of=T1,
        reasons=("HARVEST_ALREADY_COMMITTED",),
    )

    decision = _compose(
        pending,
        STExitFamily.PROFIT_HARVEST,
        as_of=T2,
        channel_available=False,
    )

    assert decision.action is DecisionAction.SELL
    assert decision.execution_urgency is STExitExecutionUrgency.HARVEST_RELEASE_DUE
    assert decision.execution.state is ExitExecutionState.ABSENT
    assert decision.execution_event_consumed is False
    assert decision.waiting_for == ()

    closed = transition_position_exit_lifecycle(pending, decision).current
    assert closed.position is PositionState.FLAT
    assert closed.last_closed_st_exit is not None
    assert closed.last_closed_st_exit.family is STExitFamily.PROFIT_HARVEST
    assert closed.last_closed_st_exit.intent_committed_at == T1


def test_pending_harvest_escalates_to_protective_and_exits_immediately():
    pending = transition_st_exit_intent(
        _state(stage=ExitStage.EXIT_READY),
        STExitFamily.PROFIT_HARVEST,
        as_of=T1,
        reasons=("HARVEST_ALREADY_COMMITTED",),
    )

    decision = _compose(
        pending,
        STExitFamily.PROTECTIVE_EXIT,
        as_of=T2,
    )

    assert decision.action is DecisionAction.SELL
    assert decision.execution_urgency is STExitExecutionUrgency.PROTECTIVE_IMMEDIATE
    assert decision.execution_event_consumed is False

    closed = transition_position_exit_lifecycle(pending, decision).current
    assert closed.position is PositionState.FLAT
    assert closed.last_closed_st_exit is not None
    assert closed.last_closed_st_exit.family is STExitFamily.PROTECTIVE_EXIT
    assert closed.last_closed_st_exit.intent_committed_at == T2
    assert closed.last_closed_st_exit.reasons == ("STEP9_ECONOMIC_TERMINAL",)


def test_restart_preserves_harvest_release_and_event_consumption_outcome():
    initial = _state()
    waiting = _compose(initial, STExitFamily.PROFIT_HARVEST)
    pending = transition_position_exit_lifecycle(initial, waiting).current
    restored = deserialize_trade_lifecycle_state(serialize_trade_lifecycle_state(pending))
    assert restored == pending

    fresh_event = _event(T2)
    cold_decision = _compose(
        pending,
        STExitFamily.PROFIT_HARVEST,
        as_of=T2,
        event=fresh_event,
    )
    restart_decision = _compose(
        restored,
        STExitFamily.PROFIT_HARVEST,
        as_of=T2,
        event=fresh_event,
    )

    for decision in (cold_decision, restart_decision):
        assert decision.action is DecisionAction.SELL
        assert decision.execution_urgency is STExitExecutionUrgency.HARVEST_RELEASE_DUE
        assert decision.execution_event_consumed is False
        assert "STEP9_EXIT_EVENT" not in decision.reasons

    assert transition_position_exit_lifecycle(pending, cold_decision).current == transition_position_exit_lifecycle(
        restored,
        restart_decision,
    ).current


def test_nonterminal_st_hold_does_not_consume_premature_exit_event():
    state = _state()
    decision = _compose(
        state,
        None,
        event=_event(T1),
    )

    assert decision.action is DecisionAction.HOLD
    assert decision.stage is ExitStage.MONITOR
    assert decision.execution_urgency is STExitExecutionUrgency.NOT_ARMED
    assert decision.execution.state is ExitExecutionState.NOT_ARMED
    assert decision.execution_event_consumed is False
