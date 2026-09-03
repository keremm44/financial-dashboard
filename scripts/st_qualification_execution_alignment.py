from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

from buy_sell_backtest import _calibration, _causal_warmup_start
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.arbiter import assess_entry_arbitration
from financial_dashboard.decision.engine import DecisionEngineConfig
from financial_dashboard.decision.entry import assess_entry_decision
from financial_dashboard.decision.execution_detect import detect_30m_execution_events
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.lifecycle import PositionState
from financial_dashboard.decision.lifecycle_replay import replay_canonical_trade_lifecycle
from financial_dashboard.decision.scenario import ScenarioPresence, ScenarioStage, prepare_entry_scenario
from financial_dashboard.decision.structural import DecisionHorizon
from financial_dashboard.decision.timeline_cache import DecisionTimelineCacheMiss, load_frozen_decision_timeline


def _value(value: Any) -> str:
    return getattr(value, "value", str(value))


def _distance_summary(values: list[int]) -> dict[str, int | float | None]:
    if not values:
        return {"count": 0, "min": None, "median": None, "max": None}
    return {
        "count": len(values),
        "min": min(values),
        "median": float(median(values)),
        "max": max(values),
    }


def _print_counter(title: str, counter: Counter[str]) -> None:
    print()
    print(title)
    print("-" * len(title))
    if not counter:
        print("None.")
        return
    width = max(len(key) for key in counter)
    for key, count in counter.most_common():
        print(f"{key:<{width}}  {count:6d}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnostic-only ST qualification/execution/arbitration/lifecycle timing audit. "
            "It never changes Structure, Timing, Opportunity, arbitration, or lifecycle policy."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
    parser.add_argument("--window-bars", type=int, default=6)
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
    engine_config = DecisionEngineConfig(opportunity_calibration=calibration)

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
    lifecycle = replay_canonical_trade_lifecycle(
        snapshots,
        config=engine_config,
        entry_execution_events=entry_events,
        exit_execution_events=exit_events,
    )
    lifecycle_by_asof = {row.snapshot.as_of: row for row in lifecycle.rows}
    event_indices = sorted(i for i, snapshot in enumerate(snapshots) if snapshot.as_of in entry_events)
    event_index_set = set(event_indices)

    scenario_rows: list[dict[str, Any]] = []
    qualified_indices: list[int] = []
    counters: Counter[str] = Counter()
    event_counters: Counter[str] = Counter()
    lifecycle_counters: Counter[str] = Counter()

    for i, snapshot in enumerate(snapshots):
        prepared = prepare_entry_scenario(
            snapshot,
            DecisionHorizon.SHORT_TERM,
            config=engine_config,
        )
        scenario = prepared.scenario
        event = entry_events.get(snapshot.as_of)
        qualified = (
            scenario.presence is ScenarioPresence.PRESENT
            and scenario.stage is ScenarioStage.QUALIFIED
        )
        if scenario.presence is ScenarioPresence.PRESENT:
            counters["ST_PRESENT"] += 1
        if qualified:
            counters["ST_QUALIFIED"] += 1
            qualified_indices.append(i)
            if event is not None:
                counters["QUALIFIED_WITH_FRESH_EVENT"] += 1
            else:
                counters["QUALIFIED_WITHOUT_FRESH_EVENT"] += 1

        if event is not None:
            event_counters["FRESH_EVENTS"] += 1
            if scenario.presence is not ScenarioPresence.PRESENT:
                event_counters["EVENT_WHILE_ST_NOT_PRESENT"] += 1
            elif qualified:
                event_counters["EVENT_WHILE_ST_QUALIFIED"] += 1
            else:
                event_counters["EVENT_WHILE_ST_DEVELOPING"] += 1

            arbitration = assess_entry_arbitration(snapshot, config=engine_config)
            entry = assess_entry_decision(
                snapshot,
                config=engine_config,
                execution_event=event,
            )
            event_counters[f"ARBITER:{_value(arbitration.selection)}"] += 1
            event_counters[f"ENTRY_ACTION:{_value(entry.action)}"] += 1
            if entry.execution_event_consumed:
                event_counters["EVENT_CONSUMED"] += 1
                selected = "NONE" if entry.selected_horizon is None else _value(entry.selected_horizon)
                event_counters[f"EVENT_CONSUMED_BY:{selected}"] += 1
            else:
                event_counters["EVENT_NOT_CONSUMED"] += 1

            lifecycle_row = lifecycle_by_asof.get(snapshot.as_of)
            if lifecycle_row is not None:
                previous_position = lifecycle_row.previous_state.position
                lifecycle_counters[f"EVENT_POSITION_BEFORE:{_value(previous_position)}"] += 1
                if previous_position is PositionState.OPEN:
                    lifecycle_counters["EVENT_DURING_OPEN_POSITION"] += 1
                elif previous_position is PositionState.FLAT:
                    lifecycle_counters["EVENT_WHILE_FLAT"] += 1
                lifecycle_counters[f"LIFECYCLE_ACTION:{_value(lifecycle_row.action)}"] += 1

        scenario_rows.append(
            {
                "index": i,
                "as_of": str(snapshot.as_of),
                "presence": _value(scenario.presence),
                "stage": _value(scenario.stage),
                "qualified": qualified,
                "fresh_event": event is not None,
                "scenario_kind": _value(scenario.kind),
                "target_identity": scenario.active_target_identity,
                "waiting_for": list(scenario.waiting_for),
                "blockers": list(scenario.blockers),
            }
        )

    # Nearest event timing around every qualified snapshot. Positive = event occurs later,
    # negative = event already happened. Same-bar event = 0.
    nearest_signed: list[int] = []
    nearest_abs: list[int] = []
    qualified_with_event_in_window = 0
    window = max(0, int(args.window_bars))
    for qi in qualified_indices:
        if not event_indices:
            continue
        nearest = min(event_indices, key=lambda ei: (abs(ei - qi), ei))
        signed = nearest - qi
        nearest_signed.append(signed)
        nearest_abs.append(abs(signed))
        if abs(signed) <= window:
            qualified_with_event_in_window += 1

    # Contiguous QUALIFIED runs are more meaningful than counting every qualified bar.
    runs: list[dict[str, Any]] = []
    if qualified_indices:
        run_start = qualified_indices[0]
        run_end = run_start
        for qi in qualified_indices[1:]:
            if qi == run_end + 1:
                run_end = qi
                continue
            runs.append({"start": run_start, "end": run_end})
            run_start = qi
            run_end = qi
        runs.append({"start": run_start, "end": run_end})

    run_event_counts: Counter[str] = Counter()
    run_details: list[dict[str, Any]] = []
    for run_id, run in enumerate(runs, start=1):
        start_i = int(run["start"])
        end_i = int(run["end"])
        inside = [ei for ei in event_indices if start_i <= ei <= end_i]
        previous = [ei for ei in event_indices if ei < start_i]
        following = [ei for ei in event_indices if ei > end_i]
        prev_distance = None if not previous else start_i - previous[-1]
        next_distance = None if not following else following[0] - end_i
        if inside:
            run_event_counts["RUN_WITH_EVENT_INSIDE"] += 1
        else:
            run_event_counts["RUN_WITHOUT_EVENT_INSIDE"] += 1
            if prev_distance is not None and prev_distance <= window:
                run_event_counts["RUN_EVENT_JUST_BEFORE"] += 1
            if next_distance is not None and next_distance <= window:
                run_event_counts["RUN_EVENT_JUST_AFTER"] += 1
        run_details.append(
            {
                "run_id": run_id,
                "start_as_of": str(snapshots[start_i].as_of),
                "end_as_of": str(snapshots[end_i].as_of),
                "bars": end_i - start_i + 1,
                "events_inside": len(inside),
                "previous_event_distance_bars": prev_distance,
                "next_event_distance_bars": next_distance,
                "scenario_kind": scenario_rows[start_i]["scenario_kind"],
                "target_identity_start": scenario_rows[start_i]["target_identity"],
            }
        )

    # Event-centric detail, including whether qualification appears shortly after an early event.
    event_details: list[dict[str, Any]] = []
    for ei in event_indices:
        snapshot = snapshots[ei]
        current = scenario_rows[ei]
        future_qualified = [qi for qi in qualified_indices if qi > ei]
        past_qualified = [qi for qi in qualified_indices if qi < ei]
        next_q = None if not future_qualified else future_qualified[0] - ei
        prev_q = None if not past_qualified else ei - past_qualified[-1]
        arbitration = assess_entry_arbitration(snapshot, config=engine_config)
        entry = assess_entry_decision(snapshot, config=engine_config, execution_event=entry_events[snapshot.as_of])
        lifecycle_row = lifecycle_by_asof.get(snapshot.as_of)
        previous_position = None if lifecycle_row is None else _value(lifecycle_row.previous_state.position)
        event_details.append(
            {
                "as_of": str(snapshot.as_of),
                "st_presence": current["presence"],
                "st_stage": current["stage"],
                "next_qualified_distance_bars": next_q,
                "previous_qualified_distance_bars": prev_q,
                "arbiter_selection": _value(arbitration.selection),
                "entry_action": _value(entry.action),
                "selected_horizon": None if entry.selected_horizon is None else _value(entry.selected_horizon),
                "event_consumed": bool(entry.execution_event_consumed),
                "lifecycle_previous_position": previous_position,
                "lifecycle_action": None if lifecycle_row is None else _value(lifecycle_row.action),
            }
        )

    print("=" * 84)
    print("ST QUALIFICATION / EXECUTION ALIGNMENT DIAGNOSTIC")
    print("=" * 84)
    print(f"SYMBOL\t{clean_symbol}")
    print(f"CAUSAL_WARMUP_START\t{effective_start}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print(f"FROZEN_CACHE_STATUS\t{frozen.cache_status}")
    print(f"OPPORTUNITY_CALIBRATION\t{calibration_source}")
    print(f"ENTRY_EXECUTION_EVENTS\t{len(entry_events)}")
    print(f"EXIT_EXECUTION_EVENTS\t{len(exit_events)}")
    print(f"ALIGNMENT_WINDOW_BARS\t{window}")
    print("TRADING_POLICY_MUTATION\tNONE")
    print("STRUCTURE_MUTATION\tNONE")
    print("TIMING_MUTATION\tNONE")
    print("LIFECYCLE_MUTATION\tNONE")

    _print_counter("QUALIFICATION SNAPSHOT COUNTS", counters)
    _print_counter("FRESH EVENT FUNNEL", event_counters)
    _print_counter("FRESH EVENT LIFECYCLE STATE", lifecycle_counters)
    _print_counter("CONTIGUOUS QUALIFIED RUN ALIGNMENT", run_event_counts)

    print()
    print("QUALIFIED SNAPSHOT -> NEAREST FRESH EVENT")
    print("-----------------------------------------")
    print(f"QUALIFIED_SNAPSHOTS\t{len(qualified_indices)}")
    print(f"WITH_EVENT_WITHIN_{window}_BARS\t{qualified_with_event_in_window}")
    print(f"ABS_DISTANCE_SUMMARY\t{_distance_summary(nearest_abs)}")
    if nearest_signed:
        before = sum(1 for value in nearest_signed if value < 0)
        same = sum(1 for value in nearest_signed if value == 0)
        after = sum(1 for value in nearest_signed if value > 0)
        print(f"NEAREST_EVENT_ALREADY_PASSED\t{before}")
        print(f"NEAREST_EVENT_SAME_BAR\t{same}")
        print(f"NEAREST_EVENT_STILL_AHEAD\t{after}")

    print()
    print("QUALIFIED RUN DETAIL")
    print("--------------------")
    for row in run_details:
        print(
            f"run={row['run_id']} | {row['start_as_of']} -> {row['end_as_of']} | "
            f"bars={row['bars']} | events_inside={row['events_inside']} | "
            f"prev_event={row['previous_event_distance_bars']} bars | "
            f"next_event={row['next_event_distance_bars']} bars | "
            f"kind={row['scenario_kind']}"
        )

    print()
    print("FRESH EVENT DETAIL")
    print("------------------")
    for row in event_details:
        print(
            f"{row['as_of']} | ST={row['st_presence']}/{row['st_stage']} | "
            f"nextQ={row['next_qualified_distance_bars']} | prevQ={row['previous_qualified_distance_bars']} | "
            f"arbiter={row['arbiter_selection']} | entry={row['entry_action']} | "
            f"selected={row['selected_horizon']} | consumed={row['event_consumed']} | "
            f"position={row['lifecycle_previous_position']} | lifecycle={row['lifecycle_action']}"
        )

    if args.json_out is not None:
        payload = {
            "symbol": clean_symbol,
            "snapshots": len(snapshots),
            "frozen_cache_status": frozen.cache_status,
            "opportunity_calibration": calibration_source,
            "entry_execution_events": len(entry_events),
            "exit_execution_events": len(exit_events),
            "alignment_window_bars": window,
            "qualification_counts": dict(counters),
            "event_funnel": dict(event_counters),
            "event_lifecycle": dict(lifecycle_counters),
            "qualified_run_alignment": dict(run_event_counts),
            "qualified_nearest_event_abs_distance": _distance_summary(nearest_abs),
            "qualified_with_event_in_window": qualified_with_event_in_window,
            "qualified_runs": run_details,
            "fresh_event_details": event_details,
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON_REPORT\t{args.json_out}")

    print("ST_QUALIFICATION_EXECUTION_ALIGNMENT_OK")


if __name__ == "__main__":
    main()
