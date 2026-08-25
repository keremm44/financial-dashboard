from __future__ import annotations

import argparse
from pathlib import Path

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.historical_replay import HistoricalReplayConfig, replay_historical_decisions
from financial_dashboard.decision.opportunity import OpportunityCalibration
from financial_dashboard.decision.structural import DecisionHorizon
from financial_dashboard.decision_audit import DecisionAuditConfig, audit_decisions, render_json, render_text


def _calibration(args: argparse.Namespace) -> OpportunityCalibration | None:
    values = (
        args.opportunity_none_max_atr,
        args.opportunity_compressed_max_atr,
        args.opportunity_moderate_max_atr,
    )
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise SystemExit(
            "Opportunity calibration requires all three boundaries: "
            "--opportunity-none-max-atr, --opportunity-compressed-max-atr, "
            "--opportunity-moderate-max-atr"
        )
    return OpportunityCalibration(
        none_max_atr=float(values[0]),
        compressed_max_atr=float(values[1]),
        moderate_max_atr=float(values[2]),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Causally replay the decision engine over historical 1h closes, optionally "
            "convert READY side changes into a diagnostic long-only position proxy, "
            "then run the hindsight decision-quality audit."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument(
        "--horizon",
        choices=("lt", "st"),
        default="st",
        help="Decision horizon: lt=long term, st=short term",
    )
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
    parser.add_argument(
        "--readiness-position-proxy",
        action="store_true",
        help=(
            "Diagnostic only: flat+LONG READY becomes BUY; open-long+SHORT READY becomes SELL. "
            "This is not the production execution-trigger contract."
        ),
    )
    parser.add_argument("--opportunity-none-max-atr", type=float, default=None)
    parser.add_argument("--opportunity-compressed-max-atr", type=float, default=None)
    parser.add_argument("--opportunity-moderate-max-atr", type=float, default=None)

    parser.add_argument("--audit-timeframe", default="30m")
    parser.add_argument("--lookback-bars", type=int, default=10)
    parser.add_argument("--lookahead-bars", type=int, default=10)
    parser.add_argument("--meaningful-move-atr", type=float, default=None)
    parser.add_argument("--opportunity-horizon-bars", type=int, default=20)
    parser.add_argument("--swing-radius-bars", type=int, default=3)
    parser.add_argument("--capture-entry-window-bars", type=int, default=5)
    parser.add_argument("--worst-trades", type=int, default=5)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    horizon = DecisionHorizon.LONG_TERM if args.horizon == "lt" else DecisionHorizon.SHORT_TERM
    replay_config = HistoricalReplayConfig(
        horizon=horizon,
        pattern_profile=args.pattern_profile,
        opportunity_calibration=_calibration(args),
        readiness_position_proxy=bool(args.readiness_position_proxy),
        max_bars=args.max_bars,
        start_at=args.start,
        end_at=args.end,
    )

    decisions = replay_historical_decisions(
        args.cache_root,
        symbol=args.symbol,
        config=replay_config,
    )
    if not decisions:
        raise SystemExit("Historical replay produced no decision events")

    bars = ParquetOHLCVStore(args.cache_root).load(args.symbol, args.audit_timeframe)
    if bars.empty:
        raise SystemExit(f"No audit bars found for {args.symbol} {args.audit_timeframe}")

    audit_config = DecisionAuditConfig(
        extrema_lookback_bars=args.lookback_bars,
        extrema_lookahead_bars=args.lookahead_bars,
        opportunity_horizon_bars=args.opportunity_horizon_bars,
        swing_radius_bars=args.swing_radius_bars,
        meaningful_move_atr=args.meaningful_move_atr,
        capture_entry_window_bars=args.capture_entry_window_bars,
    )
    report = audit_decisions(
        symbol=args.symbol,
        timeframe=args.audit_timeframe,
        bars=bars,
        decisions=decisions,
        config=audit_config,
    )

    print(f"REPLAY_EVENTS\t{len(decisions)}")
    print(f"REPLAY_MODE\t{'READINESS_POSITION_PROXY' if args.readiness_position_proxy else 'RAW_DECISION_TIMELINE'}")
    if replay_config.opportunity_calibration is None:
        print("OPPORTUNITY_CALIBRATION\tUNSET (canonical engine may remain WAIT on UNKNOWN opportunity)")
    print(render_text(report, worst_trade_limit=max(1, args.worst_trades)))

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(render_json(report), encoding="utf-8")
        print(f"JSON_REPORT\t{args.json_out}")
    print("DECISION_REPLAY_AUDIT_OK")


if __name__ == "__main__":
    main()
