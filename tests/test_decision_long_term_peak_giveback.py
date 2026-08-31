from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.exit import compose_position_exit_decision
from financial_dashboard.decision.lifecycle import (
    ExitStage,
    PositionState,
    TradeLifecycleState,
    transition_trade_lifecycle,
)
from financial_dashboard.decision.structural import (
    DecisionHorizon,
    HorizonRelation,
    StructuralDirection,
    ThesisState,
)
from financial_dashboard.decision.trade_exit import ExitExecutionState


def _metadata(as_of: pd.Timestamp, *, horizon: DecisionHorizon, entry_price: float = 100.0):
    return SimpleNamespace(
        entry_as_of=as_of,
        entry_price=entry_price,
        entry_horizon=horizon,
    )


def _state(*, horizon: DecisionHorizon, peak_price: float = 110.0) -> TradeLifecycleState:
    as_of = pd.Timestamp("2026-01-02 10:00:00+03:00")
    return TradeLifecycleState(
        position=PositionState.OPEN,
        exit_stage=ExitStage.MONITOR,
        trade_id="trade:test",
        entry_as_of=as_of,
        entry_metadata=_metadata(as_of, horizon=horizon),
        peak_price=peak_price,
    )


def _side(direction: StructuralDirection, thesis_state: ThesisState = ThesisState.INTACT):
    return SimpleNamespace(
        data_quality=ContextDataQuality.VALID,
        direction=direction,
        thesis_state=thesis_state,
        transition_target=None,
        source_refs=(),
    )


def _snapshot(*, short_direction: StructuralDirection = StructuralDirection.LONG):
    relation = (
        HorizonRelation.ALIGNED
        if short_direction is StructuralDirection.LONG
        else HorizonRelation.COUNTER_REACTION
    )
    return SimpleNamespace(
        long_term=_side(StructuralDirection.LONG),
        short_term=_side(short_direction),
        relation=relation,
    )


def test_open_position_peak_is_monotonic() -> None:
    state = _state(horizon=DecisionHorizon.LONG_TERM, peak_price=110.0)
    hold = SimpleNamespace(action=DecisionAction.HOLD)

    first = transition_trade_lifecycle(
        state,
        hold,
        as_of=pd.Timestamp("2026-01-02 11:00:00+03:00"),
        position_peak_price=112.0,
    )
    second = transition_trade_lifecycle(
        first.current,
        hold,
        as_of=pd.Timestamp("2026-01-02 12:00:00+03:00"),
        position_peak_price=108.0,
    )

    assert first.current.peak_price == 112.0
    assert second.current.peak_price == 112.0


def test_long_term_short_term_counter_move_waits_while_giveback_is_small() -> None:
    state = _state(horizon=DecisionHorizon.LONG_TERM, peak_price=110.0)

    decision = compose_position_exit_decision(
        state,
        _snapshot(short_direction=StructuralDirection.SHORT),
        as_of=pd.Timestamp("2026-01-03 10:00:00+03:00"),
        current_price=107.0,
    )

    assert decision.action is DecisionAction.HOLD
    assert decision.stage is ExitStage.EXIT_WATCH
    assert decision.peak_giveback_pct == pytest.approx((110.0 - 107.0) / 110.0 * 100.0)
    assert "LT_PRIMARY_LONG_THESIS_STILL_INTACT" in decision.reasons
    assert "LT_PRIMARY_STRUCTURE_DETERIORATION_OR_PEAK_PROTECTION" in decision.waiting_for


def test_long_term_four_pct_giveback_arms_confirmation_zone() -> None:
    state = _state(horizon=DecisionHorizon.LONG_TERM, peak_price=110.0)

    decision = compose_position_exit_decision(
        state,
        _snapshot(),
        as_of=pd.Timestamp("2026-01-03 10:00:00+03:00"),
        current_price=105.6,
    )

    assert decision.action is DecisionAction.HOLD
    assert decision.stage is ExitStage.EXIT_READY
    assert decision.peak_giveback_pct == pytest.approx(4.0)
    assert not decision.risk_cap_exit
    assert "LT_PEAK_GIVEBACK_CONFIRMATION_ZONE" in decision.reasons
    assert decision.execution.state is ExitExecutionState.ABSENT


def test_long_term_five_pct_giveback_forces_exit_without_more_confirmation() -> None:
    state = _state(horizon=DecisionHorizon.LONG_TERM, peak_price=110.0)

    decision = compose_position_exit_decision(
        state,
        _snapshot(),
        as_of=pd.Timestamp("2026-01-03 10:00:00+03:00"),
        current_price=104.4,
    )

    assert decision.action is DecisionAction.SELL
    assert decision.stage is ExitStage.EXIT_READY
    assert decision.peak_giveback_pct is not None
    assert decision.peak_giveback_pct > 5.0
    assert decision.risk_cap_exit
    assert not decision.execution_event_consumed
    assert decision.execution.state is ExitExecutionState.CONFIRMED
    assert "LT_PEAK_GIVEBACK_HARD_CAP_REACHED" in decision.reasons
    assert "LT_PEAK_GIVEBACK_HARD_CAP_EXECUTION" in decision.reasons


def test_peak_giveback_cap_does_not_apply_to_short_term_position() -> None:
    state = _state(horizon=DecisionHorizon.SHORT_TERM, peak_price=110.0)

    decision = compose_position_exit_decision(
        state,
        _snapshot(),
        as_of=pd.Timestamp("2026-01-03 10:00:00+03:00"),
        current_price=99.0,
    )

    assert decision.action is DecisionAction.HOLD
    assert decision.stage is ExitStage.MONITOR
    assert decision.peak_giveback_pct == pytest.approx(10.0)
    assert not decision.risk_cap_exit
    assert "LT_PEAK_GIVEBACK_HARD_CAP_REACHED" not in decision.reasons
