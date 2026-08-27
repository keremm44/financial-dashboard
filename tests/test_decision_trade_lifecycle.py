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
    first = transition_trade_lifecycle(
        TradeLifecycleState(),
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
        exit_stage=ExitStage.MONITOR,
    )
    assert repeated.action is DecisionAction.HOLD
    assert repeated.current == first.current
    assert repeated.reason == "LIFECYCLE_REPEATED_BUY_SUPPRESSED"


def test_legacy_market_sell_cannot_close_open_long_without_exit_contract():
    opened = transition_trade_lifecycle(
        TradeLifecycleState(),
        _final(DecisionAction.BUY),
        as_of=pd.Timestamp("2026-01-05 10:00"),
    ).current

    legacy_sell = transition_trade_lifecycle(
        opened,
        _final(DecisionAction.SELL),
        as_of=pd.Timestamp("2026-01-05 10:30"),
        exit_stage=ExitStage.MONITOR,
    )

    assert legacy_sell.action is DecisionAction.HOLD
    assert legacy_sell.current.position is PositionState.OPEN
    assert legacy_sell.reason == "LIFECYCLE_LEGACY_SELL_IGNORED_BY_LONG_EXIT_CONTRACT"


def test_exit_stage_matures_without_selling_until_fresh_exit_is_confirmed():
    opened = transition_trade_lifecycle(
        TradeLifecycleState(),
        _final(DecisionAction.BUY),
        as_of=pd.Timestamp("2026-01-05 10:00"),
    ).current

    watch = transition_trade_lifecycle(
        opened,
        _final(DecisionAction.NO_TRADE),
        as_of=pd.Timestamp("2026-01-05 11:00"),
        exit_stage=ExitStage.EXIT_WATCH,
    )
    assert watch.action is DecisionAction.HOLD
    assert watch.current.exit_stage is ExitStage.EXIT_WATCH

    ready = transition_trade_lifecycle(
        watch.current,
        _final(DecisionAction.NO_TRADE),
        as_of=pd.Timestamp("2026-01-05 12:00"),
        exit_stage=ExitStage.EXIT_READY,
    )
    assert ready.action is DecisionAction.HOLD
    assert ready.current.exit_stage is ExitStage.EXIT_READY

    closed = transition_trade_lifecycle(
        ready.current,
        _final(DecisionAction.NO_TRADE),
        as_of=pd.Timestamp("2026-01-05 12:30"),
        exit_stage=ExitStage.EXIT_READY,
        exit_execution_confirmed=True,
    )
    assert closed.action is DecisionAction.SELL
    assert closed.current == TradeLifecycleState()
    assert closed.reason == "LIFECYCLE_OPEN_EXIT_EXECUTED_CONFIRMED_EVENT"


def test_exit_execution_cannot_bypass_exit_ready():
    opened = transition_trade_lifecycle(
        TradeLifecycleState(),
        _final(DecisionAction.BUY),
        as_of=pd.Timestamp("2026-01-05 10:00"),
    ).current

    with pytest.raises(ValueError, match="EXIT_READY"):
        transition_trade_lifecycle(
            opened,
            _final(DecisionAction.NO_TRADE),
            as_of=pd.Timestamp("2026-01-05 10:30"),
            exit_stage=ExitStage.MONITOR,
            exit_execution_confirmed=True,
        )


def test_flat_sell_is_suppressed_and_exit_confirmation_is_invalid_while_flat():
    state = TradeLifecycleState()
    sell = transition_trade_lifecycle(
        state,
        _final(DecisionAction.SELL),
        as_of=pd.Timestamp("2026-01-05 14:30"),
    )
    assert sell.action is DecisionAction.WAIT
    assert sell.current.position is PositionState.FLAT
    assert sell.reason == "LIFECYCLE_FLAT_SELL_SUPPRESSED"

    with pytest.raises(ValueError, match="FLAT"):
        transition_trade_lifecycle(
            state,
            _final(DecisionAction.WAIT),
            as_of=pd.Timestamp("2026-01-05 15:00"),
            exit_execution_confirmed=True,
        )


def test_execution_actions_alternate_when_exit_confirmation_is_explicit():
    state = TradeLifecycleState()
    emitted = []

    entry = transition_trade_lifecycle(
        state,
        _final(DecisionAction.BUY),
        as_of=pd.Timestamp("2026-01-05 10:00"),
    )
    state = entry.current
    emitted.append(entry.action)

    repeated = transition_trade_lifecycle(
        state,
        _final(DecisionAction.BUY),
        as_of=pd.Timestamp("2026-01-05 10:30"),
        exit_stage=ExitStage.MONITOR,
    )
    assert repeated.action is DecisionAction.HOLD
    state = repeated.current

    exit_one = transition_trade_lifecycle(
        state,
        _final(DecisionAction.NO_TRADE),
        as_of=pd.Timestamp("2026-01-05 11:00"),
        exit_stage=ExitStage.EXIT_READY,
        exit_execution_confirmed=True,
    )
    emitted.append(exit_one.action)
    state = exit_one.current

    entry_two = transition_trade_lifecycle(
        state,
        _final(DecisionAction.BUY),
        as_of=pd.Timestamp("2026-01-05 11:30"),
    )
    emitted.append(entry_two.action)
    state = entry_two.current

    exit_two = transition_trade_lifecycle(
        state,
        _final(DecisionAction.NO_TRADE),
        as_of=pd.Timestamp("2026-01-05 12:00"),
        exit_stage=ExitStage.EXIT_READY,
        exit_execution_confirmed=True,
    )
    emitted.append(exit_two.action)

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
