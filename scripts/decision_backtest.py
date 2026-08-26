from __future__ import annotations

import argparse
from dataclasses import asdict
from json import dumps
from pathlib import Path
from time import perf_counter
from typing import Any

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.historical_stream import (
    HistoricalDecisionStreamConfig,
    decision_events_from_snapshot_stream,
)
from financial_dashboard.decision.history_incremental import (
    IncrementalHistoricalDecisionInputReplayRunner,
)
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.history_single_pass import (
    SinglePassHistoricalDecisionInputReplayRunner,
)
from financial_dashboard.decision.opportunity import OpportunityCalibration
from financial_dashboard.decision.structural import DecisionHorizon
from financial_dashboard.decision_audit import DecisionAuditConfig, audit_decisions, render_json, render_text
from financial_dashboard.structure_location_replay import CausalBarClock


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
    """Return the first decision-bar timestamp with all MTF inputs causally available."""

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
    requested = _align_requested_start(requested_start, warmup_start)
    return max(warmup_start, requested)


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
            "Replay each native market engine forward once, freeze causal 1h decision "
            "states, evaluate the long-only trade lifecycle, then run hindsight audit."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--horizon", choices=("lt", "st"), default="st")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument(
        "--max-bars",
        type=int,
        default=None,
        help="Smoke/debug only: keep the last N 1h decision points; native history is still replayed once.",
    )
    parser.add_argument("--pattern-profile", default=None)
    parser.add_argument(
        "--incremental-state-timeline",
        action="store_true",
        help=(
            "Use the migration candidate backed by the shared historical/live causal reducer. "
            "The legacy single-pass path remains the default until golden equivalence is proven."
        ),
    )
    proxy_group = parser.add_mutually_exclusive_group()
    proxy_group.add_argument(
        "--readiness-position-proxy",
        action="store_true",
        help=(
            "Legacy audit-only proxy: flat+LONG READY => BUY and open-long+SHORT READY => SELL. "
            "Bypasses the dedicated long-exit lifecycle; retained only for historical comparison."
        ),
    )
    proxy_group.add_argument(
        "--lifecycle-readiness-proxy",
        action="store_true",
        help=(
            "Audit-only structural lifecycle baseline: LONG READY substitutes entry execution, "
            "and dedicated EXIT_READY substitutes exit execution. The real FLAT/OPEN lifecycle "
            "and long-exit assessment still run; no production execution trigger is invented."
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
    parser.add_argument(
        "--timeline-json-out",
        type=Path,
        default=None,
        help=(
            "Write every causal decision bar with lifecycle phase, permission, typed domain "
            "states, reasons, blockers and waiting/next-condition diagnostics."
        ),
    )
    args = parser.parse_args()

    horizon = DecisionHorizon.LONG_TERM if args.horizon == "lt" else DecisionHorizon.SHORT_TERM
    calibration = _calibration(args)
    store = ParquetOHLCVStore(args.cache_root)
    effective_start = _causal_warmup_start(
        store,
        symbol=args.symbol,
        requested_start=args.start,
    )

    replay_runner = (
        IncrementalHistoricalDecisionInputReplayRunner(store)
        if args.incremental_state_timeline
        else SinglePassHistoricalDecisionInputReplayRunner(store)
    )
    started = perf_counter()
    input_replay = replay_runner.replay(
        args.symbol,
        config=HistoricalDecisionInputConfig(
            pattern_profile=args.pattern_profile,
            max_bars=args.max_bars,
            start_at=effective_start,
            end_at=args.end,
        ),
    )
    input_seconds = perf_counter() - started
    if not input_replay.snapshots:
        raise SystemExit("Historical input replay produced no causal decision snapshots")

    started = perf_counter()
    decisions = decision_events_from_snapshot_stream(
        input_replay.snapshots,
        config=HistoricalDecisionStreamConfig(
            horizon=horizon,
            opportunity_calibration=calibration,
            readiness_position_proxy=bool(args.readiness_position_proxy),
            lifecycle_readiness_proxy=bool(args.lifecycle_readiness_proxy),
        ),
    )
    decision_seconds = perf_counter() - started

    bars = store.load(args.symbol, args.audit_timeframe)
    if bars.empty:
        raise SystemExit(f"No audit bars found for {args.symbol} {args.audit_timeframe}")

    started = perf_counter()
    report = audit_decisions(
        symbol=args.symbol,
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

    timings = input_replay.timings
    print(f"CAUSAL_WARMUP_START\t{effective_start}")
    print(f"CAUSAL_SNAPSHOTS\t{len(input_replay.snapshots)}")
    print(f"DECISION_EVENTS\t{len(decisions)}")
    print(
        "INPUT_REPLAY_PATH\t"
        + ("INCREMENTAL_STATE_TIMELINE" if args.incremental_state_timeline else "LEGACY_SINGLE_PASS")
    )
    print(f"LOAD_INPUTS_SECONDS\t{timings.load_inputs_seconds:.2f}")
    print(f"NATIVE_CAPTURE_PASS_SECONDS\t{timings.native_capture_pass_seconds:.2f}")
    print(f"HAM_REPLAY_SECONDS\t{timings.ham_seconds:.2f}")
    print(f"VOLUME_REPLAY_SECONDS\t{timings.volume_seconds:.2f}")
    print(f"VOLATILITY_REPLAY_SECONDS\t{timings.volatility_seconds:.2f}")
    print(f"STABIL_REPLAY_SECONDS\t{timings.stabil_seconds:.2f}")
    print(f"NATIVE_REPLAY_SECONDS\t{timings.native_replay_seconds:.2f}")
    print(f"SNAPSHOT_ASSEMBLY_SECONDS\t{timings.snapshot_assembly_seconds:.2f}")
    print(f"DOMAIN_REPLAY_AND_SNAPSHOT_SECONDS\t{input_seconds:.2f}")
    print(f"DECISION_LAYER_SECONDS\t{decision_seconds:.2f}")
    print(f"HINDSIGHT_AUDIT_SECONDS\t{audit_seconds:.2f}")
    if args.readiness_position_proxy:
        replay_mode = "LEGACY_READINESS_POSITION_PROXY"
    elif args.lifecycle_readiness_proxy:
        replay_mode = "LIFECYCLE_READINESS_PROXY"
    else:
        replay_mode = "CAUSAL_TRADE_LIFECYCLE"
    print(f"REPLAY_MODE\t{replay_mode}")
    if calibration is None:
        print("OPPORTUNITY_CALIBRATION\tUNSET")
    print(render_text(report, worst_trade_limit=max(1, args.worst_trades)))

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(render_json(report), encoding="utf-8")
        print(f"JSON_REPORT\t{args.json_out}")
    if args.timeline_json_out is not None:
        args.timeline_json_out.parent.mkdir(parents=True, exist_ok=True)
        args.timeline_json_out.write_text(_timeline_json(decisions), encoding="utf-8")
        print(f"TIMELINE_JSON\t{args.timeline_json_out}")
    print("DECISION_BACKTEST_OK")


if __name__ == "__main__":
    main()
