from types import SimpleNamespace

import pandas as pd
import pytest

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.execution import ExecutionTriggerEvent, ExecutionTriggerState
from financial_dashboard.decision.lifecycle import ExitStage
from financial_dashboard.decision.structural import HorizonRelation, StructuralDirection, ThesisState
from financial_dashboard.decision.trade_exit import (
    ExitExecutionState,
    PositionHealth,
    assess_long_exit_execution,
    assess_long_position_exit,
)


def _structural_snapshot(
    *,
    lt_direction=StructuralDirection.LONG,
    lt_thesis=ThesisState.INTACT,
    relation=HorizonRelation.ALIGNED,
    transition_target=None,
    quality=ContextDataQuality.VALID,
):
    lt = SimpleNamespace(
        direction=lt_direction,
        thesis_state=lt_thesis,
        transition_target=transition_target,
        data_quality=quality,
        source_refs=(),
    )
    st = SimpleNamespace(source_refs=())
    return SimpleNamespace(long_term=lt, short_term=st, relation=relation)


def _exit_event(*, as_of, side=StructuralDirection.SHORT, state=ExecutionTriggerState.CONFIRMED):
    return ExecutionTriggerEvent(
        state=state,
        side=side,
        timeframe="30m",
        observed_at=pd.Timestamp(as_of),
        available_at=pd.Timestamp(as_of),
        reason="TEST_FRESH_EXIT_EVENT",
        source_refs=(),
    )


def test_lt_intact_st_counter_reaction_is_protected_hold_not_exit_ready():
    assessment = assess_long_position_exit(
        _structural_snapshot(relation=HorizonRelation.COUNTER_REACTION)
    )

    assert assessment.stage is ExitStage.MONITOR
    assert assessment.position_health is PositionHealth.PROTECTED
    assert assessment.reasons == ("LT_LONG_INTACT_ST_COUNTER_REACTION",)


def test_lt_transition_toward_short_is_watch_not_automatic_sell():
    assessment = assess_long_position_exit(
        _structural_snapshot(
            lt_direction=StructuralDirection.LONG,
            lt_thesis=ThesisState.TRANSITIONING,
            transition_target=StructuralDirection.SHORT,
            relation=HorizonRelation.EARLY_TRANSITION,
        )
    )

    assert assessment.stage is ExitStage.EXIT_WATCH
    assert assessment.position_health is PositionHealth.PRESSURED
    assert assessment.waiting_for == ("LT_TRANSITION_TO_RESOLVE",)


def test_established_lt_bearish_thesis_arms_long_exit_but_does_not_sell_by_itself():
    assessment = assess_long_position_exit(
        _structural_snapshot(
            lt_direction=StructuralDirection.SHORT,
            lt_thesis=ThesisState.INTACT,
            relation=HorizonRelation.COUNTER_REACTION,
        )
    )
    execution = assess_long_exit_execution(
        assessment,
        as_of=pd.Timestamp("2026-01-05 12:00"),
        event=None,
    )

    assert assessment.stage is ExitStage.EXIT_READY
    assert execution.state is ExitExecutionState.ABSENT
    assert execution.waiting_for == ("FRESH_LONG_EXIT_EXECUTION_EVENT",)


def test_fresh_short_exit_event_executes_only_after_exit_ready():
    as_of = pd.Timestamp("2026-01-05 12:00")
    protected = assess_long_position_exit(
        _structural_snapshot(relation=HorizonRelation.COUNTER_REACTION)
    )
    premature = assess_long_exit_execution(
        protected,
        as_of=as_of,
        event=_exit_event(as_of=as_of),
    )
    assert premature.state is ExitExecutionState.NOT_ARMED

    ready = assess_long_position_exit(
        _structural_snapshot(
            lt_direction=StructuralDirection.SHORT,
            lt_thesis=ThesisState.INTACT,
        )
    )
    confirmed = assess_long_exit_execution(
        ready,
        as_of=as_of,
        event=_exit_event(as_of=as_of),
    )
    assert confirmed.state is ExitExecutionState.CONFIRMED
    assert confirmed.waiting_for == ()


def test_exit_event_must_be_short_side_fresh_and_same_timeframe():
    ready = assess_long_position_exit(
        _structural_snapshot(
            lt_direction=StructuralDirection.SHORT,
            lt_thesis=ThesisState.INTACT,
        )
    )
    as_of = pd.Timestamp("2026-01-05 12:00")

    with pytest.raises(ValueError, match="SHORT-side"):
        assess_long_exit_execution(
            ready,
            as_of=as_of,
            event=_exit_event(as_of=as_of, side=StructuralDirection.LONG),
        )

    stale = ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=StructuralDirection.SHORT,
        timeframe="30m",
        observed_at=pd.Timestamp("2026-01-05 11:30"),
        available_at=pd.Timestamp("2026-01-05 11:30"),
        reason="STALE_EXIT",
        source_refs=(),
    )
    with pytest.raises(ValueError, match="fresh"):
        assess_long_exit_execution(
            ready,
            as_of=as_of,
            event=stale,
        )


def test_missing_lt_authority_is_watch_unknown_not_forced_sell():
    assessment = assess_long_position_exit(
        _structural_snapshot(quality=ContextDataQuality.UNAVAILABLE)
    )

    assert assessment.stage is ExitStage.EXIT_WATCH
    assert assessment.position_health is PositionHealth.UNKNOWN
    assert assessment.reasons == ("LT_STRUCTURE_DATA_UNAVAILABLE",)
