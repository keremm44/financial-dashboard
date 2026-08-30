from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from json import dumps
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.calibration import load_opportunity_calibration
from financial_dashboard.decision.canonical_events import canonical_decision_events_from_replay
from financial_dashboard.decision.engine import DecisionEngineConfig
from financial_dashboard.decision.execution_detect import (
    detect_1h_execution_events,
    detect_30m_execution_events,
)
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.lifecycle_replay import replay_canonical_trade_lifecycle
from financial_dashboard.decision.opportunity import OpportunityCalibration
from financial_dashboard.decision.timeline_cache import (
    DecisionTimelineCacheMiss,
    load_frozen_decision_timeline,
)
from financial_dashboard.decision_audit import (
    DecisionAuditConfig,
    TradeQualityAuditConfig,
    audit_decisions,
    audit_trade_quality,
    render_json,
    render_text,
    render_trade_quality_json,
    render_trade_quality_text,
)
from financial_dashboard.decision_audit.research import ResearchAuditConfig, audit_buy_sell_research
from financial_dashboard.decision_audit.research_reporting import (
    render_research_json,
    render_research_text,
)
from financial_dashboard.decision_audit.target_transition_research import (
    TargetTransitionAuditConfig,
    audit_target_path_transitions,
    render_target_path_transition_text,
)
from financial_dashboard.structure_location_replay import CausalBarClock


@dataclass(frozen=True, slots=True)
class ExecutionTrade:
    entry_signal_at: pd.Timestamp
    entry_fill_at: pd.Timestamp
    exit_signal_at: pd.Timestamp
    exit_fill_at: pd.Timestamp
    entry_fill: float
    exit_fill: float
    gross_return_pct: float
    net_return_pct: float
    bars_held: int


@dataclass(frozen=True, slots=True)
class ExecutionPnlReport:
    fill_model: str
    spread_bps: float
    slippage_bps: float
    commission_bps: float
    closed_trades: int
    open_trades: int
    wins: int
    losses: int
    win_rate_pct: float | None
    average_net_return_pct: float | None
    cumulative_net_return_pct: float
    max_drawdown_pct: float
    trades: tuple[ExecutionTrade, ...]


def _align_requested_start(value: str, reference: pd.Timestamp) -> pd.Timestamp:
    requested = pd.Timestamp(value)
    if reference.tzinfo is not None and requested.tzinfo is None:
        requested = requested.tz_localize(reference.tzinfo)
    elif reference.tzinfo is None and requested.tzinfo is not None:
        requested = requested.tz_localize(None)
    elif reference.tzinfo is not None and requested.tzinfo is not None:
        requested = requested.tz_convert(reference.tzinfo)
    return requested


def _causal_warmup_start(
    store: ParquetOHLCVStore,
    *,
    symbol: str,
    requested_start: str | None,
    decision_timeframe: str = "1h",
) -> pd.Timestamp:
    clock = CausalBarClock()
    first_available: list[pd.Timestamp] = []
    for timeframe in ANALYSIS_TIMEFRAMES:
        frame = store.load(symbol, timeframe)
        if frame.empty:
            raise SystemExit(f"No historical bars found for {symbol} {timeframe}")
        first_timestamp = pd.Timestamp(frame.iloc[0]["timestamp"])
        first_available.append(pd.Timestamp(clock.available_at(first_timestamp, timeframe)))

    common_cutoff = max(first_available)
    decision_frame = store.load(symbol, decision_timeframe)
    if decision_frame.empty:
        raise SystemExit(f"No historical bars found for {symbol} {decision_timeframe}")

    warmup_start: pd.Timestamp | None = None
    for value in decision_frame["timestamp"]:
        timestamp = pd.Timestamp(value)
        if pd.Timestamp(clock.available_at(timestamp, decision_timeframe)) >= common_cutoff:
            warmup_start = timestamp
            break
    if warmup_start is None:
        raise SystemExit(
            "No decision bar exists after all required timeframe histories become causally available"
        )
    if requested_start is None:
        return warmup_start
    return max(warmup_start, _align_requested_start(requested_start, warmup_start))


def _calibration(
    args: argparse.Namespace,
    *,
    cache_root: Path,
    symbol: str,
) -> tuple[OpportunityCalibration | None, str]:
    manual = (
        args.opportunity_none_max_atr,
        args.opportunity_compressed_max_atr,
        args.opportunity_moderate_max_atr,
    )
    has_manual = any(value is not None for value in manual)
    has_file = args.opportunity_calibration is not None or args.auto_calibration
    if has_manual and has_file:
        raise SystemExit(
            "manual opportunity boundaries cannot be combined with --opportunity-calibration/--auto-calibration"
        )
    if has_manual:
        if any(value is None for value in manual):
            raise SystemExit(
                "Opportunity calibration requires all three manual boundaries: "
                "--opportunity-none-max-atr, --opportunity-compressed-max-atr, "
                "--opportunity-moderate-max-atr"
            )
        return (
            OpportunityCalibration(
                none_max_atr=float(manual[0]),
                compressed_max_atr=float(manual[1]),
                moderate_max_atr=float(manual[2]),
            ),
            "MANUAL",
        )

    path = args.opportunity_calibration
    if path is None and args.auto_calibration:
        path = cache_root / "calibration" / "opportunity" / f"{normalize_symbol(symbol)}.json"
    if path is None:
        return None, "NONE"
    if not Path(path).exists():
        raise SystemExit(
            f"opportunity calibration file is missing: {path}. Build it with "
            f"`python scripts/build_opportunity_calibration.py {cache_root} {normalize_symbol(symbol)}`"
        )
    record = load_opportunity_calibration(path)
    clean_symbol = normalize_symbol(symbol)
    if normalize_symbol(record.symbol) != clean_symbol:
        raise SystemExit(
            f"opportunity calibration symbol mismatch: file={record.symbol} requested={clean_symbol}"
        )
    return record.calibration, str(path)


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)


def _timeline_json(decisions) -> str:
    return dumps(
        [asdict(event) for event in decisions],
        ensure_ascii=False,
        indent=2,
        default=_json_default,
    )


def _normalise_execution_bars(bars: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "open", "close"}
    missing = required.difference(bars.columns)
    if missing:
        raise ValueError(f"execution P/L bars missing columns: {sorted(missing)}")
    frame = bars.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    return frame.sort_values("timestamp").reset_index(drop=True)


def _execution_fill(
    frame: pd.DataFrame,
    signal_at: Any,
    fill_model: str,
) -> tuple[int, pd.Timestamp, float] | None:
    timestamp = pd.Timestamp(signal_at)
    if fill_model == "next-open":
        matches = frame.index[frame["timestamp"] > timestamp]
        price_column = "open"
    elif fill_model == "decision-close":
        matches = frame.index[frame["timestamp"] == timestamp]
        price_column = "close"
    else:
        raise ValueError("fill_model must be 'next-open' or 'decision-close'")
    if len(matches) == 0:
        return None
    index = int(matches[0])
    return index, pd.Timestamp(frame.at[index, "timestamp"]), float(frame.at[index, price_column])


def simulate_execution_pnl(
    decisions: Iterable[Any],
    bars: pd.DataFrame,
    *,
    fill_model: str = "next-open",
    spread_bps: float = 0.0,
    slippage_bps: float = 0.0,
    commission_bps: float = 0.0,
) -> ExecutionPnlReport:
    """Downstream long-only fill simulation; never feeds hindsight into decisions."""

    for name, value in {
        "spread_bps": spread_bps,
        "slippage_bps": slippage_bps,
        "commission_bps": commission_bps,
    }.items():
        if value < 0:
            raise ValueError(f"{name} must be >= 0")

    frame = _normalise_execution_bars(bars)
    ordered = sorted(decisions, key=lambda item: pd.Timestamp(item.timestamp))
    pending_buy = None
    trades: list[ExecutionTrade] = []
    adverse = (spread_bps / 2.0 + slippage_bps) / 10_000.0
    commission = commission_bps / 10_000.0

    for event in ordered:
        action = str(getattr(getattr(event, "action", None), "value", getattr(event, "action", ""))).upper()
        if action == "BUY" and pending_buy is None:
            pending_buy = event
            continue
        if action != "SELL" or pending_buy is None:
            continue

        entry = _execution_fill(frame, pending_buy.timestamp, fill_model)
        exit_fill = _execution_fill(frame, event.timestamp, fill_model)
        if entry is None or exit_fill is None:
            pending_buy = None
            continue
        entry_index, entry_at, raw_entry = entry
        exit_index, exit_at, raw_exit = exit_fill
        if exit_index <= entry_index:
            pending_buy = None
            continue

        buy_price = raw_entry * (1.0 + adverse)
        sell_price = raw_exit * (1.0 - adverse)
        gross = raw_exit / raw_entry - 1.0
        net = sell_price / buy_price - 1.0 - 2.0 * commission
        trades.append(
            ExecutionTrade(
                entry_signal_at=pd.Timestamp(pending_buy.timestamp),
                entry_fill_at=entry_at,
                exit_signal_at=pd.Timestamp(event.timestamp),
                exit_fill_at=exit_at,
                entry_fill=buy_price,
                exit_fill=sell_price,
                gross_return_pct=gross * 100.0,
                net_return_pct=net * 100.0,
                bars_held=exit_index - entry_index,
            )
        )
        pending_buy = None

    wins = sum(trade.net_return_pct > 0 for trade in trades)
    losses = sum(trade.net_return_pct <= 0 for trade in trades)
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for trade in trades:
        equity *= 1.0 + trade.net_return_pct / 100.0
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = min(max_drawdown, equity / peak - 1.0)

    count = len(trades)
    return ExecutionPnlReport(
        fill_model=fill_model,
        spread_bps=float(spread_bps),
        slippage_bps=float(slippage_bps),
        commission_bps=float(commission_bps),
        closed_trades=count,
        open_trades=1 if pending_buy is not None else 0,
        wins=wins,
        losses=losses,
        win_rate_pct=None if count == 0 else wins / count * 100.0,
        average_net_return_pct=None if count == 0 else sum(t.net_return_pct for t in trades) / count,
        cumulative_net_return_pct=(equity - 1.0) * 100.0,
        max_drawdown_pct=max_drawdown * 100.0,
        trades=tuple(trades),
    )


def _execution_report_json(report: ExecutionPnlReport) -> str:
    return dumps(asdict(report), ensure_ascii=False, indent=2, default=_json_default)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run canonical BUY/SELL lifecycle, hindsight audits, counterfactual/4H move research, "
            "target-path transition research, and an optional-realism fill/P&L audit from an exact "
            "frozen DecisionInput timeline. Domains are never replayed."
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
        help="Do not attach the primary 1h event detector (READY stays READY).",
    )
    parser.add_argument("--opportunity-calibration", type=Path, default=None)
    parser.add_argument(
        "--auto-calibration",
        action="store_true",
        help="Load <cache_root>/calibration/opportunity/<symbol>.json and fail if missing",
    )
    parser.add_argument("--opportunity-none-max-atr", type=float, default=None)
    parser.add_argument("--opportunity-compressed-max-atr", type=float, default=None)
    parser.add_argument("--opportunity-moderate-max-atr", type=float, default=None)

    parser.add_argument("--audit-timeframe", default="30m")
    parser.add_argument("--lookback-bars", type=int, default=10)
    parser.add_argument("--lookahead-bars", type=int, default=10)
    parser.add_argument("--short-lookback-bars", type=int, default=6)
    parser.add_argument("--short-lookahead-bars", type=int, default=6)
    parser.add_argument("--long-lookback-bars", type=int, default=20)
    parser.add_argument("--long-lookahead-bars", type=int, default=20)
    parser.add_argument("--meaningful-move-atr", type=float, default=None)
    parser.add_argument("--opportunity-horizon-bars", type=int, default=20)
    parser.add_argument("--swing-radius-bars", type=int, default=3)
    parser.add_argument("--capture-entry-window-bars", type=int, default=5)
    parser.add_argument("--worst-trades", type=int, default=5)
    parser.add_argument(
        "--research-thresholds-pct",
        nargs="+",
        type=float,
        default=(1.0, 2.5, 5.0),
        help="Hindsight-only pre-entry/pre-exit distance checkpoints (default: 1 2.5 5).",
    )
    parser.add_argument(
        "--large-move-min-pct",
        type=float,
        default=10.0,
        help="Minimum absolute 4H leg size included in the market opportunity audit.",
    )
    parser.add_argument(
        "--large-move-reversal-pct",
        type=float,
        default=5.0,
        help="4H reversal used to terminate one maximal large-move leg without nested duplicates.",
    )
    parser.add_argument(
        "--target-persistence-snapshots",
        type=int,
        default=2,
        help="Research-only consecutive 1h decision closes above a prior target node (default: 2).",
    )
    parser.add_argument(
        "--target-retest-tolerance-pct",
        type=float,
        default=0.50,
        help="Research-only 30m held-retest tolerance around the prior target node (default: 0.50).",
    )
    parser.add_argument("--execution-fill-model", choices=("next-open", "decision-close"), default="next-open")
    parser.add_argument("--spread-bps", type=float, default=0.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--commission-bps", type=float, default=0.0)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--quality-json-out", type=Path, default=None)
    parser.add_argument("--research-json-out", type=Path, default=None)
    parser.add_argument("--timeline-json-out", type=Path, default=None)
    parser.add_argument("--execution-json-out", type=Path, default=None)
    args = parser.parse_args()

    store = ParquetOHLCVStore(args.cache_root)
    clean_symbol = normalize_symbol(args.symbol)
    effective_start = _causal_warmup_start(store, symbol=clean_symbol, requested_start=args.start)
    history_config = HistoricalDecisionInputConfig(
        pattern_profile=args.pattern_profile,
        max_bars=args.max_bars,
        start_at=effective_start,
        end_at=args.end,
    )

    started = perf_counter()
    try:
        frozen = load_frozen_decision_timeline(store, clean_symbol, config=history_config)
    except DecisionTimelineCacheMiss as exc:
        raise SystemExit(
            "FROZEN_DECISION_TIMELINE_CACHE_MISS\n"
            "BUY/SELL backtest did not replay any domains.\n"
            "Build/refresh the frozen timeline explicitly with:\n"
            f"  python scripts/build_decision_timeline_cache.py {args.cache_root} {clean_symbol}\n"
            "Use identical --start/--end/--max-bars/--pattern-profile options on both commands."
        ) from exc
    frozen_load_seconds = perf_counter() - started
    input_replay = frozen.replay
    if not input_replay.snapshots:
        raise SystemExit("Frozen historical DecisionInput timeline contains no causal snapshots")

    calibration, calibration_label = _calibration(
        args,
        cache_root=args.cache_root,
        symbol=clean_symbol,
    )
    decision_config = DecisionEngineConfig(opportunity_calibration=calibration)
    micro_entry_events, micro_exit_events = detect_30m_execution_events(input_replay.snapshots)
    if args.no_primary_execution:
        entry_events: dict = {}
        exit_events: dict = {}
    else:
        entry_events, exit_events = detect_1h_execution_events(input_replay.snapshots)

    started = perf_counter()
    lifecycle = replay_canonical_trade_lifecycle(
        input_replay.snapshots,
        config=decision_config,
        entry_execution_events=entry_events,
        exit_execution_events=exit_events,
        readiness_execution_proxy=bool(args.canonical_readiness_proxy),
    )
    decisions = canonical_decision_events_from_replay(lifecycle)
    decision_seconds = perf_counter() - started

    bars = store.load(clean_symbol, args.audit_timeframe)
    if bars.empty:
        raise SystemExit(f"No audit bars found for {clean_symbol} {args.audit_timeframe}")
    market_bars_4h = store.load(clean_symbol, "4h")
    if market_bars_4h.empty:
        raise SystemExit(f"No 4H market bars found for {clean_symbol}; research audit cannot run")

    started = perf_counter()
    audit_report = audit_decisions(
        symbol=clean_symbol,
        timeframe=args.audit_timeframe,
        bars=bars,
        decisions=decisions,
        config=DecisionAuditConfig(
            extrema_lookback_bars=args.lookback_bars,
            extrema_lookahead_bars=args.lookahead_bars,
            opportunity_horizon_bars=args.opportunity_horizon_bars,
            swing_radius_bars=args.swing_radius_bars,
            meaningful_move_atr=args.meaningful_move_atr,
            capture_entry_window_bars=args.capture_entry_window_bars,
        ),
    )
    audit_seconds = perf_counter() - started

    started = perf_counter()
    quality_report = audit_trade_quality(
        symbol=clean_symbol,
        timeframe=args.audit_timeframe,
        bars=bars,
        decisions=decisions,
        config=TradeQualityAuditConfig(
            short_lookback_bars=args.short_lookback_bars,
            short_lookahead_bars=args.short_lookahead_bars,
            long_lookback_bars=args.long_lookback_bars,
            long_lookahead_bars=args.long_lookahead_bars,
            fallback_lookback_bars=args.lookback_bars,
            fallback_lookahead_bars=args.lookahead_bars,
        ),
    )
    quality_seconds = perf_counter() - started

    started = perf_counter()
    research_report = audit_buy_sell_research(
        symbol=clean_symbol,
        audit_timeframe=args.audit_timeframe,
        audit_bars=bars,
        market_bars_4h=market_bars_4h,
        decisions=decisions,
        snapshots=input_replay.snapshots,
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
    target_transition_report = audit_target_path_transitions(
        symbol=clean_symbol,
        moves=(row.move for row in research_report.large_moves),
        snapshots=input_replay.snapshots,
        decisions=decisions,
        micro_bars=bars,
        decision_config=decision_config,
        config=TargetTransitionAuditConfig(
            persistence_snapshots=args.target_persistence_snapshots,
            retest_tolerance_pct=args.target_retest_tolerance_pct,
            top_n=max(1, args.worst_trades),
        ),
    )
    target_transition_seconds = perf_counter() - started

    started = perf_counter()
    execution_report = simulate_execution_pnl(
        decisions,
        bars,
        fill_model=args.execution_fill_model,
        spread_bps=args.spread_bps,
        slippage_bps=args.slippage_bps,
        commission_bps=args.commission_bps,
    )
    execution_seconds = perf_counter() - started

    print(f"CAUSAL_WARMUP_START\t{effective_start}")
    print(f"CAUSAL_SNAPSHOTS\t{len(input_replay.snapshots)}")
    print(f"DECISION_EVENTS\t{len(decisions)}")
    print(f"PRIMARY_EXECUTION_TIMEFRAME\t1h")
    print(f"EXECUTION_EVENTS_ENTRY_1H\t{len(entry_events)}")
    print(f"EXECUTION_EVENTS_EXIT_1H\t{len(exit_events)}")
    print(f"MICRO_EVENTS_ENTRY_30M\t{len(micro_entry_events)}")
    print(f"MICRO_EVENTS_EXIT_30M\t{len(micro_exit_events)}")
    print(f"OPPORTUNITY_CALIBRATION\t{calibration_label}")
    print("INPUT_REPLAY_PATH\tFROZEN_DECISION_TIMELINE_CACHE_ONLY")
    print(f"FROZEN_CACHE_STATUS\t{frozen.cache_status}")
    print(f"FROZEN_TIMELINE_LOAD_SECONDS\t{frozen_load_seconds:.3f}")
    print("DOMAIN_REPLAY_AND_SNAPSHOT_SECONDS\t0.00")
    print(f"DECISION_LAYER_SECONDS\t{decision_seconds:.3f}")
    print(f"HINDSIGHT_AUDIT_SECONDS\t{audit_seconds:.3f}")
    print(f"HORIZON_TRADE_QUALITY_SECONDS\t{quality_seconds:.3f}")
    print(f"RESEARCH_AUDIT_SECONDS\t{research_seconds:.3f}")
    print(f"TARGET_TRANSITION_AUDIT_SECONDS\t{target_transition_seconds:.3f}")
    print(f"EXECUTION_PNL_SECONDS\t{execution_seconds:.3f}")
    print(
        "REPLAY_MODE\t"
        + (
            "CANONICAL_1H_PRIMARY_READINESS_PROXY"
            if args.canonical_readiness_proxy
            else "CANONICAL_1H_PRIMARY_REAL_EXECUTION_ONLY"
        )
    )
    print(render_text(audit_report, worst_trade_limit=max(1, args.worst_trades)))
    print()
    print(render_trade_quality_text(quality_report, worst_trade_limit=max(1, args.worst_trades)))
    print()
    print(render_research_text(research_report))
    print()
    print(render_target_path_transition_text(target_transition_report))
    print()
    print("EXECUTION P/L AUDIT")
    print(f"FILL_MODEL\t{execution_report.fill_model}")
    print(f"SPREAD_BPS\t{execution_report.spread_bps}")
    print(f"SLIPPAGE_BPS\t{execution_report.slippage_bps}")
    print(f"COMMISSION_BPS\t{execution_report.commission_bps}")
    print(f"CLOSED_TRADES\t{execution_report.closed_trades}")
    print(f"OPEN_TRADES\t{execution_report.open_trades}")
    print(f"WIN_RATE_PCT\t{execution_report.win_rate_pct}")
    print(f"AVERAGE_NET_RETURN_PCT\t{execution_report.average_net_return_pct}")
    print(f"CUMULATIVE_NET_RETURN_PCT\t{execution_report.cumulative_net_return_pct:.6g}")
    print(f"MAX_DRAWDOWN_PCT\t{execution_report.max_drawdown_pct:.6g}")

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(render_json(audit_report), encoding="utf-8")
        print(f"JSON_REPORT\t{args.json_out}")
    if args.quality_json_out is not None:
        args.quality_json_out.parent.mkdir(parents=True, exist_ok=True)
        args.quality_json_out.write_text(render_trade_quality_json(quality_report), encoding="utf-8")
        print(f"QUALITY_JSON_REPORT\t{args.quality_json_out}")
    if args.research_json_out is not None:
        args.research_json_out.parent.mkdir(parents=True, exist_ok=True)
        args.research_json_out.write_text(render_research_json(research_report), encoding="utf-8")
        print(f"RESEARCH_JSON_REPORT\t{args.research_json_out}")
    if args.timeline_json_out is not None:
        args.timeline_json_out.parent.mkdir(parents=True, exist_ok=True)
        args.timeline_json_out.write_text(_timeline_json(decisions), encoding="utf-8")
        print(f"TIMELINE_JSON\t{args.timeline_json_out}")
    if args.execution_json_out is not None:
        args.execution_json_out.parent.mkdir(parents=True, exist_ok=True)
        args.execution_json_out.write_text(_execution_report_json(execution_report), encoding="utf-8")
        print(f"EXECUTION_JSON_REPORT\t{args.execution_json_out}")

    print("BUY_SELL_BACKTEST_OK")


if __name__ == "__main__":
    main()
