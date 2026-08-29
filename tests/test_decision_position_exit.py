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


def _exit_event(as_of, *, state=ExecutionTriggerState.CONFIRMED, reason="30M_PATTERN_BREAK_CONFIRMED"):
    return ExecutionTriggerEvent(
        state=state,
        side=StructuralDirection.SHORT,
        timeframe="30m",
        observed_at=as_of,
        available_at=as_of,
        reason=reason,
        source_refs=(),
    )


def test_long_term_entry_arms_on_1h_bearish_without_waiting_for_daily_bos():
    as_of = pd.Timestamp("2026-01-05 12:00")
    state = _state(DecisionHorizon.LONG_TERM)
    structural = _structural_snapshot(
        lt_direction=StructuralDirection.LONG,
        lt_thesis=ThesisState.INTACT,
        st_direction=StructuralDirection.SHORT,
        st_thesis=ThesisState.INTACT,
        relation=HorizonRelation.COUNTER_REACTION,
    )
    waiting = compose_position_exit_decision(state, structural, as_of=as_of)

    assert waiting.action is DecisionAction.HOLD
    assert waiting.entry_horizon is DecisionHorizon.LONG_TERM
    assert waiting.stage is ExitStage.EXIT_READY
    assert waiting.position_health is PositionHealth.PRESSURED
    assert "ST_BEARISH_THESIS_ESTABLISHED_AGAINST_OPEN_LONG" in waiting.reasons
    assert waiting.waiting_for == ("FRESH_LONG_EXIT_EXECUTION_EVENT",)

    sold = compose_position_exit_decision(
        state,
        structural,
        as_of=as_of,
        execution_event=_exit_event(as_of),
    )
    assert sold.action is DecisionAction.SELL
    assert sold.execution_event_consumed is True


def test_short_term_entry_uses_st_structure_and_established_bearish_state_arms_exit():
    as_of = pd.Timestamp("2026-01-05 12:00")
    state = _state(DecisionHorizon.SHORT_TERM)
    structural = _structural_snapshot(
        lt_direction=StructuralDirection.LONG,
        lt_thesis=ThesisState.INTACT,
        st_direction=StructuralDirection.SHORT,
        st_thesis=ThesisState.INTACT,
        relation=HorizonRelation.COUNTER_REACTION,
    )

    waiting = compose_position_exit_decision(state, structural, as_of=as_of)
    assert waiting.action is DecisionAction.HOLD
    assert waiting.entry_horizon is DecisionHorizon.SHORT_TERM
    assert waiting.stage is ExitStage.EXIT_READY
    assert waiting.execution.state is ExitExecutionState.ABSENT
    assert waiting.waiting_for == ("FRESH_LONG_EXIT_EXECUTION_EVENT",)

    confirmed = compose_position_exit_decision(
        state,
        structural,
        as_of=as_of,
        execution_event=_exit_event(as_of),
    )
    assert confirmed.action is DecisionAction.SELL
    assert confirmed.execution.state is ExitExecutionState.CONFIRMED
    assert confirmed.execution_event_consumed is True


def test_short_term_transition_down_sells_on_fresh_30m_short():
    as_of = pd.Timestamp("2026-01-05 12:00")
    state = _state(DecisionHorizon.SHORT_TERM)
    structural = _structural_snapshot(
        st_direction=StructuralDirection.LONG,
        st_thesis=ThesisState.TRANSITIONING,
        st_transition_target=StructuralDirection.SHORT,
        relation=HorizonRelation.STRUCTURAL_CONFLICT,
    )
    waiting = compose_position_exit_decision(state, structural, as_of=as_of)
    assert waiting.action is DecisionAction.HOLD
    assert waiting.stage is ExitStage.EXIT_READY
    assert waiting.execution.state is ExitExecutionState.ABSENT
    assert waiting.execution_event_consumed is False
    assert "ST_LONG_THESIS_TRANSITIONING_TOWARD_SHORT" in waiting.reasons

    sold = compose_position_exit_decision(
        state,
        structural,
        as_of=as_of,
        execution_event=_exit_event(as_of),
    )
    assert sold.action is DecisionAction.SELL
    assert sold.stage is ExitStage.EXIT_READY
    assert sold.execution_event_consumed is True


def test_intact_lt_still_holds_without_30m_short_event():
    as_of = pd.Timestamp("2026-01-05 12:00")
    decision = compose_position_exit_decision(
        _state(DecisionHorizon.LONG_TERM),
        _structural_snapshot(),
        as_of=as_of,
    )

    assert decision.stage is ExitStage.MONITOR
    assert decision.action is DecisionAction.HOLD
    assert decision.execution.state is ExitExecutionState.NOT_ARMED
    assert decision.execution_event_consumed is False


def test_open_long_sells_on_fresh_30m_short_even_if_lt_stays_intact():
    as_of = pd.Timestamp("2026-01-05 12:00")
    decision = compose_position_exit_decision(
        _state(DecisionHorizon.LONG_TERM),
        _structural_snapshot(),
        as_of=as_of,
        execution_event=_exit_event(as_of),
    )

    assert decision.action is DecisionAction.SELL
    assert decision.stage is ExitStage.EXIT_READY
    assert decision.execution.state is ExitExecutionState.CONFIRMED
    assert decision.execution_event_consumed is True
    assert "30M_SHORT_CONFIRM_AGAINST_OPEN_LONG" in decision.reasons


def test_30m_structure_bos_does_not_sell_an_intact_lt_pullback():
    as_of = pd.Timestamp("2026-01-05 12:00")
    decision = compose_position_exit_decision(
        _state(DecisionHorizon.LONG_TERM),
        _structural_snapshot(
            relation=HorizonRelation.PULLBACK,
            st_direction=StructuralDirection.SHORT,
            st_thesis=ThesisState.TRANSITIONING,
            st_transition_target=StructuralDirection.LONG,
        ),
        as_of=as_of,
        execution_event=_exit_event(as_of, reason="30M_STRUCTURE_BOS_CONFIRMED"),
    )

    assert decision.action is DecisionAction.HOLD
    assert decision.stage is ExitStage.MONITOR
    assert decision.execution_event_consumed is False
    assert "30M_SHORT_CONFIRM_AGAINST_OPEN_LONG" not in decision.reasons


def test_30m_structure_bos_can_still_execute_after_1h_already_armed():
    as_of = pd.Timestamp("2026-01-05 12:00")
    decision = compose_position_exit_decision(
        _state(DecisionHorizon.LONG_TERM),
        _structural_snapshot(
            st_direction=StructuralDirection.SHORT,
            st_thesis=ThesisState.INTACT,
            relation=HorizonRelation.COUNTER_REACTION,
        ),
        as_of=as_of,
        execution_event=_exit_event(as_of, reason="30M_STRUCTURE_BOS_CONFIRMED"),
    )

    assert decision.action is DecisionAction.SELL
    assert decision.stage is ExitStage.EXIT_READY
    assert decision.execution_event_consumed is True
    assert decision.execution.reasons == ("30M_STRUCTURE_BOS_CONFIRMED",)


def test_failed_30m_short_does_not_arm_or_sell_while_thesis_intact():
    as_of = pd.Timestamp("2026-01-05 12:00")
    decision = compose_position_exit_decision(
        _state(DecisionHorizon.LONG_TERM),
        _structural_snapshot(),
        as_of=as_of,
        execution_event=_exit_event(as_of, state=ExecutionTriggerState.FAILED),
    )

    assert decision.action is DecisionAction.HOLD
    assert decision.stage is ExitStage.MONITOR
    assert decision.execution.state is ExitExecutionState.NOT_ARMED
    assert decision.execution_event_consumed is False


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


def test_confirmed_turn8_exit_closes_position_and_clears_frozen_metadata():
    as_of = pd.Timestamp("2026-01-05 12:00")
    state = _state(DecisionHorizon.SHORT_TERM)
    decision = compose_position_exit_decision(
        state,
        _structural_snapshot(
            st_direction=StructuralDirection.SHORT,
            st_thesis=ThesisState.INTACT,
        ),
        as_of=as_of,
        execution_event=_exit_event(as_of),
    )

    transition = transition_position_exit_lifecycle(state, decision)
    assert transition.action is DecisionAction.SELL
    assert transition.current == TradeLifecycleState()
    assert transition.current.entry_metadata is None


def test_failed_exit_event_is_consumed_but_does_not_sell():
    as_of = pd.Timestamp("2026-01-05 12:00")
    decision = compose_position_exit_decision(
        _state(DecisionHorizon.SHORT_TERM),
        _structural_snapshot(
            st_direction=StructuralDirection.SHORT,
            st_thesis=ThesisState.INTACT,
        ),
        as_of=as_of,
        execution_event=_exit_event(as_of, state=ExecutionTriggerState.FAILED),
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
    )

    with pytest.raises(ValueError, match="frozen position metadata"):
        transition_position_exit_lifecycle(state, decision)
