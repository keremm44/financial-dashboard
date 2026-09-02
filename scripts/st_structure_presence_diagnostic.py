from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from json import dumps
from pathlib import Path
from typing import Any, Sequence

from entry_reason_profile import _calibration, _causal_warmup_start
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.engine import DecisionEngineConfig
from financial_dashboard.decision.execution_detect import detect_30m_execution_events
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.scenario import ScenarioPresence, prepare_entry_scenario
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection
from financial_dashboard.decision.timeline_cache import (
    DecisionTimelineCacheMiss,
    load_frozen_decision_timeline,
)


@dataclass(frozen=True, slots=True)
class EventStructureRow:
    as_of: Any
    event_side: str
    scenario_presence: str
    scenario_reason: str
    st_direction: str
    st_thesis_state: str
    st_native_state: str | None
    st_transition_target: str | None
    st_authority_as_of: Any | None
    st_reason: str
    lt_direction: str
    lt_thesis_state: str
    horizon_relation: str
    opportunity_state: str
    opportunity_room_atr: float | None
    bars_since_st_direction_change: int
    hindsight_bars_until_long: int | None
    forward_3bar_return_pct: float | None
    forward_3bar_mfe_pct: float | None
    forward_3bar_mae_pct: float | None
    forward_6bar_return_pct: float | None
    forward_6bar_mfe_pct: float | None
    forward_6bar_mae_pct: float | None


def _value(value: Any) -> str:
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)


def _forward_quality(
    snapshots: Sequence[Any],
    index: int,
    bars: int,
) -> tuple[float | None, float | None, float | None]:
    entry = float(snapshots[index].current_price)
    end = min(len(snapshots) - 1, index + bars)
    if entry <= 0.0 or end <= index:
        return None, None, None
    prices = [float(snapshots[pos].current_price) for pos in range(index + 1, end + 1)]
    returns = [(price / entry - 1.0) * 100.0 for price in prices]
    return returns[-1], max(returns), min(returns)


def _bars_since_direction_change(directions: Sequence[StructuralDirection], index: int) -> int:
    current = directions[index]
    pos = index - 1
    while pos >= 0 and directions[pos] is current:
        pos -= 1
    return index - pos - 1


def _hindsight_bars_until_long(
    directions: Sequence[StructuralDirection],
    index: int,
) -> int | None:
    for pos in range(index + 1, len(directions)):
        if directions[pos] is StructuralDirection.LONG:
            return pos - index
    return None


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnostic-only ST Structure/scenario-presence audit at fresh entry events. "
            "Production Structure, scenario and trading policy are never modified."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--opportunity-calibration", type=Path, default=None)
    parser.add_argument("--auto-calibration", action="store_true")
    parser.add_argument("--opportunity-none-max-atr", type=float, default=None)
    parser.add_argument("--opportunity-compressed-max-atr", type=float, default=None)
    parser.add_argument("--opportunity-moderate-max-atr", type=float, default=None)
    args = parser.parse_args()

    store = ParquetOHLCVStore(args.cache_root)
    clean_symbol = normalize_symbol(args.symbol)
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
    calibration, calibration_source = _calibration(
        args,
        cache_root=args.cache_root,
        symbol=clean_symbol,
    )
    config = DecisionEngineConfig(opportunity_calibration=calibration)

    try:
        frozen = load_frozen_decision_timeline(store, clean_symbol, config=history_config)
    except DecisionTimelineCacheMiss as exc:
        raise SystemExit(
            "FROZEN_DECISION_TIMELINE_CACHE_MISS: build the exact frozen timeline first"
        ) from exc

    snapshots = tuple(frozen.replay.snapshots)
    if not snapshots:
        raise SystemExit("Frozen historical DecisionInput timeline contains no causal snapshots")

    entry_events, exit_events = detect_30m_execution_events(snapshots)

    prepared_rows = [
        prepare_entry_scenario(snapshot, DecisionHorizon.SHORT_TERM, config=config)
        for snapshot in snapshots
    ]
    directions = [item.assessment.structural.direction for item in prepared_rows]

    rows: list[EventStructureRow] = []
    for index, (snapshot, prepared) in enumerate(zip(snapshots, prepared_rows)):
        event = entry_events.get(snapshot.as_of)
        if event is None:
            continue

        scenario = prepared.scenario
        assessment = prepared.assessment
        st = assessment.structural
        structural_snapshot = assessment.structural_snapshot
        lt = structural_snapshot.long_term
        r3, mfe3, mae3 = _forward_quality(snapshots, index, 3)
        r6, mfe6, mae6 = _forward_quality(snapshots, index, 6)
        rows.append(
            EventStructureRow(
                as_of=snapshot.as_of,
                event_side=_value(event.side),
                scenario_presence=_value(scenario.presence),
                scenario_reason="|".join(scenario.reasons) if scenario.reasons else "NONE",
                st_direction=_value(st.direction),
                st_thesis_state=_value(st.thesis_state),
                st_native_state=st.native_state,
                st_transition_target=None if st.transition_target is None else _value(st.transition_target),
                st_authority_as_of=st.authority_as_of,
                st_reason="|".join(st.reasons) if st.reasons else "NONE",
                lt_direction=_value(lt.direction),
                lt_thesis_state=_value(lt.thesis_state),
                horizon_relation=_value(structural_snapshot.relation),
                opportunity_state=_value(assessment.opportunity.state),
                opportunity_room_atr=assessment.opportunity.room_atr,
                bars_since_st_direction_change=_bars_since_direction_change(directions, index),
                hindsight_bars_until_long=(
                    None
                    if st.direction is StructuralDirection.LONG
                    else _hindsight_bars_until_long(directions, index)
                ),
                forward_3bar_return_pct=r3,
                forward_3bar_mfe_pct=mfe3,
                forward_3bar_mae_pct=mae3,
                forward_6bar_return_pct=r6,
                forward_6bar_mfe_pct=mfe6,
                forward_6bar_mae_pct=mae6,
            )
        )

    presence_counts = Counter(row.scenario_presence for row in rows)
    direction_counts = Counter(row.st_direction for row in rows)
    non_presence_reasons = Counter(
        row.scenario_reason
        for row in rows
        if row.scenario_presence != ScenarioPresence.PRESENT.value
    )

    rejected_short = [
        row
        for row in rows
        if row.scenario_presence == ScenarioPresence.ABSENT.value
        and "LONG_ENTRY_REQUIRES_LONG_STRUCTURE" in row.scenario_reason
    ]

    print("=" * 84)
    print("ST STRUCTURE / SCENARIO-PRESENCE FRESH-EVENT DIAGNOSTIC")
    print("=" * 84)
    print(f"SYMBOL\t{clean_symbol}")
    print(f"CAUSAL_WARMUP_START\t{effective_start}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print(f"FROZEN_CACHE_STATUS\t{frozen.cache_status}")
    print(f"OPPORTUNITY_CALIBRATION\t{calibration_source}")
    print(f"ENTRY_EXECUTION_EVENTS\t{len(entry_events)}")
    print(f"EXIT_EXECUTION_EVENTS\t{len(exit_events)}")
    print("TRADING_POLICY_MUTATION\tNONE")
    print("STRUCTURE_MUTATION\tNONE")
    print("SCENARIO_MUTATION\tNONE")
    print("HINDSIGHT_FIELDS\tBARS_UNTIL_LONG,FORWARD_3BAR,FORWARD_6BAR")
    print()

    print("FRESH EVENT SCENARIO PRESENCE")
    print("-----------------------------")
    for key in ("PRESENT", "ABSENT", "UNKNOWN"):
        print(f"{key:10} {presence_counts.get(key, 0):5}")
    print()

    print("FRESH EVENT ST DIRECTION")
    print("------------------------")
    for key in ("LONG", "SHORT", "UNRESOLVED"):
        print(f"{key:10} {direction_counts.get(key, 0):5}")
    print()

    print("NON-PRESENCE REASONS")
    print("--------------------")
    for reason, count in non_presence_reasons.most_common():
        print(f"{reason}\t{count}")
    print()

    print("LONG-ENTRY REJECTED BY ST SHORT STRUCTURE")
    print("-----------------------------------------")
    print(f"COUNT\t{len(rejected_short)}")
    for row in rejected_short:
        room = "n/a" if row.opportunity_room_atr is None else f"{row.opportunity_room_atr:.3f}ATR"
        until_long = "n/a" if row.hindsight_bars_until_long is None else str(row.hindsight_bars_until_long)
        f3 = "n/a" if row.forward_3bar_return_pct is None else f"{row.forward_3bar_return_pct:.2f}%"
        mfe3 = "n/a" if row.forward_3bar_mfe_pct is None else f"{row.forward_3bar_mfe_pct:.2f}%"
        mae3 = "n/a" if row.forward_3bar_mae_pct is None else f"{row.forward_3bar_mae_pct:.2f}%"
        f6 = "n/a" if row.forward_6bar_return_pct is None else f"{row.forward_6bar_return_pct:.2f}%"
        mfe6 = "n/a" if row.forward_6bar_mfe_pct is None else f"{row.forward_6bar_mfe_pct:.2f}%"
        mae6 = "n/a" if row.forward_6bar_mae_pct is None else f"{row.forward_6bar_mae_pct:.2f}%"
        print(
            f"{row.as_of} | event={row.event_side} | ST={row.st_direction}/{row.st_thesis_state}/"
            f"{row.st_native_state or 'NONE'} | relation={row.horizon_relation} | "
            f"opp={row.opportunity_state}:{room} | short_age={row.bars_since_st_direction_change} bars | "
            f"hindsight_until_LONG={until_long} | 3bar ret={f3} MFE={mfe3} MAE={mae3} | "
            f"6bar ret={f6} MFE={mfe6} MAE={mae6}"
        )
    print()

    print("ALL FRESH EVENT DETAIL")
    print("----------------------")
    for row in rows:
        print(
            f"{row.as_of} | event={row.event_side} | presence={row.scenario_presence} | "
            f"ST={row.st_direction}/{row.st_thesis_state}/{row.st_native_state or 'NONE'} | "
            f"LT={row.lt_direction}/{row.lt_thesis_state} | relation={row.horizon_relation} | "
            f"reason={row.scenario_reason}"
        )

    if args.json_out is not None:
        report = {
            "symbol": clean_symbol,
            "causal_warmup_start": effective_start,
            "snapshots": len(snapshots),
            "frozen_cache_status": frozen.cache_status,
            "opportunity_calibration": calibration_source,
            "entry_execution_events": len(entry_events),
            "exit_execution_events": len(exit_events),
            "presence_counts": dict(presence_counts),
            "direction_counts": dict(direction_counts),
            "non_presence_reasons": dict(non_presence_reasons),
            "rejected_short_count": len(rejected_short),
            "rows": [asdict(row) for row in rows],
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            dumps(report, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )
        print(f"JSON_REPORT\t{args.json_out}")

    print("ST_STRUCTURE_PRESENCE_DIAGNOSTIC_OK")


if __name__ == "__main__":
    main()
