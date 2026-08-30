import pandas as pd

from financial_dashboard.decision_audit.models import DecisionAction, DecisionEvent, DecisionSide
from financial_dashboard.decision_audit.research import (
    LargeMarketMove,
    ResearchAuditConfig,
    attribute_large_market_moves,
    audit_event_counterfactuals,
    detect_large_market_moves,
)
from financial_dashboard.decision_audit.research_reporting import render_research_text
from financial_dashboard.decision_audit.research import BuySellResearchAuditReport


def _bars(times, lows, highs, closes):
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(times),
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": 1.0,
        }
    )


def _event(
    timestamp,
    action,
    price,
    *,
    waiting=(),
    blockers=(),
    reasons=(),
    horizon="SHORT_TERM",
    position="FLAT",
):
    return DecisionEvent(
        timestamp=pd.Timestamp(timestamp),
        action=action,
        side=DecisionSide.LONG if action in {DecisionAction.BUY, DecisionAction.SELL, DecisionAction.HOLD} else DecisionSide.NONE,
        price=float(price),
        reasons=tuple(reasons),
        blockers=tuple(blockers),
        waiting_for=tuple(waiting),
        snapshot={
            "trade_horizon": horizon,
            "scenario_kind": "CONTINUATION",
            "lifecycle_phase": "ENTRY_WAIT" if action is DecisionAction.WAIT else action.value,
            "trade_lifecycle": {"position_state": position},
            "entry_decision": None
            if action in {DecisionAction.HOLD, DecisionAction.SELL}
            else {
                "scenario_stage": "QUALIFIED" if action is DecisionAction.BUY else "DEVELOPING",
                "scenario_kind": "CONTINUATION",
                "selected_horizon": horizon,
                "trade_horizon": horizon,
                "execution_state": "CONFIRMED" if action is DecisionAction.BUY else "ABSENT",
            },
            "position_exit": None,
        },
    )


def test_counterfactual_audit_finds_first_pre_buy_threshold_and_missing_gate():
    times = pd.date_range("2026-01-05 10:00", periods=5, freq="1h")
    bars = _bars(
        times,
        lows=[101.0, 100.0, 101.0, 102.0, 105.0],
        highs=[102.0, 101.0, 103.0, 104.0, 107.0],
        closes=[101.5, 100.5, 102.0, 103.0, 106.0],
    )
    decisions = (
        _event(times[0], DecisionAction.WAIT, 101.5, waiting=("STRUCTURE_TO_RESOLVE",)),
        _event(times[1], DecisionAction.WAIT, 100.5, waiting=("SETUP_TRIGGER",)),
        _event(times[2], DecisionAction.WAIT, 102.0, waiting=("SETUP_TRIGGER",)),
        _event(times[3], DecisionAction.WAIT, 103.0, waiting=("FRESH_EXECUTION_EVENT",)),
        _event(times[4], DecisionAction.BUY, 106.0, position="OPEN"),
    )

    rows = audit_event_counterfactuals(
        audit_bars=bars,
        decisions=decisions,
        config=ResearchAuditConfig(counterfactual_thresholds_pct=(1.0, 2.5, 5.0)),
    )

    assert len(rows) == 1
    buy = rows[0]
    assert buy.action == "BUY"
    assert buy.extreme_kind == "LOW"
    assert buy.event_vs_extreme == "AFTER_EXTREME"
    assert buy.checkpoints[0].relation == "BEFORE_EVENT"
    assert buy.checkpoints[0].checkpoint_time == times[2]
    assert buy.checkpoints[0].waiting_for == ("SETUP_TRIGGER",)
    assert buy.checkpoints[1].relation == "BEFORE_EVENT"
    assert buy.checkpoints[1].checkpoint_time == times[3]
    assert buy.checkpoints[1].waiting_for == ("FRESH_EXECUTION_EVENT",)
    assert buy.checkpoints[2].relation == "EVENT_BEFORE_THRESHOLD"
    assert buy.checkpoints[2].checkpoint_time is None


def test_counterfactual_audit_labels_buy_that_precedes_eventual_low_as_early():
    times = pd.date_range("2026-01-05 10:00", periods=5, freq="1h")
    bars = _bars(
        times,
        lows=[106.0, 105.0, 103.0, 100.0, 102.0],
        highs=[108.0, 107.0, 105.0, 102.0, 104.0],
        closes=[107.0, 106.0, 104.0, 101.0, 103.0],
    )
    decisions = (
        _event(times[0], DecisionAction.WAIT, 107.0),
        _event(times[1], DecisionAction.BUY, 106.0, position="OPEN"),
        _event(times[2], DecisionAction.HOLD, 104.0, position="OPEN"),
        _event(times[3], DecisionAction.HOLD, 101.0, position="OPEN"),
        _event(times[4], DecisionAction.HOLD, 103.0, position="OPEN"),
    )

    rows = audit_event_counterfactuals(
        audit_bars=bars,
        decisions=decisions,
        config=ResearchAuditConfig(counterfactual_thresholds_pct=(2.5,)),
    )

    buy = rows[0]
    assert buy.event_vs_extreme == "BEFORE_EXTREME"
    assert buy.checkpoints[0].relation == "AFTER_EVENT_TOWARD_EXTREME"
    assert buy.checkpoints[0].checkpoint_time == times[3]
    assert buy.checkpoints[0].action == "HOLD"


def test_large_move_detector_emits_one_maximal_up_leg_not_nested_duplicates():
    times = pd.date_range("2026-01-05 10:00", periods=7, freq="4h")
    bars = _bars(
        times,
        lows=[100.0, 101.0, 105.0, 110.0, 114.0, 112.0, 110.0],
        highs=[102.0, 108.0, 115.0, 121.0, 118.0, 115.0, 114.0],
        closes=[101.0, 107.0, 114.0, 120.0, 115.0, 113.0, 112.0],
    )

    moves = detect_large_market_moves(bars, min_move_pct=10.0, reversal_pct=5.0)
    up = [move for move in moves if move.direction == "UP"]

    assert len(up) == 1
    assert up[0].start_price == 100.0
    assert up[0].end_price == 121.0
    assert round(up[0].move_pct, 2) == 21.0
    assert up[0].classification == "MAJOR"


def test_large_up_move_attributes_pre_buy_waiting_gates_and_progress():
    move = LargeMarketMove(
        direction="UP",
        classification="MAJOR",
        start_time=pd.Timestamp("2026-01-05 10:00"),
        end_time=pd.Timestamp("2026-01-07 10:00"),
        start_price=100.0,
        end_price=120.0,
        move_pct=20.0,
        duration_hours=48.0,
        four_hour_bars=13,
        trading_days=3,
        move_pct_per_4h_bar=20.0 / 12.0,
        move_pct_per_trading_day=20.0 / 3.0,
    )
    decisions = (
        _event("2026-01-05 10:00", DecisionAction.WAIT, 100.0, waiting=("SETUP_TRIGGER",)),
        _event("2026-01-05 14:00", DecisionAction.WAIT, 103.0, waiting=("SETUP_TRIGGER", "FRESH_EXECUTION_EVENT")),
        _event("2026-01-06 10:00", DecisionAction.BUY, 108.0, position="OPEN"),
        _event("2026-01-07 10:00", DecisionAction.HOLD, 120.0, position="OPEN"),
    )

    row = attribute_large_market_moves((move,), decisions)[0]

    assert row.status == "BUY_CAPTURED"
    assert row.action_horizon == "SHORT_TERM"
    assert row.move_elapsed_before_action_pct == 40.0
    assert row.time_elapsed_before_action_pct == 50.0
    assert row.dominant_waiting_for[0] == ("SETUP_TRIGGER", 2)
    assert ("FRESH_EXECUTION_EVENT", 1) in row.dominant_waiting_for


def test_large_down_move_without_long_exposure_is_not_short_opportunity():
    move = LargeMarketMove(
        direction="DOWN",
        classification="LARGE",
        start_time=pd.Timestamp("2026-01-05 10:00"),
        end_time=pd.Timestamp("2026-01-06 10:00"),
        start_price=120.0,
        end_price=105.0,
        move_pct=-12.5,
        duration_hours=24.0,
        four_hour_bars=7,
        trading_days=2,
        move_pct_per_4h_bar=12.5 / 6.0,
        move_pct_per_trading_day=6.25,
    )
    decisions = (
        _event("2026-01-05 10:00", DecisionAction.WAIT, 120.0),
        _event("2026-01-05 14:00", DecisionAction.NO_TRADE, 116.0),
        _event("2026-01-06 10:00", DecisionAction.WAIT, 105.0),
    )

    row = attribute_large_market_moves((move,), decisions)[0]
    assert row.status == "NOT_EXPOSED"
    assert row.action_time is None


def test_research_report_marks_hindsight_as_non_authoritative():
    report = BuySellResearchAuditReport(
        symbol="TEST",
        audit_timeframe="30m",
        market_timeframe="4h",
        thresholds_pct=(1.0, 2.5, 5.0),
        counterfactuals=(),
        large_moves=(),
    )
    text = render_research_text(report)
    assert "DIAGNOSTIC ONLY" in text
    assert "Decision authority: NONE" in text
