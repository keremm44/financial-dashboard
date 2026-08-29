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
    st_direction=StructuralDirection.LONG,
    st_thesis=ThesisState.INTACT,
    st_transition_target=None,
):
    lt = SimpleNamespace(
        direction=lt_direction,
        thesis_state=lt_thesis,
        transition_target=transition_target,
        data_quality=quality,
        source_refs=(),
    )
    st = SimpleNamespace(
        direction=st_direction,
        thesis_state=st_thesis,
        transition_target=st_transition_target,
        data_quality=quality,
        source_refs=(),
    )
    return SimpleNamespace(long_term=lt, short_term=st, relation=relation)


def _exit_event(*, as_of, side=StructuralDirection.SHORT, state=ExecutionTriggerState.CONFIRMED):
    return ExecutionTriggerEvent(
        state=state,
        side=side,
        timeframe="1h",
        observed_at=pd.Timestamp(as_of),
        available_at=pd.Timestamp(as_of),
        reason="TEST_FRESH_1H_EXIT_EVENT",
        source_refs=(),
    )


def test_lt_intact_st_counter_reaction_arms_exit_without_daily_bos():
    assessment = assess_long_position_exit(
        _structural_snapshot(
            relation=HorizonRelation.COUNTER_REACTION,
            st_direction=StructuralDirection.SHORT,
            st_thesis=ThesisState.INTACT,
        )
    )
    assert assessment.stage is ExitStage.EXIT_READY
    assert assessment.position_health is PositionHealth.PRESSURED
    assert assessment.reasons == ("ST_BEARISH_THESIS_ESTABLISHED_AGAINST_OPEN_LONG",)


def test_lt_intact_1h_transition_down_does_not_arm_lt_exit():
    assessment = assess_long_position_exit(
        _structural_snapshot(
            st_direction=StructuralDirection.LONG,
            st_thesis=ThesisState.TRANSITIONING,
            st_transition_target=StructuralDirection.SHORT,
        )
    )
    assert assessment.stage is ExitStage.MONITOR
    assert assessment.reasons == ("LT_LONG_INTACT_ST_ALIGNED",)


def test_lt_intact_st_pullback_recovering_toward_lt_stays_monitor():
    assessment = assess_long_position_exit(
        _structural_snapshot(
            relation=HorizonRelation.PULLBACK,
            st_direction=StructuralDirection.SHORT,
            st_thesis=ThesisState.TRANSITIONING,
            st_transition_target=StructuralDirection.LONG,
        )
    )
    assert assessment.stage is ExitStage.MONITOR
    assert assessment.position_health is PositionHealth.PROTECTED


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


def test_fresh_short_exit_event_executes_only_after_exit_ready():
    as_of = pd.Timestamp("2026-01-05 12:00")
    protected = assess_long_position_exit(_structural_snapshot())
    premature = assess_long_exit_execution(protected, as_of=as_of, event=_exit_event(as_of=as_of))
    assert premature.state is ExitExecutionState.NOT_ARMED

    ready = assess_long_position_exit(
        _structural_snapshot(lt_direction=StructuralDirection.SHORT, lt_thesis=ThesisState.INTACT)
    )
    confirmed = assess_long_exit_execution(ready, as_of=as_of, event=_exit_event(as_of=as_of))
    assert confirmed.state is ExitExecutionState.CONFIRMED


def test_exit_event_must_be_short_side_and_not_future():
    ready = assess_long_position_exit(
        _structural_snapshot(lt_direction=StructuralDirection.SHORT, lt_thesis=ThesisState.INTACT)
    )
    as_of = pd.Timestamp("2026-01-05 12:00")
    with pytest.raises(ValueError, match="SHORT-side"):
        assess_long_exit_execution(
            ready,
            as_of=as_of,
            event=_exit_event(as_of=as_of, side=StructuralDirection.LONG),
        )

    prior_event = _exit_event(as_of=pd.Timestamp("2026-01-05 11:00"))
    assert assess_long_exit_execution(ready, as_of=as_of, event=prior_event).state is ExitExecutionState.CONFIRMED

    future = _exit_event(as_of=pd.Timestamp("2026-01-05 13:00"))
    with pytest.raises(ValueError, match="future-unavailable|future-observed"):
        assess_long_exit_execution(ready, as_of=as_of, event=future)


def test_missing_lt_authority_is_watch_unknown_not_forced_sell():
    assessment = assess_long_position_exit(_structural_snapshot(quality=ContextDataQuality.UNAVAILABLE))
    assert assessment.stage is ExitStage.EXIT_WATCH
    assert assessment.position_health is PositionHealth.UNKNOWN
    assert assessment.reasons == ("LT_STRUCTURE_DATA_UNAVAILABLE",)
