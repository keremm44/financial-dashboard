from __future__ import annotations

import argparse
from dataclasses import asdict
from json import dumps
from pathlib import Path
from time import perf_counter
from typing import Any

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.calibration import load_opportunity_calibration
from financial_dashboard.decision.canonical_events import canonical_decision_events_from_replay
from financial_dashboard.decision.engine import DecisionEngineConfig
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
from financial_dashboard.structure_location_replay import CausalBarClock


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
) -> tuple[OpportunityCalibration, str]:
    manual = (
        args.opportunity_none_max_atr,
        args.opportunity_compressed_max_atr,
        args.opportunity_moderate_max_atr,
    )
    has_manual = any(value is not None for value in manual)
    has_file = args.opportunity_calibration is not None or args.auto_calibration
    if has_manual and has_file:
        raise SystemExit(
            "manual opportunity boundaries cannot be combined with "
            "--opportunity-calibration/--auto-calibration"
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
    clean_symbol = normalize_symbol(symbol)
    if path is None and args.auto_calibration:
        path = cache_root / "calibration" / "opportunity" / f"{clean_symbol}.json"
    if path is None:
        raise SystemExit(
            "OPPORTUNITY_CALIBRATION_REQUIRED\n"
            "Canonical BUY/SELL replay cannot classify opportunity without explicit Class-C "
            "calibration. Build the per-symbol artifact first with:\n"
            f"  python scripts/build_opportunity_calibration.py {cache_root} {clean_symbol}\n"
            "Then rerun this command with --auto-calibration."
        )
    if not Path(path).exists():
        raise SystemExit(
            f"opportunity calibration file is missing: {path}. Build it with "
            f"`python scripts/build_opportunity_calibration.py {cache_root} {clean_symbol}`"
        )
    record = load_opportunity_calibration(path)
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run only the canonical BUY/SELL lifecycle and hindsight audits from an exact "
            "frozen DecisionInput timeline. This command never replays market domains."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
    parser.add_argument("--canonical-readiness-proxy", action="store_true")
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
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--quality-json-out", type=Path, default=None)
    parser.add_argument("--timeline-json-out", type=Path, default=None)
    args = parser.parse_args()

    store = ParquetOHLCVStore(args.cache_root)
    clean_symbol = normalize_symbol(args.symbol)
    effective_start = _causal_warmup_start(
        store,
        symbol=clean_symbol,
        requested_start=args.start,
    )
    history_config = HistoricalDecisionInputConfig(
        pattern_profile=args.pattern_profile,
        max_bars=args.max_bars,
        start_at=effective_start,
        end_at=args.end,
    )

    started = perf_counter()
    try:
        frozen = load_frozen_decision_timeline(
            store,
            clean_symbol,
            config=history_config,
        )
    except DecisionTimelineCacheMiss as exc:
        raise SystemExit(
            "FROZEN_DECISION_TIMELINE_CACHE_MISS\n"
            "BUY/SELL backtest did not replay any domains.\n"
            "Build/refresh the frozen timeline first with:\n"
            f"  python scripts/build_decision_timeline_cache.py {args.cache_root} {clean_symbol}\n"
            "Use identical --start/--end/--max-bars/--pattern-profile options on both commands."
        ) from exc
    frozen_load_seconds = perf_counter() - started
    input_replay = frozen.replay
    if not input_replay.snapshots:
        raise SystemExit("Frozen historical DecisionInput timeline contains no causal snapshots")

    calibration, calibration_source = _calibration(
        args,
        cache_root=args.cache_root,
        symbol=clean_symbol,
    )
    started = perf_counter()
    lifecycle = replay_canonical_trade_lifecycle(
        input_replay.snapshots,
        config=DecisionEngineConfig(opportunity_calibration=calibration),
        readiness_execution_proxy=bool(args.canonical_readiness_proxy),
    )
    decisions = canonical_decision_events_from_replay(lifecycle)
    decision_seconds = perf_counter() - started

    bars = store.load(clean_symbol, args.audit_timeframe)
    if bars.empty:
        raise SystemExit(f"No audit bars found for {clean_symbol} {args.audit_timeframe}")

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

    print(f"CAUSAL_WARMUP_START\t{effective_start}")
    print(f"CAUSAL_SNAPSHOTS\t{len(input_replay.snapshots)}")
    print(f"DECISION_EVENTS\t{len(decisions)}")
    print("INPUT_REPLAY_PATH\tFROZEN_DECISION_TIMELINE_CACHE_ONLY")
    print(f"FROZEN_CACHE_STATUS\t{frozen.cache_status}")
    print(f"OPPORTUNITY_CALIBRATION\t{calibration_source}")
    print(f"FROZEN_TIMELINE_LOAD_SECONDS\t{frozen_load_seconds:.3f}")
    print("DOMAIN_REPLAY_AND_SNAPSHOT_SECONDS\t0.00")
    print(f"DECISION_LAYER_SECONDS\t{decision_seconds:.3f}")
    print(f"HINDSIGHT_AUDIT_SECONDS\t{audit_seconds:.3f}")
    print(f"HORIZON_TRADE_QUALITY_SECONDS\t{quality_seconds:.3f}")
    print(
        "REPLAY_MODE\t"
        + (
            "CANONICAL_TURN4_9_READINESS_PROXY"
            if args.canonical_readiness_proxy
            else "CANONICAL_TURN4_9_REAL_EXECUTION_ONLY"
        )
    )
    print(render_text(audit_report, worst_trade_limit=max(1, args.worst_trades)))
    print()
    print(render_trade_quality_text(quality_report, worst_trade_limit=max(1, args.worst_trades)))

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(render_json(audit_report), encoding="utf-8")
        print(f"JSON_REPORT\t{args.json_out}")
    if args.quality_json_out is not None:
        args.quality_json_out.parent.mkdir(parents=True, exist_ok=True)
        args.quality_json_out.write_text(render_trade_quality_json(quality_report), encoding="utf-8")
        print(f"QUALITY_JSON_REPORT\t{args.quality_json_out}")
    if args.timeline_json_out is not None:
        args.timeline_json_out.parent.mkdir(parents=True, exist_ok=True)
        args.timeline_json_out.write_text(_timeline_json(decisions), encoding="utf-8")
        print(f"TIMELINE_JSON\t{args.timeline_json_out}")

    print("BUY_SELL_BACKTEST_OK")


if __name__ == "__main__":
    main()
