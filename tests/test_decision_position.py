from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.composer import (
    ActionPolicy,
    ActionSide,
    DecisionAction,
    compose_position_decision,
)
from financial_dashboard.decision.eligibility import EligibilityAssessment, EligibilityState
from financial_dashboard.decision.execution import ExecutionTriggerAssessment, ExecutionTriggerState
from financial_dashboard.decision.historical_stream import (
    HistoricalDecisionStreamConfig,
    _advance_position,
)
from financial_dashboard.decision.position import (
    PositionContext,
    PositionSide,
    position_exit_candidate,
)
from financial_dashboard.decision.structural import (
    DecisionHorizon,
    StructuralAssessment,
    StructuralDirection,
    ThesisState,
)


AS_OF = pd.Timestamp("2026-08-25 12:00:00", tz="UTC")


def _structural(
    side: StructuralDirection,
    *,
    thesis: ThesisState = ThesisState.INTACT,
    transition_target: StructuralDirection | None = None,
) -> StructuralAssessment:
    state = (
        "STATE_BULLISH"
        if side is StructuralDirection.LONG
        else "STATE_BEARISH"
        if side is StructuralDirection.SHORT
        else "STATE_NEUTRAL"
    )
    return StructuralAssessment(
        horizon=DecisionHorizon.SHORT_TERM,
        authority_timeframe="1h",
        direction=side,
        thesis_state=thesis,
        native_state=state,
        transition_target=transition_target,
        data_quality=ContextDataQuality.VALID,
        authority_as_of=AS_OF,
        protected_high=None,
        protected_low=None,
        weak_high=None,
        weak_low=None,
        source_refs=(),
        reasons=("TEST_STRUCTURE",),
    )


def _eligibility(state: EligibilityState = EligibilityState.ELIGIBLE) -> EligibilityAssessment:
    if state is EligibilityState.BLOCKED:
        return EligibilityAssessment(
            state,
            ("HARD_GATE_ACTIVE",),
            ("OPPORTUNITY_NONE", "VOLATILITY_SHOCK"),
            (),
        )
    if state is EligibilityState.WAITING:
        return EligibilityAssessment(state, ("WAIT",), (), ("TIMING",))
    return EligibilityAssessment(state, ("OK",), (), ())


def _execution(
    side: StructuralDirection,
    state: ExecutionTriggerState,
) -> ExecutionTriggerAssessment:
    return ExecutionTriggerAssessment(
        state=state,
        side=side,
        timeframe="30m",
        reasons=(f"TEST_EXECUTION:{state.value}",),
        source_refs=(),
    )


def test_position_context_is_explicit_and_does_not_invent_entry_metadata() -> None:
    assert PositionContext.flat().side is PositionSide.FLAT
    assert PositionContext.long().side is PositionSide.LONG
    assert PositionContext.short().side is PositionSide.SHORT

    with pytest.raises(ValueError):
        PositionContext(PositionSide.FLAT, opened_at=AS_OF)
    with pytest.raises(ValueError):
        PositionContext.long(entry_price=0.0)


def test_flat_short_market_side_still_cannot_bypass_cash_equity_entry_policy() -> None:
    decision = compose_position_decision(
        _structural(StructuralDirection.SHORT),
        eligibility=_eligibility(),
        execution=_execution(StructuralDirection.SHORT, ExecutionTriggerState.CONFIRMED),
        policy=ActionPolicy(permitted_sides=(StructuralDirection.LONG,)),
        position=PositionContext.flat(),
    )

    assert decision.action is DecisionAction.NO_TRADE
    assert decision.action_side is ActionSide.NONE
    assert decision.position_before is PositionSide.FLAT
    assert decision.position_after is PositionSide.FLAT
    assert "ACTION_SIDE_NOT_PERMITTED:SHORT" in decision.blockers


def test_open_long_does_not_emit_duplicate_buy_when_long_thesis_remains_intact() -> None:
    decision = compose_position_decision(
        _structural(StructuralDirection.LONG),
        eligibility=_eligibility(),
        execution=_execution(StructuralDirection.LONG, ExecutionTriggerState.CONFIRMED),
        position=PositionContext.long(opened_at=AS_OF, entry_price=100.0),
    )

    assert decision.action is DecisionAction.HOLD
    assert decision.action_side is ActionSide.LONG
    assert decision.position_before is PositionSide.LONG
    assert decision.position_after is PositionSide.LONG


def test_open_long_exit_is_not_blocked_by_short_entry_policy_or_fresh_entry_gates() -> None:
    structural = _structural(StructuralDirection.SHORT)
    assert position_exit_candidate(structural, PositionContext.long()) is StructuralDirection.SHORT

    decision = compose_position_decision(
        structural,
        eligibility=_eligibility(EligibilityState.BLOCKED),
        execution=_execution(StructuralDirection.SHORT, ExecutionTriggerState.CONFIRMED),
        policy=ActionPolicy(permitted_sides=(StructuralDirection.LONG,)),
        position=PositionContext.long(opened_at=AS_OF, entry_price=100.0),
    )

    assert decision.action is DecisionAction.SELL
    assert decision.action_side is ActionSide.NONE
    assert decision.position_before is PositionSide.LONG
    assert decision.position_after is PositionSide.FLAT
    assert not decision.blockers
    assert "POSITION_LONG_EXIT_CONFIRMED" in decision.reasons


def test_transitioning_long_only_monitors_exit_until_fresh_30m_event_exists() -> None:
    structural = _structural(
        StructuralDirection.LONG,
        thesis=ThesisState.TRANSITIONING,
        transition_target=StructuralDirection.SHORT,
    )
    position = PositionContext.long()
    assert position_exit_candidate(structural, position) is StructuralDirection.SHORT

    waiting = compose_position_decision(
        structural,
        eligibility=_eligibility(EligibilityState.WAITING),
        execution=_execution(StructuralDirection.SHORT, ExecutionTriggerState.ABSENT),
        position=position,
    )
    assert waiting.action is DecisionAction.HOLD
    assert waiting.position_after is PositionSide.LONG
    assert waiting.waiting_for == ("FRESH_POSITION_EXIT_EVENT",)

    exit_decision = compose_position_decision(
        structural,
        eligibility=_eligibility(EligibilityState.WAITING),
        execution=_execution(StructuralDirection.SHORT, ExecutionTriggerState.CONFIRMED),
        position=position,
    )
    assert exit_decision.action is DecisionAction.SELL
    assert exit_decision.position_after is PositionSide.FLAT


def test_invalidated_long_thesis_activates_exit_monitoring_without_declaring_short_thesis() -> None:
    structural = _structural(
        StructuralDirection.UNRESOLVED,
        thesis=ThesisState.INVALIDATED,
    )
    position = PositionContext.long()

    assert position_exit_candidate(structural, position) is StructuralDirection.SHORT
    decision = compose_position_decision(
        structural,
        eligibility=_eligibility(EligibilityState.BLOCKED),
        execution=_execution(StructuralDirection.SHORT, ExecutionTriggerState.ABSENT),
        position=position,
    )
    assert decision.market_side is StructuralDirection.UNRESOLVED
    assert decision.action is DecisionAction.HOLD
    assert decision.waiting_for == ("FRESH_POSITION_EXIT_EVENT",)


def test_position_aware_historical_state_advances_only_on_filled_actions() -> None:
    with pytest.raises(ValueError):
        HistoricalDecisionStreamConfig(
            position_aware=True,
            readiness_position_proxy=True,
        )

    flat = PositionContext.flat()
    buy_assessment = SimpleNamespace(
        as_of=AS_OF,
        final=SimpleNamespace(action=DecisionAction.BUY),
    )
    long_position = _advance_position(flat, buy_assessment, price=101.5)
    assert long_position.side is PositionSide.LONG
    assert long_position.opened_at == AS_OF
    assert long_position.entry_price == 101.5

    hold_assessment = SimpleNamespace(
        as_of=AS_OF + pd.Timedelta(hours=1),
        final=SimpleNamespace(action=DecisionAction.HOLD),
    )
    assert _advance_position(long_position, hold_assessment, price=102.0) == long_position

    sell_assessment = SimpleNamespace(
        as_of=AS_OF + pd.Timedelta(hours=2),
        final=SimpleNamespace(action=DecisionAction.SELL),
    )
    assert _advance_position(long_position, sell_assessment, price=103.0).side is PositionSide.FLAT
