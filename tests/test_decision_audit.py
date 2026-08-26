from __future__ import annotations

import pandas as pd
import pytest

from financial_dashboard.decision_audit import (
    DecisionAction,
    DecisionAuditConfig,
    DecisionEvent,
    DecisionSide,
    audit_decisions,
    render_text,
)


def _bars() -> pd.DataFrame:
    timestamps = pd.date_range("2026-01-01", periods=8, freq="30min")
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [100, 100, 100, 102, 106, 108, 111, 110],
            "high": [101, 102, 104, 108, 110, 109, 112, 111],
            "low": [99, 98, 97, 101, 105, 107, 108, 106],
            "close": [100, 100, 102, 106, 108, 108, 111, 107],
        }
    )


def _lifecycle_snapshot(
    *,
    previous: str,
    current: str,
    action: str,
    exit_stage: str | None,
    trade_id: str | None,
    execution_state: str | None = None,
    long_exit_stage: str | None = None,
    position_health: str | None = None,
    exit_execution_state: str | None = None,
) -> dict:
    snapshot = {
        "trade_lifecycle": {
            "previous_position": previous,
            "position_state": current,
            "previous_exit_stage": None,
            "exit_stage": exit_stage,
            "trade_id": trade_id,
            "entry_as_of": None,
            "requested_action": action,
            "action": action,
            "transition_reason": "TEST_TRANSITION",
            "changed_position": previous != current,
        }
    }
    if execution_state is not None:
        snapshot["execution"] = {"state": execution_state}
    if long_exit_stage is not None:
        snapshot["long_exit"] = {
            "stage": long_exit_stage,
            "position_health": position_health,
            "reasons": [],
            "waiting_for": [],
            "source_refs": [],
            "execution": None
            if exit_execution_state is None
            else {"state": exit_execution_state},
        }
    return snapshot


def test_audit_measures_entry_exit_regret_mae_mfe_and_capture() -> None:
    bars = _bars()
    decisions = (
        DecisionEvent(timestamp=bars.iloc[0]["timestamp"], action=DecisionAction.WAIT),
        DecisionEvent(
            timestamp=bars.iloc[1]["timestamp"],
            action=DecisionAction.BUY,
            side=DecisionSide.LONG,
            price=100.0,
            reasons=("LT_LONG_INTACT", "ST_TRIGGER_CONFIRMED"),
            source_lineage=("STRUCT:1D:42",),
            snapshot={"lt": {"direction": "LONG"}, "timing": "READY"},
        ),
        DecisionEvent(timestamp=bars.iloc[2]["timestamp"], action=DecisionAction.WAIT),
        DecisionEvent(timestamp=bars.iloc[3]["timestamp"], action=DecisionAction.READY),
        DecisionEvent(timestamp=bars.iloc[4]["timestamp"], action=DecisionAction.READY),
        DecisionEvent(
            timestamp=bars.iloc[5]["timestamp"],
            action=DecisionAction.SELL,
            side=DecisionSide.LONG,
            price=108.0,
            reasons=("EXIT_TRIGGER",),
            snapshot={"st": {"direction": "LONG"}, "exit": "CONFIRMED"},
        ),
    )
    report = audit_decisions(
        symbol="TEST",
        timeframe="30m",
        bars=bars,
        decisions=decisions,
        config=DecisionAuditConfig(extrema_lookback_bars=2, extrema_lookahead_bars=2),
    )

    assert report.metrics.completed_trades == 1
    trade = report.trades[0]
    assert trade.return_pct == pytest.approx(8.0)
    assert trade.mfe_pct == pytest.approx(10.0)
    assert trade.mae_pct == pytest.approx(-3.0)
    assert trade.move_capture_ratio == pytest.approx(0.8)

    assert trade.entry_local_low == pytest.approx(97.0)
    assert trade.entry_local_low_miss_pct == pytest.approx((100.0 - 97.0) / 97.0 * 100.0)
    assert trade.entry_early_bars == 1
    assert trade.entry_late_bars == 0
    assert trade.post_entry_additional_downside_pct == pytest.approx(-3.0)

    assert trade.exit_local_high == pytest.approx(112.0)
    assert trade.exit_early_bars == 1
    assert trade.exit_late_bars == 0
    assert trade.exit_peak_miss_pct == pytest.approx((112.0 - 108.0) / 112.0 * 100.0)
    assert trade.post_exit_missed_upside_pct == pytest.approx((112.0 - 108.0) / 108.0 * 100.0)
    assert trade.profit_giveback_pct == pytest.approx((110.0 - 108.0) / 110.0 * 100.0)

    assert trade.entry_reasons == ("LT_LONG_INTACT", "ST_TRIGGER_CONFIRMED")
    assert trade.entry_source_lineage == ("STRUCT:1D:42",)
    assert trade.entry_snapshot["lt"]["direction"] == "LONG"

    assert report.metrics.win_rate_pct == pytest.approx(100.0)
    assert report.metrics.average_move_capture_ratio_pct == pytest.approx(80.0)
    assert report.metrics.early_entry_cases == 1
    assert report.metrics.early_exit_cases == 1


def test_signal_stability_tracks_wait_ready_churn_and_ready_to_buy_delay() -> None:
    bars = _bars()
    decisions = (
        DecisionEvent(timestamp=bars.iloc[0]["timestamp"], action=DecisionAction.WAIT),
        DecisionEvent(timestamp=bars.iloc[1]["timestamp"], action=DecisionAction.READY),
        DecisionEvent(timestamp=bars.iloc[2]["timestamp"], action=DecisionAction.WAIT),
        DecisionEvent(timestamp=bars.iloc[3]["timestamp"], action=DecisionAction.READY),
        DecisionEvent(timestamp=bars.iloc[4]["timestamp"], action=DecisionAction.READY),
        DecisionEvent(timestamp=bars.iloc[5]["timestamp"], action=DecisionAction.BUY, price=108.0),
        DecisionEvent(timestamp=bars.iloc[6]["timestamp"], action=DecisionAction.HOLD),
        DecisionEvent(timestamp=bars.iloc[7]["timestamp"], action=DecisionAction.SELL, price=107.0),
    )
    report = audit_decisions(
        symbol="TEST",
        timeframe="30m",
        bars=bars,
        decisions=decisions,
    )
    stability = report.signal_stability

    assert stability.action_counts["WAIT"] == 2
    assert stability.action_counts["READY"] == 3
    assert stability.ready_to_wait_reversals == 1
    assert stability.wait_episode_count == 2
    assert stability.ready_episode_count == 2
    assert stability.average_ready_duration_bars == pytest.approx(1.5)
    assert stability.average_ready_to_buy_delay_bars == pytest.approx(2.0)


def test_missed_opportunity_audit_is_explicitly_opt_in() -> None:
    bars = _bars()
    report = audit_decisions(
        symbol="TEST",
        timeframe="30m",
        bars=bars,
        decisions=(),
        config=DecisionAuditConfig(meaningful_move_atr=None),
    )

    assert report.missed_opportunities == ()
    assert "Not evaluated" in render_text(report)


def test_open_trade_at_sample_end_is_censored_not_unmatched_buy() -> None:
    bars = _bars()
    decisions = (
        DecisionEvent(timestamp=bars.iloc[0]["timestamp"], action=DecisionAction.SELL, price=100.0),
        DecisionEvent(timestamp=bars.iloc[1]["timestamp"], action=DecisionAction.BUY, price=100.0),
    )
    report = audit_decisions(
        symbol="TEST",
        timeframe="30m",
        bars=bars,
        decisions=decisions,
    )

    assert report.metrics.completed_trades == 0
    assert report.unmatched_sell_events == 1
    assert report.unmatched_buy_events == 0
    assert len(report.censored_trades) == 1
    censored = report.censored_trades[0]
    assert censored.entry_time == bars.iloc[1]["timestamp"]
    assert censored.sample_end_time == bars.iloc[-1]["timestamp"]
    assert censored.bars_open == 7
    assert "RIGHT-CENSORED OPEN TRADES" in render_text(report)


def test_lifecycle_audit_validates_exit_maturity_and_measures_stage_churn() -> None:
    bars = _bars()
    trade_id = "trade:test"
    decisions = (
        DecisionEvent(
            timestamp=bars.iloc[0]["timestamp"],
            action=DecisionAction.WAIT,
            snapshot=_lifecycle_snapshot(
                previous="FLAT", current="FLAT", action="WAIT", exit_stage=None, trade_id=None
            ),
        ),
        DecisionEvent(
            timestamp=bars.iloc[1]["timestamp"],
            action=DecisionAction.BUY,
            side=DecisionSide.LONG,
            price=100.0,
            snapshot=_lifecycle_snapshot(
                previous="FLAT",
                current="OPEN",
                action="BUY",
                exit_stage="MONITOR",
                trade_id=trade_id,
                execution_state="CONFIRMED",
            ),
        ),
        DecisionEvent(
            timestamp=bars.iloc[2]["timestamp"],
            action=DecisionAction.HOLD,
            side=DecisionSide.LONG,
            snapshot=_lifecycle_snapshot(
                previous="OPEN",
                current="OPEN",
                action="HOLD",
                exit_stage="MONITOR",
                trade_id=trade_id,
                long_exit_stage="MONITOR",
                position_health="PROTECTED",
                exit_execution_state="NOT_ARMED",
            ),
        ),
        DecisionEvent(
            timestamp=bars.iloc[3]["timestamp"],
            action=DecisionAction.HOLD,
            side=DecisionSide.LONG,
            snapshot=_lifecycle_snapshot(
                previous="OPEN",
                current="OPEN",
                action="HOLD",
                exit_stage="EXIT_WATCH",
                trade_id=trade_id,
                long_exit_stage="EXIT_WATCH",
                position_health="PRESSURED",
                exit_execution_state="NOT_ARMED",
            ),
        ),
        DecisionEvent(
            timestamp=bars.iloc[4]["timestamp"],
            action=DecisionAction.HOLD,
            side=DecisionSide.LONG,
            snapshot=_lifecycle_snapshot(
                previous="OPEN",
                current="OPEN",
                action="HOLD",
                exit_stage="EXIT_READY",
                trade_id=trade_id,
                long_exit_stage="EXIT_READY",
                position_health="PRESSURED",
                exit_execution_state="ABSENT",
            ),
        ),
        DecisionEvent(
            timestamp=bars.iloc[5]["timestamp"],
            action=DecisionAction.SELL,
            side=DecisionSide.LONG,
            price=108.0,
            snapshot=_lifecycle_snapshot(
                previous="OPEN",
                current="FLAT",
                action="SELL",
                exit_stage=None,
                trade_id=None,
                long_exit_stage="EXIT_READY",
                position_health="PRESSURED",
                exit_execution_state="CONFIRMED",
            ),
        ),
    )

    report = audit_decisions(symbol="TEST", timeframe="30m", bars=bars, decisions=decisions)
    lifecycle = report.lifecycle

    assert lifecycle.metadata_events == len(decisions)
    assert lifecycle.lifecycle_valid is True
    assert lifecycle.violations == ()
    assert lifecycle.completed_cycles == 1
    assert lifecycle.censored_open_trades == 0
    assert lifecycle.hold_bars == 3
    assert lifecycle.protected_hold_bars == 1
    assert lifecycle.pressured_hold_bars == 2
    assert lifecycle.exit_watch_episode_count == 1
    assert lifecycle.exit_ready_episode_count == 1
    assert lifecycle.average_exit_watch_duration_bars == pytest.approx(1.0)
    assert lifecycle.average_exit_ready_duration_bars == pytest.approx(2.0)
    assert lifecycle.average_exit_ready_to_sell_delay_bars == pytest.approx(1.0)
    assert lifecycle.exit_watch_to_monitor_reversions == 0
    assert lifecycle.exit_ready_to_watch_reversions == 0


def test_lifecycle_audit_surfaces_invalid_sell_contract_instead_of_hiding_it() -> None:
    bars = _bars()
    trade_id = "trade:test"
    decisions = (
        DecisionEvent(
            timestamp=bars.iloc[1]["timestamp"],
            action=DecisionAction.BUY,
            price=100.0,
            snapshot=_lifecycle_snapshot(
                previous="FLAT",
                current="OPEN",
                action="BUY",
                exit_stage="MONITOR",
                trade_id=trade_id,
                execution_state="CONFIRMED",
            ),
        ),
        DecisionEvent(
            timestamp=bars.iloc[2]["timestamp"],
            action=DecisionAction.SELL,
            price=102.0,
            snapshot=_lifecycle_snapshot(
                previous="OPEN",
                current="FLAT",
                action="SELL",
                exit_stage=None,
                trade_id=None,
                long_exit_stage="MONITOR",
                position_health="HEALTHY",
                exit_execution_state="NOT_ARMED",
            ),
        ),
    )

    report = audit_decisions(symbol="TEST", timeframe="30m", bars=bars, decisions=decisions)
    assert report.lifecycle.lifecycle_valid is False
    assert any("SELL_WITHOUT_EXIT_READY" in item for item in report.lifecycle.violations)
    assert any("SELL_WITHOUT_CONFIRMED_EXIT_EVENT" in item for item in report.lifecycle.violations)


def test_audit_rejects_incomplete_price_contract() -> None:
    bars = pd.DataFrame({"timestamp": [pd.Timestamp("2026-01-01")], "close": [100.0]})
    with pytest.raises(ValueError, match="missing columns"):
        audit_decisions(symbol="TEST", timeframe="30m", bars=bars, decisions=())
