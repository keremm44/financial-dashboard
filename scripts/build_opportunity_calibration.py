from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.calibration import (
    OPPORTUNITY_CALIBRATION_SCHEMA_VERSION,
    OpportunityCalibrationRecord,
    save_opportunity_calibration,
)
from financial_dashboard.decision.engine import assess_horizon_decision
from financial_dashboard.decision.history_replay import HistoricalDecisionInputReplayRunner
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.opportunity import OpportunityCalibration
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection
from financial_dashboard.decision.timeline_cache import (
    DecisionTimelineCacheMiss,
    load_frozen_decision_timeline,
)
from financial_dashboard.structure_location_replay import CausalBarClock

DECISION_TIMEFRAME = "1h"


@dataclass(frozen=True, slots=True)
class OpportunityOutcomeSample:
    as_of: pd.Timestamp
    horizon: DecisionHorizon
    room_atr: float
    future_mfe_atr: float
    future_mae_atr: float
    target_reached: bool


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

    warmup_start: pd.Timestamp | None = None
    for value in decision_frame["timestamp"]:
        timestamp = pd.Timestamp(value)
        if pd.Timestamp(clock.available_at(timestamp, DECISION_TIMEFRAME)) >= common_cutoff:
            warmup_start = timestamp
            break
    if warmup_start is None:
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


def _forward_excursions_atr(
    *,
    direction: StructuralDirection,
    entry_price: float,
    frame: pd.DataFrame,
    positions: pd.Index,
    as_of: pd.Timestamp,
    forward_bars: int,
    reference_atr: float,
) -> tuple[float, float] | None:
    located = positions.get_indexer([_normalize_timestamp(as_of)])
    position = int(located[0]) if len(located) else -1
    if position < 0 or not reference_atr > 0:
        return None
    window = frame.iloc[position + 1 : position + 1 + forward_bars]
    if len(window) < forward_bars:
        return None
    if direction is StructuralDirection.LONG:
        mfe = (float(window["high"].max()) - entry_price) / reference_atr
        mae = (entry_price - float(window["low"].min())) / reference_atr
    else:
        mfe = (entry_price - float(window["low"].min())) / reference_atr
        mae = (float(window["high"].max()) - entry_price) / reference_atr
    return max(0.0, mfe), max(0.0, mae)


def _bucket(room_atr: float, calibration: OpportunityCalibration) -> str:
    if room_atr <= calibration.none_max_atr:
        return "NONE"
    if room_atr <= calibration.compressed_max_atr:
        return "COMPRESSED"
    if room_atr <= calibration.moderate_max_atr:
        return "MODERATE"
    return "AMPLE"


def _validation_summary(
    samples: list[OpportunityOutcomeSample],
    calibration: OpportunityCalibration,
) -> dict[str, dict[str, float | int | None]]:
    grouped: dict[str, list[OpportunityOutcomeSample]] = {
        "NONE": [],
        "COMPRESSED": [],
        "MODERATE": [],
        "AMPLE": [],
    }
    for sample in samples:
        grouped[_bucket(sample.room_atr, calibration)].append(sample)

    result: dict[str, dict[str, float | int | None]] = {}
    for name, rows in grouped.items():
        if not rows:
            result[name] = {
                "count": 0,
                "target_hit_rate": None,
                "median_mfe_atr": None,
                "median_mae_atr": None,
            }
            continue
        result[name] = {
            "count": len(rows),
            "target_hit_rate": sum(row.target_reached for row in rows) / len(rows),
            "median_mfe_atr": float(pd.Series([row.future_mfe_atr for row in rows]).median()),
            "median_mae_atr": float(pd.Series([row.future_mae_atr for row in rows]).median()),
        }
    return result


def _calibrate_from_samples(
    samples: list[OpportunityOutcomeSample],
    *,
    quantiles: tuple[float, float, float],
    train_fraction: float,
    min_samples: int,
) -> tuple[OpportunityCalibration, list[OpportunityOutcomeSample], list[OpportunityOutcomeSample]]:
    if len(samples) < min_samples:
        raise SystemExit(
            f"only {len(samples)} calibration samples available; --min-samples requires {min_samples}"
        )
    ordered = sorted(samples, key=lambda item: (item.as_of, item.horizon.value))
    split = max(1, min(len(ordered) - 1, int(len(ordered) * train_fraction)))
    train = ordered[:split]
    validation = ordered[split:]
    room = pd.Series([sample.room_atr for sample in train], dtype=float)
    q1, q2, q3 = quantiles
    none_max = float(room.quantile(q1))
    compressed_max = float(room.quantile(q2))
    moderate_max = float(room.quantile(q3))
    try:
        calibration = OpportunityCalibration(none_max, compressed_max, moderate_max)
    except ValueError as error:
        raise SystemExit(
            "degenerate room_atr distribution produced invalid boundaries "
            f"({none_max=:.6g}, {compressed_max=:.6g}, {moderate_max=:.6g}): {error}"
        ) from error
    return calibration, train, validation


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build per-symbol OpportunityCalibration from an exact frozen DecisionInput timeline. "
            "This command never replays market domains."
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
    parser.add_argument("--train-fraction", type=float, default=0.70)
    parser.add_argument("--min-samples", type=int, default=50)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    if args.forward_bars < 1:
        raise SystemExit("--forward-bars must be >= 1")
    if not 0.5 <= args.train_fraction < 1.0:
        raise SystemExit("--train-fraction must be >= 0.5 and < 1.0")
    quantiles = _parse_quantiles(args.quantiles)

    store = ParquetOHLCVStore(args.cache_root)
    clean_symbol = normalize_symbol(args.symbol)
    effective_start = _causal_warmup_start(store, symbol=clean_symbol, requested_start=args.start)
    config = HistoricalDecisionInputConfig(
        pattern_profile=args.pattern_profile,
        max_bars=args.max_bars,
        start_at=effective_start,
        end_at=args.end,
    )

    runner = HistoricalDecisionInputReplayRunner(store)
    identity = runner._cache_identity(symbol=clean_symbol, config=config)
    started = perf_counter()
    try:
        frozen = load_frozen_decision_timeline(store, clean_symbol, config=config)
    except DecisionTimelineCacheMiss as error:
        raise SystemExit(
            "exact frozen DecisionInput timeline is missing; domains were NOT replayed. "
            "Build it explicitly with "
            f"`python scripts/build_decision_timeline_cache.py {args.cache_root} {clean_symbol}`"
        ) from error
    load_seconds = perf_counter() - started
    snapshots = frozen.replay.snapshots

    decision_frame = store.load(clean_symbol, DECISION_TIMEFRAME)
    normalized_frame = decision_frame.copy()
    normalized_frame["timestamp"] = [
        _normalize_timestamp(value) for value in normalized_frame["timestamp"]
    ]
    positions = pd.Index(normalized_frame["timestamp"])

    samples: list[OpportunityOutcomeSample] = []
    for snapshot in snapshots:
        targeting = snapshot.targeting
        if targeting is None:
            continue
        reference_atr = float(targeting.reference_atr)
        for horizon in (DecisionHorizon.LONG_TERM, DecisionHorizon.SHORT_TERM):
            assessment = assess_horizon_decision(snapshot, horizon)
            direction = assessment.structural.direction
            room_atr = assessment.opportunity.room_atr
            if direction not in {StructuralDirection.LONG, StructuralDirection.SHORT}:
                continue
            if room_atr is None or room_atr < 0:
                continue
            excursions = _forward_excursions_atr(
                direction=direction,
                entry_price=float(snapshot.current_price),
                frame=normalized_frame,
                positions=positions,
                as_of=pd.Timestamp(snapshot.as_of),
                forward_bars=args.forward_bars,
                reference_atr=reference_atr,
            )
            if excursions is None:
                continue
            mfe, mae = excursions
            samples.append(
                OpportunityOutcomeSample(
                    as_of=pd.Timestamp(snapshot.as_of),
                    horizon=horizon,
                    room_atr=float(room_atr),
                    future_mfe_atr=float(mfe),
                    future_mae_atr=float(mae),
                    target_reached=bool(mfe >= float(room_atr)),
                )
            )

    calibration, train, validation = _calibrate_from_samples(
        samples,
        quantiles=quantiles,
        train_fraction=args.train_fraction,
        min_samples=args.min_samples,
    )
    validation_by_bucket = _validation_summary(validation, calibration)
    train_end = max((row.as_of for row in train), default=None)
    validation_start = min((row.as_of for row in validation), default=None)

    record = OpportunityCalibrationRecord(
        calibration=calibration,
        symbol=clean_symbol,
        sample_size=len(samples),
        version=OPPORTUNITY_CALIBRATION_SCHEMA_VERSION,
        meta={
            "method": "paired_room_atr_train_quantiles_v2",
            "forward_bars": args.forward_bars,
            "quantiles": list(quantiles),
            "train_fraction": args.train_fraction,
            "train_samples": len(train),
            "validation_samples": len(validation),
            "train_end": None if train_end is None else train_end.isoformat(),
            "validation_start": None if validation_start is None else validation_start.isoformat(),
            "validation_by_bucket": validation_by_bucket,
            "reference_timeframe": DECISION_TIMEFRAME,
            "source_identity": str(identity),
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
    print("METHOD\tpaired_room_atr_train_quantiles_v2")
    print(f"SAMPLES\t{len(samples)}")
    print(f"TRAIN_SAMPLES\t{len(train)}")
    print(f"VALIDATION_SAMPLES\t{len(validation)}")
    print(f"TRAIN_END\t{train_end}")
    print(f"VALIDATION_START\t{validation_start}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print(f"FROZEN_CACHE_STATUS\t{frozen.cache_status}")
    print(f"FROZEN_TIMELINE_LOAD_SECONDS\t{load_seconds:.3f}")
    print("DOMAIN_REPLAY_SECONDS\t0.000")
    print(f"FORWARD_BARS\t{args.forward_bars}")
    print(f"NONE_MAX_ATR\t{calibration.none_max_atr:.6g}")
    print(f"COMPRESSED_MAX_ATR\t{calibration.compressed_max_atr:.6g}")
    print(f"MODERATE_MAX_ATR\t{calibration.moderate_max_atr:.6g}")
    for bucket_name, stats in validation_by_bucket.items():
        print(
            f"VALIDATION_{bucket_name}\tcount={stats['count']}\t"
            f"target_hit_rate={stats['target_hit_rate']}\t"
            f"median_mfe_atr={stats['median_mfe_atr']}\t"
            f"median_mae_atr={stats['median_mae_atr']}"
        )
    print(f"OUTPUT\t{output}")
    print(f"SOURCE_IDENTITY\t{identity}")
    print("OPPORTUNITY_CALIBRATION_OK")


if __name__ == "__main__":
    main()
