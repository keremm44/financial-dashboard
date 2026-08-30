from financial_dashboard.decision_audit.compact_backtest_reporting import compact_backtest_output


def test_compact_report_keeps_core_evidence_and_removes_repeated_detail():
    raw = """
CAUSAL_WARMUP_START\t2026-01-01
CAUSAL_SNAPSHOTS\t100
DECISION_EVENTS\t50
PRIMARY_EXECUTION_TIMEFRAME\t1h
EXECUTION_EVENTS_ENTRY_1H\t8
EXECUTION_EVENTS_EXIT_1H\t7
OPPORTUNITY_CALIBRATION\tauto.json
INPUT_REPLAY_PATH\tFROZEN_DECISION_TIMELINE_CACHE_ONLY
FROZEN_CACHE_STATUS\tHIT_EXACT_CACHE_ONLY
DOMAIN_REPLAY_AND_SNAPSHOT_SECONDS\t0.00
DECISION_LAYER_SECONDS\t12.3
REPLAY_MODE\tCANONICAL_1H_PRIMARY_REAL_EXECUTION_ONLY
Completed trades: 3
Wins/Losses: 2/1
BUY 2026-07-01 13:00 [SHORT_TERM/EARLY_TRANSITION] price=359.25
  reasons: DECISION_ST_TRANSITION_LONG_OVERLAY; CURRENT_EXTERNAL_BULLISH_CHOCH
  reasons: DECISION_ST_TRANSITION_LONG_OVERLAY; CURRENT_EXTERNAL_BULLISH_CHOCH
4H LARGE MOVE AUDIT
-------------------
Up moves >= threshold: 2 | captured/already long: 1 | missed: 1
Down moves >= threshold: 1 | long exposure cases: 0
#1 UP MAJOR +20.07% 2026-06-30 -> 2026-07-06
  path: 342.50 -> 411.25
  attribution: BUY_CAPTURED | exposed_at_start=NO
  action: 2026-07-01 13:00 price=359.25 horizon=SHORT_TERM | move elapsed=24.4% | time elapsed=25.0% | remaining move=14.48%
  dominant waiting: SETUP_TRIGGER x4
  dominant blockers: -
  dominant non-action reasons: NO_LONG_OR_SHORT_TERM_ENTRY_SCENARIO x3
#2 DOWN LARGE -12.00% 2026-07-07 -> 2026-07-10
  attribution: NOT_EXPOSED | exposed_at_start=NO
  dominant waiting: -
  dominant blockers: -
  dominant non-action reasons: -
EXECUTION P/L AUDIT
FILL_MODEL\tnext-open
CLOSED_TRADES\t3
OPEN_TRADES\t0
WIN_RATE_PCT\t66.67
AVERAGE_NET_RETURN_PCT\t2.1
CUMULATIVE_NET_RETURN_PCT\t6.2
MAX_DRAWDOWN_PCT\t-1.4
BUY_SELL_BACKTEST_OK
"""
    result = compact_backtest_output(raw)

    assert "COMPACT BUY/SELL BACKTEST REPORT" in result
    assert "CAUSAL_SNAPSHOTS\t100" in result
    assert "Completed trades: 3" in result
    assert "BUY 2026-07-01 13:00 [SHORT_TERM/EARLY_TRANSITION]" in result
    assert result.count("DECISION_ST_TRANSITION_LONG_OVERLAY") == 1
    assert "#1 UP MAJOR +20.07%" in result
    assert "attribution: BUY_CAPTURED" in result
    assert "dominant waiting: SETUP_TRIGGER x4" in result
    assert "CLOSED_TRADES\t3" in result
    assert result.rstrip().endswith("BUY_SELL_BACKTEST_OK")


def test_compact_report_does_not_invent_transition_evidence():
    result = compact_backtest_output("CAUSAL_SNAPSHOTS\t3\nBUY_SELL_BACKTEST_OK\n")
    assert "No EARLY_TRANSITION/ST transition lines in rendered report." in result
