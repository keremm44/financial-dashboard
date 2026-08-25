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


def test_unmatched_action_events_are_reported_instead_of_hidden() -> None:
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
    assert report.unmatched_buy_events == 1


def test_audit_rejects_incomplete_price_contract() -> None:
    bars = pd.DataFrame({"timestamp": [pd.Timestamp("2026-01-01")], "close": [100.0]})
    with pytest.raises(ValueError, match="missing columns"):
        audit_decisions(symbol="TEST", timeframe="30m", bars=bars, decisions=())
