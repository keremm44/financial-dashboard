from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.calibration import (
    OpportunityCalibrationRecord,
    save_opportunity_calibration,
)
from financial_dashboard.decision.engine import assess_horizon_decision
from financial_dashboard.decision.history_replay import HistoricalDecisionInputReplayRunner
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.persistent_state import PersistentObjectStore
from financial_dashboard.decision.opportunity import OpportunityCalibration
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection
from financial_dashboard.decision.timeline_cache import load_frozen_decision_timeline
from financial_dashboard.structure_location_replay import CausalBarClock

DECISION_TIMEFRAME = "1h"


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
    decision_frame = store.load(symbol, DECISION_TIMEFRAME)
    if decision_frame.empty:
        raise SystemExit(f"No historical bars found for {symbol} {DECISION_TIMEFRAME}")

    for value in decision_frame["timestamp"]:
        timestamp = pd.Timestamp(value)
        if pd.Timestamp(clock.available_at(timestamp, DECISION_TIMEFRAME)) >= common_cutoff:
            warmup_start = timestamp
            break
    else:
        raise SystemExit(
            "No decision bar exists after all required timeframe histories become causally available"
        )
    if requested_start is None:
        return warmup_start
    return max(warmup_start, _align_requested_start(requested_start, warmup_start))


def _parse_quantiles(raw: str) -> tuple[float, float, float]:
    parts = [float(item.strip()) for item in raw.split(",") if item.strip()]
    if len(parts) != 3:
        raise SystemExit("--quantiles requires exactly three values, e.g. 0.25,0.5,0.75")
    if not all(0.0 < value < 1.0 for value in parts):
        raise SystemExit("quantile values must lie strictly between 0 and 1")
    if not parts[0] < parts[1] < parts[2]:
        raise SystemExit("quantile values must be strictly increasing")
    return parts[0], parts[1], parts[2]


def _normalize_timestamp(value) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        return timestamp.tz_convert(None)
    return timestamp


def _forward_mfe_atr(
    *,
    direction: StructuralDirection,
    entry_price: float,
    frame: pd.DataFrame,
    positions: pd.Index,
    as_of: pd.Timestamp,
    forward_bars: int,
    reference_atr: float,
) -> float | None:
    located = positions.get_indexer([_normalize_timestamp(as_of)])
    position = int(located[0]) if len(located) else -1
    if position < 0:
        return None
    window = frame.iloc[position + 1 : position + 1 + forward_bars]
    if len(window) < forward_bars:
        return None
    if not reference_atr > 0:
        return None
    if direction is StructuralDirection.LONG:
        extreme = float(window["high"].max())
        return (extreme - entry_price) / reference_atr
    extreme = float(window["low"].min())
    return (entry_price - extreme) / reference_atr


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build deterministic per-symbol OpportunityCalibration boundaries from a "
            "frozen DecisionInput timeline plus forward MFE realised in ATR units."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
    parser.add_argument("--forward-bars", type=int, default=24)
    parser.add_argument("--quantiles", default="0.25,0.5,0.75")
    parser.add_argument("--min-samples", type=int, default=50)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    if args.forward_bars < 1:
        raise SystemExit("--forward-bars must be >= 1")
    q1, q2, q3 = _parse_quantiles(args.quantiles)

    store = ParquetOHLCVStore(args.cache_root)
    clean_symbol = normalize_symbol(args.symbol)
    effective_start = _causal_warmup_start(
        store,
        symbol=clean_symbol,
        requested_start=args.start,
    )
    config = HistoricalDecisionInputConfig(
        pattern_profile=args.pattern_profile,
        max_bars=args.max_bars,
        start_at=effective_start,
        end_at=args.end,
    )

    runner = HistoricalDecisionInputReplayRunner(store)
    identity = runner._cache_identity(symbol=clean_symbol, config=config)

    started = perf_counter()
    frozen = load_frozen_decision_timeline(store, clean_symbol, config=config)
    load_seconds = perf_counter() - started
    snapshots = frozen.replay.snapshots

    decision_frame = store.load(clean_symbol, DECISION_TIMEFRAME)
    positions = pd.Index([_normalize_timestamp(value) for value in decision_frame["timestamp"]])

    mfe_samples: list[float] = []
    for snapshot in snapshots:
        targeting = snapshot.targeting
        if targeting is None:
            continue
        reference_atr = float(targeting.reference_atr)
        for horizon in (DecisionHorizon.LONG_TERM, DecisionHorizon.SHORT_TERM):
            assessment = assess_horizon_decision(snapshot, horizon)
            direction = assessment.structural.direction
            if direction not in {StructuralDirection.LONG, StructuralDirection.SHORT}:
                continue
            if assessment.opportunity.room_atr is None:
                continue
            mfe = _forward_mfe_atr(
                direction=direction,
                entry_price=float(snapshot.current_price),
                frame=decision_frame,
                positions=positions,
                as_of=pd.Timestamp(snapshot.as_of),
                forward_bars=args.forward_bars,
                reference_atr=reference_atr,
            )
            if mfe is None:
                continue
            mfe_samples.append(mfe)

    if len(mfe_samples) < args.min_samples:
        raise SystemExit(
            f"only {len(mfe_samples)} calibration samples available; "
            f"--min-samples requires {args.min_samples}"
        )

    series = pd.Series(mfe_samples)
    none_max = float(series.quantile(q1))
    compressed_max = float(series.quantile(q2))
    moderate_max = float(series.quantile(q3))
    try:
        calibration = OpportunityCalibration(none_max, compressed_max, moderate_max)
    except ValueError as error:
        raise SystemExit(
            f"degenerate MFE distribution produced invalid boundaries "
            f"({none_max=:.6g}, {compressed_max=:.6g}, {moderate_max=:.6g}): {error}"
        ) from error

    record = OpportunityCalibrationRecord(
        calibration=calibration,
        symbol=clean_symbol,
        sample_size=len(mfe_samples),
        version=1,
        meta={
            "forward_bars": args.forward_bars,
            "quantiles": [q1, q2, q3],
            "reference_timeframe": DECISION_TIMEFRAME,
            "source_identity": identity,
        },
    )

    output = args.output or (
        Path(args.cache_root) / "calibration" / "opportunity" / f"{clean_symbol}.json"
    )
    save_opportunity_calibration(output, record)

    print("=" * 72)
    print("OPPORTUNITY CALIBRATION BUILT")
    print("=" * 72)
    print(f"SYMBOL\t{clean_symbol}")
    print(f"SAMPLES\t{len(mfe_samples)}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print(f"FROZEN_TIMELINE_LOAD_SECONDS\t{load_seconds:.3f}")
    print(f"FORWARD_BARS\t{args.forward_bars}")
    print(f"QUANTILES\t{q1},{q2},{q3}")
    print(f"NONE_MAX_ATR\t{none_max:.6g}")
    print(f"COMPRESSED_MAX_ATR\t{compressed_max:.6g}")
    print(f"MODERATE_MAX_ATR\t{moderate_max:.6g}")
    print(f"MFE_ATR_P10\t{float(series.quantile(0.10)):.6g}")
    print(f"MFE_ATR_P90\t{float(series.quantile(0.90)):.6g}")
    print(f"OUTPUT\t{output}")
    print(f"SOURCE_IDENTITY\t{identity}")
    print("OPPORTUNITY_CALIBRATION_OK")


if __name__ == "__main__":
    main()
