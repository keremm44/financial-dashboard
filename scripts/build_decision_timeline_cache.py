from __future__ import annotations

import argparse
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.history_replay import HistoricalDecisionInputReplayRunner
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.persistent_state import PersistentObjectStore
from financial_dashboard.decision.timeline_cache import (
    DecisionTimelineCacheMiss,
    load_frozen_decision_timeline,
)
from financial_dashboard.structure_location_replay import CausalBarClock

import financial_dashboard.decision.history_incremental as incremental_history
import financial_dashboard.decision.history_native_timeline as native_history


_ALIGNMENT_ERROR = "native checkpoint delta is not aligned with the persisted decision prefix"
_BOOTSTRAP_NATIVE_VERSION_SUFFIX = "-decision-bootstrap-full-v1"
_BOOTSTRAP_SUPPORTING_VERSION_SUFFIX = "-decision-bootstrap-full-v1"


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


@contextmanager
def _cold_domain_checkpoint_scope():
    """Force one canonical full domain run without deleting or trusting old checkpoints.

    This changes checkpoint *identity only* for the duration of the explicit builder
    process. Domain semantics/config stay unchanged. The canonical runners therefore
    execute every required historical row once, then the normal DecisionInput runner
    immediately persists the resulting frozen DecisionInput timeline. Existing native
    and supporting checkpoints are left untouched for later append-only continuation.
    """

    native_version = native_history._NATIVE_PERSISTENCE_SEMANTIC_VERSION
    supporting_version = incremental_history._SUPPORTING_PERSISTENCE_SEMANTIC_VERSION
    native_history._NATIVE_PERSISTENCE_SEMANTIC_VERSION = (
        native_version + _BOOTSTRAP_NATIVE_VERSION_SUFFIX
    )
    incremental_history._SUPPORTING_PERSISTENCE_SEMANTIC_VERSION = (
        supporting_version + _BOOTSTRAP_SUPPORTING_VERSION_SUFFIX
    )
    try:
        yield
    finally:
        native_history._NATIVE_PERSISTENCE_SEMANTIC_VERSION = native_version
        incremental_history._SUPPORTING_PERSISTENCE_SEMANTIC_VERSION = supporting_version


def _decision_prefix_exists(
    store: ParquetOHLCVStore,
    *,
    symbol: str,
    config: HistoricalDecisionInputConfig,
    runner: HistoricalDecisionInputReplayRunner,
) -> bool:
    persistent = PersistentObjectStore(store.root)
    identity = runner._decision_checkpoint_identity(symbol=symbol, config=config)
    return persistent.load_checkpoint(identity) is not None


def _build_timeline_once(
    store: ParquetOHLCVStore,
    *,
    symbol: str,
    config: HistoricalDecisionInputConfig,
):
    """Run canonical domains once and persist the exact DecisionInput read-model."""

    clean_symbol = normalize_symbol(symbol)
    runner = HistoricalDecisionInputReplayRunner(store)

    if not _decision_prefix_exists(
        store,
        symbol=clean_symbol,
        config=config,
        runner=runner,
    ):
        print("BUILD_MODE\tCANONICAL_COLD_DOMAIN_ONCE")
        with _cold_domain_checkpoint_scope():
            built = runner.replay(clean_symbol, config=config)
        return runner, built

    print("BUILD_MODE\tCANONICAL_INCREMENTAL_OR_EXACT")
    try:
        built = runner.replay(clean_symbol, config=config)
    except RuntimeError as exc:
        if _ALIGNMENT_ERROR not in str(exc):
            raise
        # A stale/incompatible decision prefix must never push the builder into the
        # retired legacy replay. Re-run canonical native/supporting engines once from
        # their true cold state, then persist the exact DecisionInput timeline.
        print("BUILD_RECOVERY\tCANONICAL_COLD_DOMAIN_ONCE")
        runner = HistoricalDecisionInputReplayRunner(store)
        with _cold_domain_checkpoint_scope():
            built = runner.replay(clean_symbol, config=config)
    return runner, built


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build or verify the exact frozen DecisionInput timeline cache. "
            "Domains run only in this explicit preparation step; BUY/SELL backtests "
            "read the frozen DecisionInput cache only."
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

        started = perf_counter()
        runner, built = _build_timeline_once(
            store,
            symbol=args.symbol,
            config=config,
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
