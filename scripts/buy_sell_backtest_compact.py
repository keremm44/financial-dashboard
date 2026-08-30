from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from time import perf_counter

from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.canonical_events import canonical_decision_events_from_replay
from financial_dashboard.decision.engine import DecisionEngineConfig
from financial_dashboard.decision.execution_detect import detect_1h_execution_events, detect_30m_execution_events
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.lifecycle_replay import replay_canonical_trade_lifecycle
from financial_dashboard.decision.timeline_cache import DecisionTimelineCacheMiss, load_frozen_decision_timeline
from financial_dashboard.decision_audit.research import ResearchAuditConfig, audit_buy_sell_research

from buy_sell_backtest import _calibration, _causal_warmup_start, simulate_execution_pnl


def _enum_text(value) -> str:
    return str(getattr(value, "value", value))


def _top_counts(values, *, limit: int = 3) -> str:
    if not values:
        return "-"
    rows = tuple(values)[:limit]
    return "; ".join(f"{name} x{count}" for name, count in rows)


def _transition_summary(decisions) -> tuple[int, tuple[str, ...]]:
    rows: list[str] = []
    count = 0
    for event in decisions:
        snapshot = dict(getattr(event, "snapshot", {}) or {})
        scenario_kind = str(snapshot.get("scenario_kind") or "")
        reasons = tuple(str(item) for item in getattr(event, "reasons", ()) or ())
        transition = (
            scenario_kind == "EARLY_TRANSITION"
            or any(
                marker in reason
                for reason in reasons
                for marker in (
                    "ST_LONG_TRANSITION",
                    "DECISION_ST_TRANSITION_LONG_OVERLAY",
                    "CURRENT_EXTERNAL_BULLISH_CHOCH",
                )
            )
        )
        if not transition:
            continue
        count += 1
        action = _enum_text(getattr(event, "action", "-"))
        price = getattr(event, "price", None)
        rows.append(
            f"{event.timestamp} action={action} scenario={scenario_kind or '-'} "
            f"price={'-' if price is None else f'{float(price):.2f}'}"
        )
    return count, tuple(dict.fromkeys(rows))[:12]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fast compact BUY/SELL backtest from the exact frozen DecisionInput timeline. "
            "Runs canonical decision replay, large-move research, and execution P/L only; "
            "skips the heavier duplicate diagnostic audits used by the full report."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
    parser.add_argument("--canonical-readiness-proxy", action="store_true")
    parser.add_argument(
        "--no-primary-execution",
        "--no-30m-execution",
        dest="no_primary_execution",
        action="store_true",
    )
    parser.add_argument("--opportunity-calibration", type=Path, default=None)
    parser.add_argument("--auto-calibration", action="store_true")
    parser.add_argument("--opportunity-none-max-atr", type=float, default=None)
    parser.add_argument("--opportunity-compressed-max-atr", type=float, default=None)
    parser.add_argument("--opportunity-moderate-max-atr", type=float, default=None)
    parser.add_argument("--audit-timeframe", default="30m")
    parser.add_argument("--short-lookback-bars", type=int, default=6)
    parser.add_argument("--short-lookahead-bars", type=int, default=6)
    parser.add_argument("--long-lookback-bars", type=int, default=20)
    parser.add_argument("--long-lookahead-bars", type=int, default=20)
    parser.add_argument("--lookback-bars", type=int, default=10)
    parser.add_argument("--lookahead-bars", type=int, default=10)
    parser.add_argument("--research-thresholds-pct", nargs="+", type=float, default=(1.0, 2.5, 5.0))
    parser.add_argument("--large-move-min-pct", type=float, default=10.0)
    parser.add_argument("--large-move-reversal-pct", type=float, default=5.0)
    parser.add_argument("--worst-trades", type=int, default=5)
    parser.add_argument("--execution-fill-model", choices=("next-open", "decision-close"), default="next-open")
    parser.add_argument("--spread-bps", type=float, default=0.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--commission-bps", type=float, default=0.0)
    args = parser.parse_args()

    store = ParquetOHLCVStore(args.cache_root)
    symbol = normalize_symbol(args.symbol)
    effective_start = _causal_warmup_start(store, symbol=symbol, requested_start=args.start)
    history_config = HistoricalDecisionInputConfig(
        pattern_profile=args.pattern_profile,
        max_bars=args.max_bars,
        start_at=effective_start,
        end_at=args.end,
    )

    total_started = perf_counter()
    started = perf_counter()
    try:
        frozen = load_frozen_decision_timeline(store, symbol, config=history_config)
    except DecisionTimelineCacheMiss as exc:
        raise SystemExit(
            "FROZEN_DECISION_TIMELINE_CACHE_MISS\n"
            "Compact backtest does not replay domains. Build the frozen timeline first."
        ) from exc
    frozen_seconds = perf_counter() - started
    snapshots = frozen.replay.snapshots
    if not snapshots:
        raise SystemExit("Frozen historical DecisionInput timeline contains no causal snapshots")

    calibration, calibration_label = _calibration(args, cache_root=args.cache_root, symbol=symbol)
    decision_config = DecisionEngineConfig(opportunity_calibration=calibration)

    micro_entry_events, micro_exit_events = detect_30m_execution_events(snapshots)
    if args.no_primary_execution:
        entry_events: dict = {}
        exit_events: dict = {}
    else:
        entry_events, exit_events = detect_1h_execution_events(snapshots)

    started = perf_counter()
    lifecycle = replay_canonical_trade_lifecycle(
        snapshots,
        config=decision_config,
        entry_execution_events=entry_events,
        exit_execution_events=exit_events,
        readiness_execution_proxy=bool(args.canonical_readiness_proxy),
    )
    decisions = canonical_decision_events_from_replay(lifecycle)
    decision_seconds = perf_counter() - started

    bars = store.load(symbol, args.audit_timeframe)
    market_bars_4h = store.load(symbol, "4h")
    if bars.empty or market_bars_4h.empty:
        raise SystemExit("Audit/4H bars are required for compact research")

    started = perf_counter()
    research = audit_buy_sell_research(
        symbol=symbol,
        audit_timeframe=args.audit_timeframe,
        audit_bars=bars,
        market_bars_4h=market_bars_4h,
        decisions=decisions,
        snapshots=snapshots,
        decision_config=decision_config,
        config=ResearchAuditConfig(
            counterfactual_thresholds_pct=tuple(args.research_thresholds_pct),
            short_lookback_bars=args.short_lookback_bars,
            short_lookahead_bars=args.short_lookahead_bars,
            long_lookback_bars=args.long_lookback_bars,
            long_lookahead_bars=args.long_lookahead_bars,
            fallback_lookback_bars=args.lookback_bars,
            fallback_lookahead_bars=args.lookahead_bars,
            large_move_min_pct=args.large_move_min_pct,
            large_move_reversal_pct=args.large_move_reversal_pct,
            attribution_top_n=max(1, args.worst_trades),
        ),
    )
    research_seconds = perf_counter() - started

    started = perf_counter()
    execution = simulate_execution_pnl(
        decisions,
        bars,
        fill_model=args.execution_fill_model,
        spread_bps=args.spread_bps,
        slippage_bps=args.slippage_bps,
        commission_bps=args.commission_bps,
    )
    execution_seconds = perf_counter() - started
    total_seconds = perf_counter() - total_started

    action_counts = Counter(_enum_text(event.action) for event in decisions)
    executed = tuple(event for event in decisions if _enum_text(event.action) in {"BUY", "SELL"})
    transition_count, transition_rows = _transition_summary(decisions)
    up_moves = tuple(row for row in research.large_moves if row.move.direction == "UP")
    down_moves = tuple(row for row in research.large_moves if row.move.direction == "DOWN")
    captured_up = sum(row.status in {"BUY_CAPTURED", "ALREADY_LONG"} for row in up_moves)
    missed_up = sum(row.status == "MISSED_NO_BUY" for row in up_moves)

    print("FAST COMPACT BUY/SELL BACKTEST")
    print("================================")
    print(f"CACHE\t{frozen.cache_status} | domain_replay=0.00s | snapshots={len(snapshots)}")
    print(f"CALIBRATION\t{calibration_label}")
    print(
        "TIMING\t"
        f"cache={frozen_seconds:.2f}s decision={decision_seconds:.2f}s "
        f"research={research_seconds:.2f}s pnl={execution_seconds:.2f}s total={total_seconds:.2f}s"
    )
    print(
        "EVENTS\t"
        f"decision={len(decisions)} entry1h={len(entry_events)} exit1h={len(exit_events)} "
        f"entry30m={len(micro_entry_events)} exit30m={len(micro_exit_events)}"
    )
    print("ACTIONS\t" + ", ".join(f"{name}={count}" for name, count in sorted(action_counts.items())))
    print()
    print("EXECUTION / P&L")
    print(
        f"closed={execution.closed_trades} open={execution.open_trades} wins={execution.wins} losses={execution.losses} "
        f"win_rate={'n/a' if execution.win_rate_pct is None else f'{execution.win_rate_pct:.2f}%'}"
    )
    print(
        f"avg_net={'n/a' if execution.average_net_return_pct is None else f'{execution.average_net_return_pct:.2f}%'} "
        f"cumulative={execution.cumulative_net_return_pct:.2f}% max_dd={execution.max_drawdown_pct:.2f}%"
    )
    for index, trade in enumerate(execution.trades, start=1):
        print(
            f"  trade#{index} BUY {trade.entry_signal_at} @{trade.entry_fill:.2f} -> "
            f"SELL {trade.exit_signal_at} @{trade.exit_fill:.2f} | net={trade.net_return_pct:+.2f}% bars={trade.bars_held}"
        )

    print()
    print("EXECUTED DECISION EVENTS")
    if not executed:
        print("  -")
    for event in executed:
        snapshot = dict(getattr(event, "snapshot", {}) or {})
        print(
            f"  {_enum_text(event.action)} {event.timestamp} price={event.price} "
            f"trade={snapshot.get('trade_horizon', '-')} scenario={snapshot.get('scenario_kind', '-')}"
        )

    print()
    print("ST EARLY TRANSITION")
    print(f"transition_related_events={transition_count}")
    for row in transition_rows:
        print("  " + row)
    if transition_count and not transition_rows:
        print("  transition evidence existed but no unique rendered rows")

    print()
    print("4H LARGE MOVE AUDIT")
    print(f"up={len(up_moves)} captured/already_long={captured_up} missed={missed_up} | down={len(down_moves)}")
    for index, row in enumerate(research.large_moves, start=1):
        move = row.move
        action = "-"
        if row.action_time is not None:
            action = (
                f"{row.action_time} @{row.action_price:.2f} {row.action_horizon or '-'} "
                f"move_elapsed={row.move_elapsed_before_action_pct:.1f}% "
                f"remaining={row.remaining_move_after_action_pct:.1f}%"
            )
        print(
            f"#{index} {move.direction} {move.move_pct:+.2f}% {move.start_time} -> {move.end_time} | "
            f"{row.status} | action={action}"
        )
        if row.status in {"MISSED_NO_BUY", "BUY_CAPTURED", "ALREADY_LONG"}:
            print(
                "   locks: "
                f"waiting={_top_counts(row.dominant_waiting_for)} | "
                f"blockers={_top_counts(row.dominant_blockers)} | "
                f"reasons={_top_counts(row.dominant_reasons)}"
            )

    print()
    print("COMPACT_PIPELINE\tDECISION_REPLAY + LARGE_MOVE_RESEARCH + EXECUTION_PNL")
    print("SKIPPED_HEAVY_DUPLICATE_AUDITS\tTRADE_QUALITY; TARGET_TRANSITION; SCENARIO_AUTHORITY; FULL_DECISION_AUDIT")
    print("BUY_SELL_BACKTEST_COMPACT_OK")


if __name__ == "__main__":
    main()
