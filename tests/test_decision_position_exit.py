from types import SimpleNamespace

import pandas as pd
import pytest

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.execution import ExecutionTriggerEvent, ExecutionTriggerState
from financial_dashboard.decision.exit import (
    compose_position_exit_decision,
    transition_position_exit_lifecycle,
)
from financial_dashboard.decision.lifecycle import ExitStage, PositionState, TradeLifecycleState
from financial_dashboard.decision.position_metadata import PositionEntryMetadata
from financial_dashboard.decision.scenario import ScenarioKind
from financial_dashboard.decision.st_exit_intent import STExitFamily
from financial_dashboard.decision.structural import (
    DecisionHorizon,
    HorizonRelation,
    StructuralDirection,
    ThesisState,
)
from financial_dashboard.decision.trade_exit import ExitExecutionState, PositionHealth


ENTRY_AS_OF = pd.Timestamp("2026-01-05 10:00")


def _metadata(horizon: DecisionHorizon) -> PositionEntryMetadata:
    return PositionEntryMetadata(
        symbol="TEST",
        entry_horizon=horizon,
        scenario_kind=ScenarioKind.CONTINUATION,
        entry_as_of=ENTRY_AS_OF,
        entry_price=100.0,
        active_target_identity="target:1",
        execution_timeframe="30m",
        execution_observed_at=ENTRY_AS_OF,
        execution_available_at=ENTRY_AS_OF,
        execution_reason="ENTRY_CONFIRMED",
        source_lineage=("entry:scenario",),
    )


def _state(horizon: DecisionHorizon | None) -> TradeLifecycleState:
    return TradeLifecycleState(
        position=PositionState.OPEN,
        exit_stage=ExitStage.MONITOR,
        trade_id="trade:1",
        entry_as_of=ENTRY_AS_OF,
        entry_metadata=None if horizon is None else _metadata(horizon),
    )


def _authority(
    *,
    direction: StructuralDirection,
    thesis: ThesisState,
    transition_target: StructuralDirection | None = None,
    quality: ContextDataQuality = ContextDataQuality.VALID,
):
    return SimpleNamespace(
        direction=direction,
        thesis_state=thesis,
        transition_target=transition_target,
        data_quality=quality,
        source_refs=(),
    )


def _structural_snapshot(
    *,
    lt_direction=StructuralDirection.LONG,
    lt_thesis=ThesisState.INTACT,
    lt_transition_target=None,
    st_direction=StructuralDirection.LONG,
    st_thesis=ThesisState.INTACT,
    st_transition_target=None,
    relation=HorizonRelation.ALIGNED,
):
    return SimpleNamespace(
        long_term=_authority(
            direction=lt_direction,
            thesis=lt_thesis,
            transition_target=lt_transition_target,
        ),
        short_term=_authority(
            direction=st_direction,
            thesis=st_thesis,
            transition_target=st_transition_target,
        ),
        relation=relation,
    )


def _st_economic(family: STExitFamily | None = None):
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
        reasons=("ST_ECONOMIC_EXIT_TEST",) if terminal else ("ST_ECONOMIC_HOLD_TEST",),
        waiting_for=("FRESH_LONG_EXIT_EXECUTION_EVENT",) if terminal else (),
        source_refs=(),
        source_lineage=("economic:test",) if terminal else (),
    )


def _exit_event(as_of, *, state=ExecutionTriggerState.CONFIRMED):
    return ExecutionTriggerEvent(
        state=state,
        side=StructuralDirection.SHORT,
        timeframe="30m",
        observed_at=as_of,
        available_at=as_of,
        reason="FRESH_EXIT_CONFIRMATION",
        source_refs=(),
    )


def test_long_term_entry_keeps_existing_lt_exit_authority():
    as_of = pd.Timestamp("2026-01-05 12:00")
    decision = compose_position_exit_decision(
        _state(DecisionHorizon.LONG_TERM),
        _structural_snapshot(
            lt_direction=StructuralDirection.LONG,
            lt_thesis=ThesisState.INTACT,
            st_direction=StructuralDirection.SHORT,
            st_thesis=ThesisState.INTACT,
            relation=HorizonRelation.COUNTER_REACTION,
        ),
        as_of=as_of,
    )

    assert decision.action is DecisionAction.HOLD
    assert decision.entry_horizon is DecisionHorizon.LONG_TERM
    assert decision.stage is ExitStage.MONITOR
    assert decision.position_health is PositionHealth.PROTECTED
    assert "LT_LONG_INTACT_ST_COUNTER_REACTION" in decision.reasons


def test_short_term_terminal_economic_exit_arms_existing_execution_gate():
    as_of = pd.Timestamp("2026-01-05 12:00")
    state = _state(DecisionHorizon.SHORT_TERM)
    structural = _structural_snapshot(
        lt_direction=StructuralDirection.LONG,
        lt_thesis=ThesisState.INTACT,
        st_direction=StructuralDirection.LONG,
        st_thesis=ThesisState.INTACT,
    )
    economic = _st_economic(STExitFamily.PROTECTIVE_EXIT)

    waiting = compose_position_exit_decision(
        state,
        structural,
        as_of=as_of,
        st_economic_exit=economic,
    )
    assert waiting.action is DecisionAction.HOLD
    assert waiting.entry_horizon is DecisionHorizon.SHORT_TERM
    assert waiting.stage is ExitStage.EXIT_READY
    assert waiting.economic_exit_family is STExitFamily.PROTECTIVE_EXIT
    assert waiting.execution.state is ExitExecutionState.ABSENT
    assert waiting.waiting_for == ("FRESH_LONG_EXIT_EXECUTION_EVENT",)

    confirmed = compose_position_exit_decision(
        state,
        structural,
        as_of=as_of,
        execution_event=_exit_event(as_of),
        st_economic_exit=economic,
    )
    assert confirmed.action is DecisionAction.SELL
    assert confirmed.execution.state is ExitExecutionState.CONFIRMED
    assert confirmed.execution_event_consumed is True


def test_short_term_structure_transition_does_not_arm_without_economic_exit():
    as_of = pd.Timestamp("2026-01-05 12:00")
    decision = compose_position_exit_decision(
        _state(DecisionHorizon.SHORT_TERM),
        _structural_snapshot(
            st_direction=StructuralDirection.LONG,
            st_thesis=ThesisState.TRANSITIONING,
            st_transition_target=StructuralDirection.SHORT,
            relation=HorizonRelation.STRUCTURAL_CONFLICT,
        ),
        as_of=as_of,
        execution_event=_exit_event(as_of),
        st_economic_exit=_st_economic(),
    )

    assert decision.action is DecisionAction.HOLD
    assert decision.stage is ExitStage.MONITOR
    assert decision.execution.state is ExitExecutionState.NOT_ARMED
    assert decision.execution_event_consumed is False
    assert decision.economic_exit_family is None
    assert "ST_ECONOMIC_HOLD_TEST" in decision.reasons


def test_premature_exit_event_is_not_consumed_or_carried_forward():
    as_of = pd.Timestamp("2026-01-05 12:00")
    decision = compose_position_exit_decision(
        _state(DecisionHorizon.LONG_TERM),
        _structural_snapshot(),
        as_of=as_of,
        execution_event=_exit_event(as_of),
    )

    assert decision.stage is ExitStage.MONITOR
    assert decision.action is DecisionAction.HOLD
    assert decision.execution.state is ExitExecutionState.NOT_ARMED
    assert decision.execution_event_consumed is False
    assert decision.execution.source_refs == ()


def test_missing_legacy_entry_metadata_fails_closed_without_guessing_horizon():
    as_of = pd.Timestamp("2026-01-05 12:00")
    decision = compose_position_exit_decision(
        _state(None),
        _structural_snapshot(
            lt_direction=StructuralDirection.SHORT,
            lt_thesis=ThesisState.INTACT,
            st_direction=StructuralDirection.SHORT,
            st_thesis=ThesisState.INTACT,
        ),
        as_of=as_of,
        execution_event=_exit_event(as_of),
    )

    assert decision.entry_horizon is None
    assert decision.stage is ExitStage.EXIT_WATCH
    assert decision.position_health is PositionHealth.UNKNOWN
    assert decision.action is DecisionAction.HOLD
    assert decision.execution_event_consumed is False
    assert decision.waiting_for == ("POSITION_ENTRY_METADATA_TO_RECOVER",)


def test_confirmed_step8_economic_exit_closes_position_and_preserves_reason():
    as_of = pd.Timestamp("2026-01-05 12:00")
    state = _state(DecisionHorizon.SHORT_TERM)
    decision = compose_position_exit_decision(
        state,
        _structural_snapshot(),
        as_of=as_of,
        execution_event=_exit_event(as_of),
        st_economic_exit=_st_economic(STExitFamily.PROFIT_HARVEST),
    )

    transition = transition_position_exit_lifecycle(state, decision)
    assert transition.action is DecisionAction.SELL
    assert transition.current.position is PositionState.FLAT
    assert transition.current.entry_metadata is None
    assert transition.current.last_closed_st_exit is not None
    assert transition.current.last_closed_st_exit.family is STExitFamily.PROFIT_HARVEST
    assert transition.current.last_closed_st_exit.reasons == ("ST_ECONOMIC_EXIT_TEST",)


def test_failed_exit_event_is_consumed_but_does_not_sell():
    as_of = pd.Timestamp("2026-01-05 12:00")
    decision = compose_position_exit_decision(
        _state(DecisionHorizon.SHORT_TERM),
        _structural_snapshot(),
        as_of=as_of,
        execution_event=_exit_event(as_of, state=ExecutionTriggerState.FAILED),
        st_economic_exit=_st_economic(STExitFamily.PROTECTIVE_EXIT),
    )

    assert decision.action is DecisionAction.HOLD
    assert decision.stage is ExitStage.EXIT_READY
    assert decision.execution.state is ExitExecutionState.FAILED
    assert decision.execution_event_consumed is True
    assert decision.waiting_for == (
        "FRESH_LONG_EXIT_EXECUTION_EVENT",
        "NEW_LONG_EXIT_EXECUTION_EVENT",
    )


def test_exit_transition_rejects_horizon_reclassification():
    as_of = pd.Timestamp("2026-01-05 12:00")
    state = _state(DecisionHorizon.LONG_TERM)
    decision = compose_position_exit_decision(
        _state(DecisionHorizon.SHORT_TERM),
        _structural_snapshot(),
        as_of=as_of,
        st_economic_exit=_st_economic(),
    )

    with pytest.raises(ValueError, match="frozen position metadata"):
        transition_position_exit_lifecycle(state, decision)
