from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from json import dumps
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.calibration import load_opportunity_calibration
from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.engine import DecisionEngineConfig
from financial_dashboard.decision.execution import ExecutionTriggerEvent
from financial_dashboard.decision.execution_detect import detect_30m_execution_events
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.timeline_cache import (
    DecisionTimelineCacheMiss,
    load_frozen_decision_timeline,
)
from financial_dashboard.structure_location_replay import CausalBarClock


@dataclass(frozen=True, slots=True)
class DiagnosticEntry:
    as_of: Any
    price: float
    selected_horizon: str | None
    scenario_kind: str | None
    forward_bars: int
    forward_return_pct: float | None
    mfe_pct: float | None
    mae_pct: float | None


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


def _value(value: Any) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)


def _forward_quality(
    snapshots: Sequence[Any],
    index: int,
    bars: int,
) -> tuple[float | None, float | None, float | None, int]:
    entry = float(snapshots[index].current_price)
    end = min(len(snapshots) - 1, index + bars)
    if end <= index or entry <= 0.0:
        return None, None, None, 0
    prices = [float(snapshots[pos].current_price) for pos in range(index + 1, end + 1)]
    returns = [(price / entry - 1.0) * 100.0 for price in prices]
    return returns[-1], max(returns), min(returns), len(prices)


def diagnostic_forced_flat_entries(
    snapshots: Sequence[Any],
    *,
    config: DecisionEngineConfig,
    entry_execution_events: Mapping[Any, ExecutionTriggerEvent],
    force_flat_after_bars: int,
) -> tuple[DiagnosticEntry, ...]:
    """Measure raw entry flow while deliberately bypassing the canonical exit lifecycle.

    This is a diagnostic counterfactual only. After a BUY, the next N decision bars are
    treated as occupied and the probe then returns to a synthetic FLAT state. It does
    not emit SELL, mutate TradeLifecycleState, reuse sticky execution evidence, or
    alter production entry/exit policy.
    """

    if force_flat_after_bars < 1:
        raise ValueError("force_flat_after_bars must be >= 1")

    locked_until_index = -1
    entries: list[DiagnosticEntry] = []
    for index, snapshot in enumerate(snapshots):
        if index <= locked_until_index:
            continue
        event = entry_execution_events.get(snapshot.as_of)
        decision = snapshot.entry_decision(config=config, execution_event=event)
        if decision.action is not DecisionAction.BUY:
            continue

        forward_return, mfe, mae, observed_bars = _forward_quality(
            snapshots,
            index,
            force_flat_after_bars,
        )
        scenario = getattr(getattr(decision, "arbitration", None), "selected_scenario", None)
        entries.append(
            DiagnosticEntry(
                as_of=snapshot.as_of,
                price=float(snapshot.current_price),
                selected_horizon=_value(getattr(decision, "selected_horizon", None)),
                scenario_kind=_value(getattr(scenario, "kind", None)),
                forward_bars=observed_bars,
                forward_return_pct=forward_return,
                mfe_pct=mfe,
                mae_pct=mae,
            )
        )
        locked_until_index = index + force_flat_after_bars

    return tuple(entries)


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnostic-only ST entry flow probe. It bypasses the canonical SELL lifecycle "
            "by returning to synthetic FLAT N decision bars after each BUY."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--force-flat-after-bars", type=int, default=3)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
    parser.add_argument("--auto-calibration", action="store_true")
    parser.add_argument("--opportunity-calibration", type=Path, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    if args.force_flat_after_bars < 1:
        raise SystemExit("--force-flat-after-bars must be >= 1")

    clean_symbol = normalize_symbol(args.symbol)
    store = ParquetOHLCVStore(args.cache_root)
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
    try:
        frozen = load_frozen_decision_timeline(store, clean_symbol, config=history_config)
    except DecisionTimelineCacheMiss as exc:
        raise SystemExit(
            "FROZEN_DECISION_TIMELINE_CACHE_MISS: build the exact frozen timeline first"
        ) from exc

    snapshots = tuple(frozen.replay.snapshots)
    if not snapshots:
        raise SystemExit("Frozen historical DecisionInput timeline contains no causal snapshots")

    calibration_path = args.opportunity_calibration
    if calibration_path is None and args.auto_calibration:
        calibration_path = args.cache_root / "calibration" / "opportunity" / f"{clean_symbol}.json"
    if calibration_path is None:
        raise SystemExit("Use --auto-calibration or --opportunity-calibration")
    if not calibration_path.exists():
        raise SystemExit(f"opportunity calibration file is missing: {calibration_path}")
    record = load_opportunity_calibration(calibration_path)
    if normalize_symbol(record.symbol) != clean_symbol:
        raise SystemExit("opportunity calibration symbol mismatch")

    entry_events, exit_events = detect_30m_execution_events(snapshots)
    entries = diagnostic_forced_flat_entries(
        snapshots,
        config=DecisionEngineConfig(opportunity_calibration=record.calibration),
        entry_execution_events=entry_events,
        force_flat_after_bars=args.force_flat_after_bars,
    )

    print("=" * 76)
    print("ST ENTRY FORCED-FLAT DIAGNOSTIC")
    print("=" * 76)
    print(f"SYMBOL\t{clean_symbol}")
    print(f"CAUSAL_WARMUP_START\t{effective_start}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print(f"FROZEN_CACHE_STATUS\t{frozen.cache_status}")
    print(f"ENTRY_EXECUTION_EVENTS\t{len(entry_events)}")
    print(f"EXIT_EXECUTION_EVENTS\t{len(exit_events)}")
    print(f"FORCE_FLAT_AFTER_BARS\t{args.force_flat_after_bars}")
    print(f"DIAGNOSTIC_BUYS\t{len(entries)}")
    print("PRODUCTION_SELL_POLICY_MUTATION\tNONE")
    print("CANONICAL_LIFECYCLE_MUTATION\tNONE")
    print("REENTRY_NOVELTY_POLICY\tBYPASSED_DIAGNOSTICALLY")
    print()
    print("DIAGNOSTIC BUY WINDOWS")
    print("----------------------")
    if not entries:
        print("None.")
    for item in entries:
        ret = "n/a" if item.forward_return_pct is None else f"{item.forward_return_pct:.2f}%"
        mfe = "n/a" if item.mfe_pct is None else f"{item.mfe_pct:.2f}%"
        mae = "n/a" if item.mae_pct is None else f"{item.mae_pct:.2f}%"
        print(
            f"{item.as_of} BUY {item.price:.4f} | {item.selected_horizon or 'NONE'} "
            f"{item.scenario_kind or 'NONE'} | {item.forward_bars} bars: "
            f"ret={ret} MFE={mfe} MAE={mae}"
        )

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            dumps([asdict(item) for item in entries], ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )
        print(f"JSON_REPORT\t{args.json_out}")

    print("ST_ENTRY_FORCED_FLAT_DIAGNOSTIC_OK")


if __name__ == "__main__":
    main()
