from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.analysis_inputs import load_analysis_inputs
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.history_replay import (
    HistoricalDecisionInputReplayRunner,
    LegacyHistoricalDecisionInputReplayRunner,
)
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.persistent_state import PersistentObjectStore
from financial_dashboard.decision.timeline_cache import (
    DecisionTimelineCacheMiss,
    load_frozen_decision_timeline,
)
from financial_dashboard.structure_location_replay import CausalBarClock


_ALIGNMENT_ERROR = "native checkpoint delta is not aligned with the persisted decision prefix"


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


def _bootstrap_full_timeline(
    store: ParquetOHLCVStore,
    *,
    symbol: str,
    config: HistoricalDecisionInputConfig,
    canonical_runner: HistoricalDecisionInputReplayRunner,
):
    """Build a missing DecisionInput prefix without trusting delta-only checkpoints.

    This recovery is intentionally confined to the explicit cache-builder command.
    It does not alter BUY/SELL, does not delete existing domain checkpoints, and does
    not make the cache-only BUY/SELL backtest capable of cold replay.
    """

    cold_runner = LegacyHistoricalDecisionInputReplayRunner(store)
    built = cold_runner.replay(symbol, config=config)
    inputs = load_analysis_inputs(
        store,
        symbol=symbol,
        timeframes=ANALYSIS_TIMEFRAMES,
    )
    persistent = PersistentObjectStore(store.root)
    canonical_runner._save_decision_checkpoints(
        persistent=persistent,
        exact_identity=canonical_runner._cache_identity(symbol=symbol, config=config),
        append_identity=canonical_runner._decision_checkpoint_identity(symbol=symbol, config=config),
        inputs=inputs,
        result=built,
    )
    canonical_runner.last_native_checkpoint_status = "BYPASSED_FOR_COLD_DECISION_BOOTSTRAP"
    canonical_runner.last_supporting_checkpoint_status = "BYPASSED_FOR_COLD_DECISION_BOOTSTRAP"
    return built


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build or verify the exact frozen DecisionInput timeline cache. "
            "This is the only command that may replay domains for BUY/SELL backtests."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
    args = parser.parse_args()

    store = ParquetOHLCVStore(args.cache_root)
    effective_start = _causal_warmup_start(
        store,
        symbol=args.symbol,
        requested_start=args.start,
    )
    config = HistoricalDecisionInputConfig(
        pattern_profile=args.pattern_profile,
        max_bars=args.max_bars,
        start_at=effective_start,
        end_at=args.end,
    )

    started = perf_counter()
    try:
        cached_load = load_frozen_decision_timeline(store, args.symbol, config=config)
    except DecisionTimelineCacheMiss:
        probe_seconds = perf_counter() - started
        print(f"CACHE_PROBE_SECONDS\t{probe_seconds:.3f}")
        print("CACHE_STATUS\tMISS_BUILDING")

        runner = HistoricalDecisionInputReplayRunner(store)
        started = perf_counter()
        try:
            built = runner.replay(args.symbol, config=config)
        except RuntimeError as exc:
            if _ALIGNMENT_ERROR not in str(exc):
                raise
            print("BUILD_RECOVERY\tCOLD_DECISION_TIMELINE_BOOTSTRAP")
            built = _bootstrap_full_timeline(
                store,
                symbol=args.symbol,
                config=config,
                canonical_runner=runner,
            )
        build_seconds = perf_counter() - started
        print(f"BUILD_SECONDS\t{build_seconds:.3f}")
        print(f"BUILD_STATUS\t{runner.last_persistent_cache_status}")
        print(f"APPEND_STATUS\t{runner.last_decision_append_status}")
        print(f"NATIVE_STATUS\t{runner.last_native_checkpoint_status}")
        print(f"SUPPORTING_STATUS\t{runner.last_supporting_checkpoint_status}")
        print(f"SNAPSHOTS\t{len(built.snapshots)}")

        started = perf_counter()
        try:
            cached_load = load_frozen_decision_timeline(store, args.symbol, config=config)
        except DecisionTimelineCacheMiss as exc:
            raise SystemExit(
                "DecisionInput timeline was computed but exact cache verification failed; "
                "do not run BUY/SELL backtest yet."
            ) from exc
        verify_seconds = perf_counter() - started
        print(f"VERIFY_LOAD_SECONDS\t{verify_seconds:.3f}")
        print(f"VERIFY_STATUS\t{cached_load.cache_status}")
    else:
        load_seconds = perf_counter() - started
        print(f"CACHE_LOAD_SECONDS\t{load_seconds:.3f}")
        print(f"CACHE_STATUS\t{cached_load.cache_status}")
        print(f"SNAPSHOTS\t{len(cached_load.replay.snapshots)}")

    print(f"CAUSAL_WARMUP_START\t{effective_start}")
    print("DECISION_TIMELINE_CACHE_READY")


if __name__ == "__main__":
    main()
