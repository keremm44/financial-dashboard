from __future__ import annotations

import argparse
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter
from typing import Any

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.history_replay import HistoricalDecisionInputReplayRunner
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.native_domain_runtime import IncrementalNativeDomainRuntime
from financial_dashboard.decision.persistent_state import PersistentObjectStore
from financial_dashboard.decision.supporting_replay_runtime import IncrementalSupportingReplayRuntime
from financial_dashboard.decision.timeline_cache import (
    DecisionTimelineCacheMiss,
    load_frozen_decision_timeline,
)
from financial_dashboard.engines.fvg_engulfing import FvgEngulfingEngine
from financial_dashboard.engines.ham_evidence import HamEvidenceEngine
from financial_dashboard.engines.liquidity_engine import LiquidityEngine
from financial_dashboard.engines.market_structure_engine import MarketStructureEngine
from financial_dashboard.engines.order_block import OrderBlockEngine
from financial_dashboard.engines.order_block_behavior import OrderBlockBehaviorTracker
from financial_dashboard.engines.pattern_compression_runtime_engine import RuntimePatternCompressionEngine
from financial_dashboard.engines.support_resistance_runtime_engine import RuntimeSupportResistanceRangeEngine
from financial_dashboard.engines.volume_evidence import VolumeEvidenceEngine
from financial_dashboard.engines.volatility_direction_runtime import RuntimeVolatilityDirectionTransitionEngine
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
    """Force one canonical full domain run without deleting or trusting old checkpoints."""

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


@contextmanager
def _live_domain_timing_scope():
    """Print real per-timeframe engine update time while the explicit builder runs.

    Instrumentation is process-local and builder-only. Every patched class method and
    runtime constructor is restored in ``finally`` so production/live semantics remain
    untouched. Timings are accumulated from the actual engine ``update`` calls; no
    estimated or synthetic durations are printed.
    """

    registry: dict[int, dict[str, Any]] = {}
    originals: dict[type, Any] = {}
    native_init = IncrementalNativeDomainRuntime.__init__
    supporting_init = IncrementalSupportingReplayRuntime.__init__

    def register(engine: Any, timeframe: str, domain: str, expected: int) -> None:
        if engine is None:
            return
        registry[id(engine)] = {
            "timeframe": timeframe,
            "domain": domain,
            "expected": int(expected),
            "count": 0,
            "seconds": 0.0,
            "started": False,
        }

    def install_update_wrapper(cls: type) -> None:
        original = cls.update
        originals[cls] = original

        def wrapped(self, *args, __original=original, **kwargs):
            meta = registry.get(id(self))
            if meta is None:
                return __original(self, *args, **kwargs)
            if not meta["started"]:
                meta["started"] = True
                print(
                    f"DOMAIN_START\t{meta['timeframe']}\t{meta['domain']}\t"
                    f"bars={meta['expected']}",
                    flush=True,
                )
            started = perf_counter()
            result = __original(self, *args, **kwargs)
            meta["seconds"] += perf_counter() - started
            meta["count"] += 1
            if meta["count"] == meta["expected"]:
                print(
                    f"DOMAIN_DONE\t{meta['timeframe']}\t{meta['domain']}\t"
                    f"{meta['seconds']:.3f}s\tbars={meta['expected']}",
                    flush=True,
                )
            return result

        cls.update = wrapped

    def timed_native_init(self, *args, **kwargs):
        native_init(self, *args, **kwargs)
        for timeframe, runtime in self._runtimes.items():
            expected = len(self.inputs.for_timeframe(timeframe).input_batch.frame)
            register(runtime.market, timeframe, "market_structure", expected)
            register(runtime.support, timeframe, "support_resistance", expected)
            register(runtime.pattern, timeframe, "pattern", expected)
            register(runtime.liquidity, timeframe, "liquidity", expected)
            register(runtime.order_block, timeframe, "order_block", expected)
            register(runtime.order_block_behavior, timeframe, "order_block_behavior", expected)
            register(runtime.fvg, timeframe, "fvg_engulfing", expected)

    def timed_supporting_init(self, *args, **kwargs):
        supporting_init(self, *args, **kwargs)
        for timeframe in self.inputs.timeframes:
            expected = len(self.inputs.for_timeframe(timeframe).input_batch.frame)
            register(self._ham[timeframe].engine, timeframe, "ham", expected)
            register(self._volume[timeframe].engine, timeframe, "volume", expected)
            if timeframe in self._volatility:
                register(self._volatility[timeframe].engine, timeframe, "volatility", expected)

    classes = (
        MarketStructureEngine,
        RuntimeSupportResistanceRangeEngine,
        RuntimePatternCompressionEngine,
        LiquidityEngine,
        OrderBlockEngine,
        OrderBlockBehaviorTracker,
        FvgEngulfingEngine,
        HamEvidenceEngine,
        VolumeEvidenceEngine,
        RuntimeVolatilityDirectionTransitionEngine,
    )
    for cls in classes:
        install_update_wrapper(cls)
    IncrementalNativeDomainRuntime.__init__ = timed_native_init
    IncrementalSupportingReplayRuntime.__init__ = timed_supporting_init
    print("DOMAIN_TIMING\tLIVE", flush=True)
    try:
        yield
    finally:
        IncrementalNativeDomainRuntime.__init__ = native_init
        IncrementalSupportingReplayRuntime.__init__ = supporting_init
        for cls, original in originals.items():
            cls.update = original


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


def _run_with_live_timings(runner, symbol: str, config: HistoricalDecisionInputConfig):
    with _live_domain_timing_scope():
        return runner.replay(symbol, config=config)


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
            built = _run_with_live_timings(runner, clean_symbol, config)
        return runner, built

    print("BUILD_MODE\tCANONICAL_INCREMENTAL_OR_EXACT")
    try:
        built = _run_with_live_timings(runner, clean_symbol, config)
    except RuntimeError as exc:
        if _ALIGNMENT_ERROR not in str(exc):
            raise
        print("BUILD_RECOVERY\tCANONICAL_COLD_DOMAIN_ONCE")
        runner = HistoricalDecisionInputReplayRunner(store)
        with _cold_domain_checkpoint_scope():
            built = _run_with_live_timings(runner, clean_symbol, config)
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
