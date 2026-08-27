from pathlib import Path

import pandas as pd
import pytest

from financial_dashboard.decision_audit import (
    DecisionAction,
    DecisionEvent,
    DecisionSide,
    TradeQualityAuditConfig,
    audit_trade_quality,
    render_trade_quality_text,
)


def _bars(count: int = 40) -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-01-05 09:30")
    for index in range(count):
        close = 96.0 + index * 0.5
        rows.append(
            {
                "timestamp": start + pd.Timedelta(minutes=30 * index),
                "high": close + 0.8,
                "low": close - 0.8,
                "close": close,
            }
        )
    frame = pd.DataFrame(rows)
    frame.loc[0, "low"] = 80.0
    return frame


def _event(
    bars: pd.DataFrame,
    index: int,
    action: DecisionAction,
    *,
    price: float | None = None,
    horizon: str = "SHORT_TERM",
    scenario: str = "PULLBACK_CONTINUATION",
    markers=None,
    target_nodes=None,
) -> DecisionEvent:
    snapshot = {
        "canonical_lifecycle": True,
        "entry_horizon": horizon,
        "scenario_kind": scenario,
        "audit_markers": markers or {},
    }
    if target_nodes is not None:
        snapshot["target_path"] = {
            "status": "READY",
            "active_identity": None,
            "nodes": target_nodes,
        }
    return DecisionEvent(
        timestamp=bars.iloc[index]["timestamp"],
        action=action,
        side=DecisionSide.LONG,
        price=float(bars.iloc[index]["close"]) if price is None else float(price),
        snapshot=snapshot,
    )


def _completed_trade(bars: pd.DataFrame, *, horizon: str):
    return (
        _event(bars, 10, DecisionAction.BUY, price=100.0, horizon=horizon),
        _event(bars, 14, DecisionAction.SELL, price=103.0, horizon=horizon),
    )


def test_horizon_specific_windows_keep_short_local_and_allow_wider_long_tolerance():
    bars = _bars()
    config = TradeQualityAuditConfig(
        short_lookback_bars=6,
        short_lookahead_bars=6,
        long_lookback_bars=20,
        long_lookahead_bars=20,
    )
    short = audit_trade_quality(
        symbol="TEST",
        timeframe="30m",
        bars=bars,
        decisions=_completed_trade(bars, horizon="SHORT_TERM"),
        config=config,
    ).trades[0]
    long = audit_trade_quality(
        symbol="TEST",
        timeframe="30m",
        bars=bars,
        decisions=_completed_trade(bars, horizon="LONG_TERM"),
        config=config,
    ).trades[0]

    assert short.audit_lookback_bars == 6
    assert short.audit_lookahead_bars == 6
    assert long.audit_lookback_bars == 20
    assert long.audit_lookahead_bars == 20
    assert long.entry_local_low == 80.0
    assert short.entry_local_low > long.entry_local_low
    assert long.entry_local_low_miss_pct > short.entry_local_low_miss_pct


def test_quality_audit_separates_confirmation_cost_from_ready_execution_delay():
    bars = _bars()
    qualified_time = bars.iloc[8]["timestamp"]
    ready_time = bars.iloc[9]["timestamp"]
    exit_watch_time = bars.iloc[12]["timestamp"]
    exit_ready_time = bars.iloc[13]["timestamp"]
    decisions = (
        _event(
            bars,
            10,
            DecisionAction.BUY,
            price=100.0,
            markers={
                "scenario_qualified_at": qualified_time,
                "scenario_qualified_price": 98.0,
                "ready_for_execution_at": ready_time,
                "ready_for_execution_price": 99.0,
            },
        ),
        _event(
            bars,
            14,
            DecisionAction.SELL,
            price=103.0,
            markers={
                "exit_watch_at": exit_watch_time,
                "exit_watch_price": 104.0,
                "exit_ready_at": exit_ready_time,
                "exit_ready_price": 104.0,
            },
        ),
    )

    trade = audit_trade_quality(
        symbol="TEST",
        timeframe="30m",
        bars=bars,
        decisions=decisions,
    ).trades[0]

    assert trade.scenario_to_buy_bars == 2
    assert trade.ready_to_buy_bars == 1
    assert trade.scenario_to_buy_price_change_pct == pytest.approx((100.0 - 98.0) / 98.0 * 100.0)
    assert trade.ready_to_buy_price_change_pct == pytest.approx((100.0 - 99.0) / 99.0 * 100.0)
    assert trade.exit_watch_to_sell_bars == 2
    assert trade.exit_ready_to_sell_bars == 1
    assert trade.exit_ready_to_sell_giveback_pct == pytest.approx((104.0 - 103.0) / 104.0 * 100.0)


def test_target_fsm_history_and_short_three_five_percent_research_are_measured_without_new_trade_rules():
    bars = _bars()
    bars.loc[11, "high"] = 101.0
    bars.loc[12, "high"] = 103.5
    bars.loc[13, "high"] = 104.0
    bars.loc[14, "high"] = 104.2
    decisions = (
        _event(bars, 10, DecisionAction.BUY, price=100.0),
        _event(
            bars,
            11,
            DecisionAction.HOLD,
            target_nodes=[
                {"identity": "T1", "state": "CLEARED", "roles": ["BARRIER"], "sources": ["SUPPORT_RESISTANCE"]},
                {"identity": "T2", "state": "ACTIVE", "roles": ["OBJECTIVE"], "sources": ["STRUCTURAL_WEAK"]},
            ],
        ),
        _event(
            bars,
            12,
            DecisionAction.HOLD,
            target_nodes=[
                {"identity": "T1", "state": "CLEARED", "roles": ["BARRIER"], "sources": ["SUPPORT_RESISTANCE"]},
                {"identity": "T2", "state": "DEFENDED", "roles": ["OBJECTIVE"], "sources": ["STRUCTURAL_WEAK"]},
            ],
        ),
        _event(bars, 14, DecisionAction.SELL, price=103.0),
    )

    report = audit_trade_quality(
        symbol="TEST",
        timeframe="30m",
        bars=bars,
        decisions=decisions,
    )
    trade = report.trades[0]

    assert trade.target_cleared_count == 1
    assert trade.target_defended_count == 1
    assert trade.target_defended_to_sell_bars == 2
    assert len(trade.short_target_hits) == 2
    three, five = trade.short_target_hits
    assert three.target_pct == 3.0 and three.reached is True and three.bars_to_reach == 2
    assert five.target_pct == 5.0 and five.reached is False and five.bars_to_reach is None
    assert report.metrics.short_target_reach_rate_pct["3%"] == 100.0
    assert report.metrics.short_target_reach_rate_pct["5%"] == 0.0


def test_quality_report_groups_by_frozen_horizon_and_scenario_and_keeps_open_trade_censored():
    bars = _bars()
    completed = (
        _event(bars, 5, DecisionAction.BUY, horizon="SHORT_TERM", scenario="BREAKOUT_RETEST", price=99.0),
        _event(bars, 8, DecisionAction.SELL, horizon="SHORT_TERM", scenario="BREAKOUT_RETEST", price=101.0),
        _event(bars, 12, DecisionAction.BUY, horizon="LONG_TERM", scenario="STABIL_RECOVERY", price=102.0),
        _event(bars, 16, DecisionAction.SELL, horizon="LONG_TERM", scenario="STABIL_RECOVERY", price=105.0),
        _event(bars, 20, DecisionAction.BUY, horizon="SHORT_TERM", scenario="PULLBACK_CONTINUATION", price=106.0),
    )
    report = audit_trade_quality(
        symbol="TEST",
        timeframe="30m",
        bars=bars,
        decisions=completed,
    )

    assert report.metrics.trade_count == 2
    assert report.censored_open_trades == 1
    assert report.metrics_by_horizon["SHORT_TERM"].trade_count == 1
    assert report.metrics_by_horizon["LONG_TERM"].trade_count == 1
    assert report.metrics_by_scenario["BREAKOUT_RETEST"].trade_count == 1
    assert report.metrics_by_scenario["STABIL_RECOVERY"].trade_count == 1
    text = render_trade_quality_text(report)
    assert "HORIZON-AWARE TRADE QUALITY AUDIT" in text
    assert "BY HORIZON" in text
    assert "BY SCENARIO" in text


def test_quality_audit_counts_repeated_buy_and_flat_sell_without_creating_fake_completed_trades():
    bars = _bars()
    decisions = (
        _event(bars, 5, DecisionAction.SELL),
        _event(bars, 8, DecisionAction.BUY),
        _event(bars, 9, DecisionAction.BUY),
        _event(bars, 12, DecisionAction.SELL),
    )
    report = audit_trade_quality(symbol="TEST", timeframe="30m", bars=bars, decisions=decisions)
    assert report.metrics.trade_count == 1
    assert report.unmatched_sell_events == 1
    assert report.unmatched_buy_events == 1


def test_trade_quality_module_is_strictly_downstream_and_cannot_call_decision_engine():
    source = Path("src/financial_dashboard/decision_audit/trade_quality.py").read_text(encoding="utf-8")
    forbidden = (
        "decision_input",
        "assess_entry_decision",
        "assess_position_exit_decision",
        "replay_canonical_trade_lifecycle",
        "build_decision_input_snapshot",
    )
    for token in forbidden:
        assert token not in source
