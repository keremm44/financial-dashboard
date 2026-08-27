import pandas as pd

from financial_dashboard.decision_audit import (
    DecisionAction,
    DecisionEvent,
    DecisionSide,
    audit_decisions,
)


def _bars():
    timestamps = pd.date_range("2026-01-05 10:00", periods=4, freq="30min")
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "high": [101.0, 103.0, 104.0, 102.0],
            "low": [99.0, 100.0, 101.0, 98.0],
            "close": [100.0, 102.0, 103.0, 99.0],
        }
    )


def test_lifecycle_readiness_proxy_is_valid_but_remains_explicitly_marked():
    bars = _bars()
    trade_id = "trade:proxy"
    decisions = (
        DecisionEvent(
            timestamp=bars.iloc[0]["timestamp"],
            action=DecisionAction.BUY,
            side=DecisionSide.LONG,
            price=100.0,
            reasons=("AUDIT_PROXY_LONG_ENTRY_FROM_READY",),
            snapshot={
                "lifecycle_readiness_proxy": True,
                "execution": {"state": "ABSENT"},
                "trade_lifecycle": {
                    "previous_position": "FLAT",
                    "position_state": "OPEN",
                    "previous_exit_stage": None,
                    "exit_stage": "MONITOR",
                    "trade_id": trade_id,
                    "entry_as_of": bars.iloc[0]["timestamp"],
                    "requested_action": "BUY",
                    "action": "BUY",
                    "transition_reason": "LIFECYCLE_FLAT_ENTRY_EXECUTED",
                    "changed_position": True,
                },
                "long_exit": None,
            },
        ),
        DecisionEvent(
            timestamp=bars.iloc[1]["timestamp"],
            action=DecisionAction.HOLD,
            side=DecisionSide.LONG,
            price=102.0,
            snapshot={
                "lifecycle_readiness_proxy": True,
                "trade_lifecycle": {
                    "previous_position": "OPEN",
                    "position_state": "OPEN",
                    "previous_exit_stage": "MONITOR",
                    "exit_stage": "EXIT_WATCH",
                    "trade_id": trade_id,
                    "entry_as_of": bars.iloc[0]["timestamp"],
                    "requested_action": "WAIT",
                    "action": "HOLD",
                    "transition_reason": "LIFECYCLE_EXIT_STAGE_MONITOR_TO_EXIT_WATCH",
                    "changed_position": False,
                },
                "long_exit": {
                    "stage": "EXIT_WATCH",
                    "position_health": "PRESSURED",
                    "execution": {"state": "NOT_ARMED"},
                },
            },
        ),
        DecisionEvent(
            timestamp=bars.iloc[2]["timestamp"],
            action=DecisionAction.SELL,
            side=DecisionSide.LONG,
            price=103.0,
            reasons=("AUDIT_PROXY_LONG_EXIT_FROM_EXIT_READY",),
            snapshot={
                "lifecycle_readiness_proxy": True,
                "trade_lifecycle": {
                    "previous_position": "OPEN",
                    "position_state": "FLAT",
                    "previous_exit_stage": "EXIT_WATCH",
                    "exit_stage": None,
                    "trade_id": None,
                    "entry_as_of": None,
                    "requested_action": "NO_TRADE",
                    "action": "SELL",
                    "transition_reason": "LIFECYCLE_OPEN_EXIT_EXECUTED_CONFIRMED_EVENT",
                    "changed_position": True,
                },
                "long_exit": {
                    "stage": "EXIT_READY",
                    "position_health": "PRESSURED",
                    "execution": {"state": "ABSENT"},
                },
            },
        ),
    )

    report = audit_decisions(
        symbol="TEST",
        timeframe="30m",
        bars=bars,
        decisions=decisions,
    )

    assert report.lifecycle.lifecycle_valid is True
    assert report.lifecycle.violations == ()
    assert report.lifecycle.completed_cycles == 1
    assert report.metrics.completed_trades == 1
    assert report.unmatched_buy_events == 0
    assert report.unmatched_sell_events == 0
