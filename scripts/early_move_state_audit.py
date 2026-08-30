from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.calibration import load_opportunity_calibration
from financial_dashboard.decision.canonical_events import canonical_decision_events_from_replay
from financial_dashboard.decision.engine import DecisionEngineConfig
from financial_dashboard.decision.execution_detect import detect_1h_execution_events
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.lifecycle_replay import replay_canonical_trade_lifecycle
from financial_dashboard.decision.timeline_cache import DecisionTimelineCacheMiss, load_frozen_decision_timeline
from financial_dashboard.decision_audit.early_move_research import (
    EarlyMoveAuditConfig,
    audit_early_move_states,
    render_early_move_text,
)
from financial_dashboard.decision_audit.research import detect_large_market_moves
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
        raise SystemExit("No decision bar exists after all required timeframe histories become causally available")
    if requested_start is None:
        return warmup_start
    return max(warmup_start, _align_requested_start(requested_start, warmup_start))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect missed/captured UP moves at ATR-normalized early progress checkpoints from a frozen timeline."
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
    parser.add_argument("--auto-calibration", action="store_true")
    parser.add_argument("--opportunity-calibration", type=Path, default=None)
    parser.add_argument("--atr-period", type=int, default=14)
    parser.add_argument("--atr-multiples", nargs="+", type=float, default=(0.75, 1.25, 2.0))
    parser.add_argument("--large-move-min-pct", type=float, default=10.0)
    parser.add_argument("--large-move-reversal-pct", type=float, default=5.0)
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

    try:
        frozen = load_frozen_decision_timeline(store, symbol, config=history_config)
    except DecisionTimelineCacheMiss as exc:
        raise SystemExit(
            "FROZEN_DECISION_TIMELINE_CACHE_MISS\n"
            "No domains were replayed. Build/refresh the frozen timeline explicitly with:\n"
            f"  python scripts/build_decision_timeline_cache.py {args.cache_root} {symbol}"
        ) from exc

    calibration_path = args.opportunity_calibration
    if calibration_path is None and args.auto_calibration:
        calibration_path = args.cache_root / "calibration" / "opportunity" / f"{symbol}.json"
    calibration = None
    if calibration_path is not None:
        if not calibration_path.exists():
            raise SystemExit(f"opportunity calibration file is missing: {calibration_path}")
        calibration = load_opportunity_calibration(calibration_path).calibration

    decision_config = DecisionEngineConfig(opportunity_calibration=calibration)
    entry_events, exit_events = detect_1h_execution_events(frozen.replay.snapshots)
    lifecycle = replay_canonical_trade_lifecycle(
        frozen.replay.snapshots,
        config=decision_config,
        entry_execution_events=entry_events,
        exit_execution_events=exit_events,
    )
    decisions = canonical_decision_events_from_replay(lifecycle)

    market_bars_4h = store.load(symbol, "4h")
    if market_bars_4h.empty:
        raise SystemExit(f"No 4H market bars found for {symbol}")
    if decisions:
        first = pd.Timestamp(decisions[0].timestamp)
        last = pd.Timestamp(decisions[-1].timestamp)
        market_bars_4h = market_bars_4h[
            (pd.to_datetime(market_bars_4h["timestamp"]) >= first)
            & (pd.to_datetime(market_bars_4h["timestamp"]) <= last)
        ].reset_index(drop=True)

    moves = detect_large_market_moves(
        market_bars_4h,
        min_move_pct=args.large_move_min_pct,
        reversal_pct=args.large_move_reversal_pct,
    )
    report = audit_early_move_states(
        symbol=symbol,
        moves=moves,
        market_bars_4h=store.load(symbol, "4h"),
        decisions=decisions,
        snapshots=frozen.replay.snapshots,
        decision_config=decision_config,
        config=EarlyMoveAuditConfig(
            atr_period=args.atr_period,
            atr_multiples=tuple(args.atr_multiples),
        ),
    )

    print(f"FROZEN_CACHE_STATUS\t{frozen.cache_status}")
    print("DOMAIN_REPLAY_AND_SNAPSHOT_SECONDS\t0.00")
    print(render_early_move_text(report))
    print("EARLY_MOVE_STATE_AUDIT_OK")


if __name__ == "__main__":
    main()
