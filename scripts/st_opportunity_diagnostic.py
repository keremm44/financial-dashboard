from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from json import dumps
from pathlib import Path
from statistics import median
from typing import Any

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.calibration import load_opportunity_calibration
from financial_dashboard.decision.engine import DecisionEngineConfig, prepare_horizon_assessment
from financial_dashboard.decision.execution_detect import detect_30m_execution_events
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.opportunity import OpportunityState
from financial_dashboard.decision.structural import DecisionHorizon
from financial_dashboard.decision.timeline_cache import (
    DecisionTimelineCacheMiss,
    load_frozen_decision_timeline,
)
from financial_dashboard.structure_location_replay import CausalBarClock


@dataclass(frozen=True, slots=True)
class OpportunityRow:
    as_of: Any
    state: str
    room_atr: float | None
    target_identity: str | None
    target_quality: str | None
    reasons: tuple[str, ...]
    has_fresh_entry_event: bool


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

    for value in decision_frame["timestamp"]:
        timestamp = pd.Timestamp(value)
        if pd.Timestamp(clock.available_at(timestamp, decision_timeframe)) >= common_cutoff:
            warmup_start = timestamp
            break
    else:
        raise SystemExit(
            "No decision bar exists after all required timeframe histories become causally available"
        )

    if requested_start is None:
        return warmup_start
    return max(warmup_start, _align_requested_start(requested_start, warmup_start))


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _room_summary(rows: list[OpportunityRow]) -> dict[str, dict[str, float | int | None]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    counts = Counter(row.state for row in rows)
    for row in rows:
        if row.room_atr is not None:
            grouped[row.state].append(float(row.room_atr))

    result: dict[str, dict[str, float | int | None]] = {}
    for state in OpportunityState:
        values = grouped.get(state.value, [])
        result[state.value] = {
            "count": int(counts.get(state.value, 0)),
            "with_room": len(values),
            "min_room_atr": min(values) if values else None,
            "median_room_atr": median(values) if values else None,
            "max_room_atr": max(values) if values else None,
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnostic-only ST Opportunity audit over an exact frozen DecisionInput timeline. "
            "It does not alter targeting, calibration, eligibility, or trading policy."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
    parser.add_argument("--auto-calibration", action="store_true")
    parser.add_argument("--opportunity-calibration", type=Path, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

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
    config = DecisionEngineConfig(opportunity_calibration=record.calibration)

    rows: list[OpportunityRow] = []
    for snapshot in snapshots:
        prepared = prepare_horizon_assessment(
            snapshot,
            DecisionHorizon.SHORT_TERM,
            config=config,
        )
        opportunity = prepared.opportunity
        rows.append(
            OpportunityRow(
                as_of=snapshot.as_of,
                state=opportunity.state.value,
                room_atr=opportunity.room_atr,
                target_identity=opportunity.target_identity,
                target_quality=opportunity.target_quality,
                reasons=tuple(opportunity.reasons),
                has_fresh_entry_event=snapshot.as_of in entry_events,
            )
        )

    state_counts = Counter(row.state for row in rows)
    reason_counts = Counter(reason for row in rows for reason in row.reasons)
    unknown_reason_counts = Counter(
        reason
        for row in rows
        if row.state == OpportunityState.UNKNOWN.value
        for reason in row.reasons
    )
    fresh_rows = [row for row in rows if row.has_fresh_entry_event]
    fresh_state_counts = Counter(row.state for row in fresh_rows)
    fresh_reason_counts = Counter(reason for row in fresh_rows for reason in row.reasons)
    fresh_unknown_reasons = Counter(
        reason
        for row in fresh_rows
        if row.state == OpportunityState.UNKNOWN.value
        for reason in row.reasons
    )

    no_target_unknown = sum(
        1
        for row in rows
        if row.state == OpportunityState.UNKNOWN.value
        and "NO_DIRECTIONAL_TARGET_OBSERVED_NOT_CLEAR_PATH" in row.reasons
    )
    targeting_unavailable = sum(
        1
        for row in rows
        if row.state == OpportunityState.UNKNOWN.value and "TARGETING_UNAVAILABLE" in row.reasons
    )
    calibration_required = sum(
        1
        for row in rows
        if row.state == OpportunityState.UNKNOWN.value
        and "OPPORTUNITY_CALIBRATION_REQUIRED" in row.reasons
    )

    print("=" * 76)
    print("ST OPPORTUNITY DIAGNOSTIC")
    print("=" * 76)
    print(f"SYMBOL\t{clean_symbol}")
    print(f"CAUSAL_WARMUP_START\t{effective_start}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print(f"FROZEN_CACHE_STATUS\t{frozen.cache_status}")
    print(f"OPPORTUNITY_CALIBRATION\t{calibration_path}")
    print(f"ENTRY_EXECUTION_EVENTS\t{len(entry_events)}")
    print(f"EXIT_EXECUTION_EVENTS\t{len(exit_events)}")
    print("TRADING_POLICY_MUTATION\tNONE")
    print("TARGETING_MUTATION\tNONE")
    print("CALIBRATION_MUTATION\tNONE")
    print()

    print("ST OPPORTUNITY STATES")
    print("---------------------")
    for state in OpportunityState:
        print(f"{state.value:12} {state_counts.get(state.value, 0):5}")
    print()

    print("UNKNOWN CAUSE SPLIT")
    print("-------------------")
    print(f"NO_DIRECTIONAL_TARGET\t{no_target_unknown}")
    print(f"TARGETING_UNAVAILABLE\t{targeting_unavailable}")
    print(f"CALIBRATION_REQUIRED\t{calibration_required}")
    for reason, count in unknown_reason_counts.most_common():
        print(f"UNKNOWN_REASON:{reason}\t{count}")
    print()

    print("ROOM DISTRIBUTION BY STATE")
    print("--------------------------")
    room_summary = _room_summary(rows)
    for state in OpportunityState:
        item = room_summary[state.value]
        print(
            f"{state.value:12} count={item['count']} with_room={item['with_room']} "
            f"min={item['min_room_atr']} median={item['median_room_atr']} max={item['max_room_atr']}"
        )
    print()

    print("FRESH ENTRY EVENT OPPORTUNITY")
    print("-----------------------------")
    for state in OpportunityState:
        print(f"{state.value:12} {fresh_state_counts.get(state.value, 0):5}")
    for reason, count in fresh_unknown_reasons.most_common():
        print(f"FRESH_UNKNOWN_REASON:{reason}\t{count}")
    print()

    print("FRESH EVENT DETAIL")
    print("------------------")
    for row in fresh_rows:
        room = "n/a" if row.room_atr is None else f"{row.room_atr:.4f}ATR"
        reasons = ",".join(row.reasons) if row.reasons else "NONE"
        print(
            f"{row.as_of} | {row.state} | room={room} | "
            f"target={row.target_identity or 'NONE'} | quality={row.target_quality or 'NONE'} | "
            f"reasons={reasons}"
        )

    if args.json_out is not None:
        report = {
            "symbol": clean_symbol,
            "causal_warmup_start": effective_start,
            "snapshots": len(snapshots),
            "frozen_cache_status": frozen.cache_status,
            "calibration_path": str(calibration_path),
            "entry_execution_events": len(entry_events),
            "exit_execution_events": len(exit_events),
            "state_counts": dict(state_counts),
            "reason_counts": dict(reason_counts),
            "unknown_reason_counts": dict(unknown_reason_counts),
            "room_summary": room_summary,
            "fresh_state_counts": dict(fresh_state_counts),
            "fresh_reason_counts": dict(fresh_reason_counts),
            "fresh_rows": [asdict(row) for row in fresh_rows],
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            dumps(report, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )
        print(f"JSON_REPORT\t{args.json_out}")

    print("ST_OPPORTUNITY_DIAGNOSTIC_OK")


if __name__ == "__main__":
    main()
