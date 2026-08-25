from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from types import MappingProxyType
from typing import Any, Callable, Iterator

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.analysis_inputs import AnalysisInputSnapshot, load_analysis_inputs
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision import history_single_pass as hs
from financial_dashboard.engines.fvg_engulfing import FvgEngulfingEngine
from financial_dashboard.engines.liquidity_engine import LiquidityEngine
from financial_dashboard.engines.market_structure_engine import MarketStructureEngine
from financial_dashboard.engines.order_block import OrderBlockEngine
from financial_dashboard.engines.order_block_behavior import OrderBlockBehaviorTracker
from financial_dashboard.engines.pattern_compression_runtime_engine import RuntimePatternCompressionEngine
from financial_dashboard.engines.support_resistance_runtime_engine import RuntimeSupportResistanceRangeEngine
from financial_dashboard.ham_mtf_replay import HamMTFEvidenceReplayRunner
from financial_dashboard.structure_location_replay import CausalBarClock
from financial_dashboard.volume_mtf_replay import VolumeMTFEvidenceReplayRunner
from financial_dashboard.volatility_mtf_replay import VOLATILITY_TIMEFRAMES, VolatilityMTFReplayRunner


@contextmanager
def _timed_attr(owner: Any, name: str, bucket: dict[str, float], key: str) -> Iterator[None]:
    original = getattr(owner, name)

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        started = perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            bucket[key] += perf_counter() - started

    setattr(owner, name, wrapped)
    try:
        yield
    finally:
        setattr(owner, name, original)


def _single_timeframe_inputs(inputs: AnalysisInputSnapshot, timeframe: str) -> AnalysisInputSnapshot:
    row = inputs.for_timeframe(timeframe)
    fingerprint = tuple(item for item in inputs.fingerprint if item[0] == timeframe)
    return AnalysisInputSnapshot(
        symbol=inputs.symbol,
        timeframes=(timeframe,),
        by_timeframe=MappingProxyType({timeframe: row}),
        fingerprint=fingerprint,
    )


def _causal_cutoffs(inputs: AnalysisInputSnapshot, clock: CausalBarClock) -> tuple[pd.Timestamp, ...]:
    first_available = []
    for timeframe in inputs.timeframes:
        frame = inputs.for_timeframe(timeframe).input_batch.frame
        if frame.empty:
            raise SystemExit(f"No bars for required timeframe {timeframe}")
        first_available.append(pd.Timestamp(clock.available_at(frame.iloc[0]["timestamp"], timeframe)))
    common_start = max(first_available)

    decision_frame = inputs.for_timeframe("1h").input_batch.frame
    return tuple(
        available
        for value in decision_frame["timestamp"]
        if (available := pd.Timestamp(clock.available_at(value, "1h"))) >= common_start
    )


def _profile_native_timeframe(
    inputs: AnalysisInputSnapshot,
    *,
    symbol: str,
    timeframe: str,
    indices: tuple[int, ...],
    clock: CausalBarClock,
    pattern_profile: str | None,
) -> tuple[Any, dict[str, float], float]:
    filtered = _single_timeframe_inputs(inputs, timeframe)
    bucket: dict[str, float] = defaultdict(float)

    update_specs: tuple[tuple[Any, str, str], ...] = (
        (MarketStructureEngine, "update", "STRUCTURE_UPDATE"),
        (RuntimeSupportResistanceRangeEngine, "update", "SR_UPDATE"),
        (RuntimePatternCompressionEngine, "update", "PATTERN_UPDATE"),
        (LiquidityEngine, "update", "LIQUIDITY_UPDATE"),
        (OrderBlockEngine, "update", "OB_UPDATE"),
        (OrderBlockBehaviorTracker, "update", "OB_UPDATE"),
        (FvgEngulfingEngine, "update", "FVG_UPDATE"),
    )
    capture_specs: tuple[tuple[Any, str, str], ...] = (
        (hs, "market_structure_timeframe_snapshot", "STRUCTURE_CAPTURE"),
        (hs, "_support_snapshot", "SR_CAPTURE"),
        (hs, "_pattern_snapshot", "PATTERN_CAPTURE"),
        (hs, "liquidity_evidence", "LIQUIDITY_CAPTURE"),
        (hs, "order_block_evidence", "OB_CAPTURE"),
        (hs, "fvg_engulfing_evidence", "FVG_CAPTURE"),
    )

    with ExitStack() as stack:
        for owner, name, key in (*update_specs, *capture_specs):
            stack.enter_context(_timed_attr(owner, name, bucket, key))
        started = perf_counter()
        native = hs._single_native_capture_pass(
            filtered,
            symbol=symbol,
            capture_indices={timeframe: indices},
            clock=clock,
            pattern_profile=pattern_profile,
        )
        total = perf_counter() - started

    return native, dict(bucket), total


def _print_native_rows(timeframe: str, timings: dict[str, float], total: float) -> None:
    domains = (
        ("STRUCTURE", "STRUCTURE_UPDATE", "STRUCTURE_CAPTURE"),
        ("SR", "SR_UPDATE", "SR_CAPTURE"),
        ("PATTERN", "PATTERN_UPDATE", "PATTERN_CAPTURE"),
        ("LIQUIDITY", "LIQUIDITY_UPDATE", "LIQUIDITY_CAPTURE"),
        ("OB", "OB_UPDATE", "OB_CAPTURE"),
        ("FVG", "FVG_UPDATE", "FVG_CAPTURE"),
    )
    measured = 0.0
    for domain, update_key, capture_key in domains:
        update_seconds = timings.get(update_key, 0.0)
        capture_seconds = timings.get(capture_key, 0.0)
        measured += update_seconds + capture_seconds
        print(
            f"PROFILE\t{timeframe}\t{domain}\tUPDATE={update_seconds:.3f}\t"
            f"CAPTURE={capture_seconds:.3f}\tMEASURED={update_seconds + capture_seconds:.3f}",
            flush=True,
        )
    print(
        f"PROFILE\t{timeframe}\tNATIVE_PASS\tTOTAL={total:.3f}\t"
        f"UNATTRIBUTED={max(0.0, total - measured):.3f}",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Profile the historical BUY/SELL input path by timeframe and domain without "
            "changing decision semantics. Native engines are timed as update vs capture work."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--pattern-profile", default=None)
    parser.add_argument(
        "--max-decision-bars",
        type=int,
        default=None,
        help=(
            "Optional profiler-only suffix of causal 1h decision cutoffs. Omit for the full "
            "historical capture workload."
        ),
    )
    args = parser.parse_args()

    store = ParquetOHLCVStore(args.cache_root)
    clock = CausalBarClock()

    started = perf_counter()
    inputs = load_analysis_inputs(store, symbol=args.symbol, timeframes=ANALYSIS_TIMEFRAMES)
    print(f"PROFILE\tALL\tLOAD_INPUTS\tTOTAL={perf_counter() - started:.3f}", flush=True)

    cutoffs = _causal_cutoffs(inputs, clock)
    if args.max_decision_bars is not None:
        if args.max_decision_bars < 1:
            raise SystemExit("--max-decision-bars must be >= 1")
        cutoffs = cutoffs[-args.max_decision_bars :]
    if not cutoffs:
        raise SystemExit("No causal 1h decision cutoffs available")

    capture_indices = hs._capture_indices(inputs, cutoffs=cutoffs, clock=clock)
    print(f"PROFILE\tALL\tDECISION_POINTS\tCOUNT={len(cutoffs)}", flush=True)

    native_by_timeframe: dict[str, Any] = {}
    total_started = perf_counter()

    for timeframe in inputs.timeframes:
        native, timings, total = _profile_native_timeframe(
            inputs,
            symbol=inputs.symbol,
            timeframe=timeframe,
            indices=capture_indices[timeframe],
            clock=clock,
            pattern_profile=args.pattern_profile,
        )
        native_by_timeframe[timeframe] = native
        _print_native_rows(timeframe, timings, total)

        filtered = _single_timeframe_inputs(inputs, timeframe)

        started = perf_counter()
        HamMTFEvidenceReplayRunner(store).replay(
            inputs.symbol,
            timeframes=(timeframe,),
            input_snapshot=filtered,
        )
        print(
            f"PROFILE\t{timeframe}\tHAM\tTOTAL={perf_counter() - started:.3f}",
            flush=True,
        )

        started = perf_counter()
        VolumeMTFEvidenceReplayRunner(store).replay(
            inputs.symbol,
            timeframes=(timeframe,),
            structure_replay=native.full_structure,
            input_snapshot=filtered,
        )
        print(
            f"PROFILE\t{timeframe}\tVOLUME\tTOTAL={perf_counter() - started:.3f}",
            flush=True,
        )

        if timeframe in VOLATILITY_TIMEFRAMES:
            started = perf_counter()
            VolatilityMTFReplayRunner(store).replay(
                inputs.symbol,
                input_snapshot=filtered,
                timeframes=(timeframe,),
            )
            print(
                f"PROFILE\t{timeframe}\tVOLATILITY\tTOTAL={perf_counter() - started:.3f}",
                flush=True,
            )
        else:
            print(f"PROFILE\t{timeframe}\tVOLATILITY\tN/A", flush=True)

        if timeframe == "1d":
            started = perf_counter()
            hs._stabil_points(inputs, indices_1d=capture_indices["1d"])
            print(
                f"PROFILE\t{timeframe}\tSTABIL\tTOTAL={perf_counter() - started:.3f}",
                flush=True,
            )
        else:
            print(f"PROFILE\t{timeframe}\tSTABIL\tN/A", flush=True)

    print(
        f"PROFILE\tALL\tTOTAL_PROFILE_SECONDS\tTOTAL={perf_counter() - total_started:.3f}",
        flush=True,
    )
    print("DECISION_DOMAIN_PROFILE_OK", flush=True)


if __name__ == "__main__":
    main()
