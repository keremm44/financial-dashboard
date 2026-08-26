from types import SimpleNamespace

import pandas as pd
import pytest

from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.lifecycle import (
    ExitStage,
    PositionState,
    TradeLifecycleState,
    transition_trade_lifecycle,
)


def _final(action: DecisionAction):
    return SimpleNamespace(action=action)


def test_flat_buy_opens_one_trade_and_repeated_buy_becomes_hold():
    state = TradeLifecycleState()
    first = transition_trade_lifecycle(
        state,
        _final(DecisionAction.BUY),
        as_of=pd.Timestamp("2026-01-05 10:00"),
    )
    assert first.action is DecisionAction.BUY
    assert first.current.position is PositionState.OPEN
    assert first.current.exit_stage is ExitStage.MONITOR

    repeated = transition_trade_lifecycle(
        first.current,
        _final(DecisionAction.BUY),
        as_of=pd.Timestamp("2026-01-05 10:30"),
    )
    assert repeated.action is DecisionAction.HOLD
    assert repeated.current == first.current
    assert repeated.reason == "LIFECYCLE_REPEATED_BUY_SUPPRESSED"


def test_open_sell_closes_once_and_flat_sell_is_suppressed():
    opened = transition_trade_lifecycle(
        TradeLifecycleState(),
        _final(DecisionAction.BUY),
        as_of=pd.Timestamp("2026-01-05 10:00"),
    )
    closed = transition_trade_lifecycle(
        opened.current,
        _final(DecisionAction.SELL),
        as_of=pd.Timestamp("2026-01-05 14:00"),
    )
    assert closed.action is DecisionAction.SELL
    assert closed.current == TradeLifecycleState()

    repeated_sell = transition_trade_lifecycle(
        closed.current,
        _final(DecisionAction.SELL),
        as_of=pd.Timestamp("2026-01-05 14:30"),
    )
    assert repeated_sell.action is DecisionAction.WAIT
    assert repeated_sell.current.position is PositionState.FLAT
    assert repeated_sell.reason == "LIFECYCLE_FLAT_SELL_SUPPRESSED"


def test_open_entry_path_states_surface_as_hold():
    opened = transition_trade_lifecycle(
        TradeLifecycleState(),
        _final(DecisionAction.BUY),
        as_of=pd.Timestamp("2026-01-05 10:00"),
    ).current

    for action in (
        DecisionAction.WAIT,
        DecisionAction.READY,
        DecisionAction.NO_TRADE,
        DecisionAction.HOLD,
    ):
        transition = transition_trade_lifecycle(
            opened,
            _final(action),
            as_of=pd.Timestamp("2026-01-05 10:30"),
        )
        assert transition.action is DecisionAction.HOLD
        assert transition.current == opened


def test_execution_actions_must_alternate_buy_sell():
    requested = (
        DecisionAction.BUY,
        DecisionAction.BUY,
        DecisionAction.BUY,
        DecisionAction.SELL,
        DecisionAction.SELL,
        DecisionAction.BUY,
        DecisionAction.SELL,
    )
    state = TradeLifecycleState()
    emitted = []
    for index, action in enumerate(requested):
        transition = transition_trade_lifecycle(
            state,
            _final(action),
            as_of=pd.Timestamp("2026-01-05 10:00") + pd.Timedelta(minutes=30 * index),
        )
        state = transition.current
        if transition.action in {DecisionAction.BUY, DecisionAction.SELL}:
            emitted.append(transition.action)

    assert emitted == [
        DecisionAction.BUY,
        DecisionAction.SELL,
        DecisionAction.BUY,
        DecisionAction.SELL,
    ]


def test_flat_state_rejects_open_trade_metadata():
    with pytest.raises(ValueError, match="FLAT lifecycle state"):
        TradeLifecycleState(
            position=PositionState.FLAT,
            exit_stage=ExitStage.MONITOR,
            trade_id="invalid",
            entry_as_of=pd.Timestamp("2026-01-05 10:00"),
        )
