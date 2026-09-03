from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from entry_reason_profile import _calibration, _causal_warmup_start
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.arbiter import prepare_entry_arbitration
from financial_dashboard.decision.engine import DecisionEngineConfig
from financial_dashboard.decision.execution_detect import detect_30m_execution_events
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.scenario import ScenarioPresence, ScenarioStage
from financial_dashboard.decision.timeline_cache import load_frozen_decision_timeline


def _value(value) -> str:
    return getattr(value, "value", str(value))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnostic-only ST qualification/execution timing alignment audit."
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
    parser.add_argument("--window-bars", type=int, default=6)
    parser.add_argument("--auto-calibration", action="store_true")
    parser.add_argument("--opportunity-calibration", type=Path, default=None)
    parser.add_argument("--opportunity-none-max-atr", type=float, default=None)
    parser.add_argument("--opportunity-compressed-max-atr", type=float, default=None)
    parser.add_argument("--opportunity-moderate-max-atr", type=float, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    store = ParquetOHLCVStore(args.cache_root)
    symbol = normalize_symbol(args.symbol)
    effective_start = _causal_warmup_start(store, symbol=symbol, requested_start=args.start)
    history_config = HistoricalDecisionInputConfig(
        pattern_profile=args.pattern_profile,
        max_bars=args.max_bars,
        start_at=effective_start,
        end_at=args.end,
    )
    calibration, calibration_source = _calibration(args, cache_root=args.cache_root, symbol=symbol)
    engine_config = DecisionEngineConfig(opportunity_calibration=calibration)
    frozen = load_frozen_decision_timeline(store, symbol, config=history_config)
    snapshots = list(frozen.replay.snapshots)
    entry_events, exit_events = detect_30m_execution_events(snapshots)

    rows: list[dict] = []
    for index, snapshot in enumerate(snapshots):
        prepared = prepare_entry_arbitration(snapshot, config=engine_config)
        st = prepared.short_term.scenario
        arbitration = prepared.arbitration
        event = entry_events.get(snapshot.as_of)
        rows.append({
            "index": index,
            "as_of": str(snapshot.as_of),
            "presence": _value(st.presence),
            "stage": _value(st.stage),
            "qualified": st.presence is ScenarioPresence.PRESENT and st.stage is ScenarioStage.QUALIFIED,
            "fresh_event": event is not None,
            "event_side": None if event is None else _value(event.side),
            "arbiter_selection": _value(arbitration.selection),
            "arbiter_state": _value(arbitration.state),
            "arbiter_reasons": list(arbitration.reasons),
            "scenario_reasons": list(st.reasons),
            "waiting_for": list(st.waiting_for),
            "target_identity": st.active_target_identity,
        })

    qualified_indices = [row["index"] for row in rows if row["qualified"]]
    event_indices = [row["index"] for row in rows if row["fresh_event"]]

    runs: list[dict] = []
    start = None
    for i, row in enumerate(rows + [{"qualified": False}]):
        if row["qualified"] and start is None:
            start = i
        elif not row["qualified"] and start is not None:
            end = i - 1
            inside = [j for j in event_indices if start <= j <= end]
            before = [start - j for j in event_indices if 0 < start - j <= args.window_bars]
            after = [j - end for j in event_indices if 0 < j - end <= args.window_bars]
            runs.append({
                "start_index": start,
                "end_index": end,
                "start_as_of": rows[start]["as_of"],
                "end_as_of": rows[end]["as_of"],
                "bars": end - start + 1,
                "events_inside": len(inside),
                "event_as_of_inside": [rows[j]["as_of"] for j in inside],
                "nearest_event_before_bars": min(before) if before else None,
                "nearest_event_after_bars": min(after) if after else None,
                "target_identity_start": rows[start]["target_identity"],
                "arbiter_selection_start": rows[start]["arbiter_selection"],
            })
            start = None

    event_detail: list[dict] = []
    for j in event_indices:
        before_q = [j - i for i in qualified_indices if i < j and j - i <= args.window_bars]
        after_q = [i - j for i in qualified_indices if i > j and i - j <= args.window_bars]
        row = rows[j]
        event_detail.append({
            "as_of": row["as_of"],
            "event_side": row["event_side"],
            "st_presence": row["presence"],
            "st_stage": row["stage"],
            "qualified_same_bar": row["qualified"],
            "nearest_qualified_before_bars": min(before_q) if before_q else None,
            "nearest_qualified_after_bars": min(after_q) if after_q else None,
            "arbiter_selection": row["arbiter_selection"],
            "arbiter_state": row["arbiter_state"],
            "arbiter_reasons": row["arbiter_reasons"],
            "scenario_reasons": row["scenario_reasons"],
            "waiting_for": row["waiting_for"],
        })

    counts = Counter()
    counts["QUALIFIED_SNAPSHOTS"] = len(qualified_indices)
    counts["QUALIFIED_RUNS"] = len(runs)
    counts["RUNS_WITH_FRESH_EVENT_INSIDE"] = sum(run["events_inside"] > 0 for run in runs)
    counts["RUNS_WITHOUT_FRESH_EVENT_INSIDE"] = sum(run["events_inside"] == 0 for run in runs)
    counts["FRESH_EVENTS"] = len(event_indices)
    counts["FRESH_EVENT_WHILE_QUALIFIED"] = sum(row["qualified_same_bar"] for row in event_detail)
    counts["FRESH_EVENT_WITH_QUALIFIED_WITHIN_WINDOW_AFTER"] = sum(
        row["nearest_qualified_after_bars"] is not None for row in event_detail
    )
    counts["FRESH_EVENT_WITH_QUALIFIED_WITHIN_WINDOW_BEFORE"] = sum(
        row["nearest_qualified_before_bars"] is not None for row in event_detail
    )

    print("=" * 88)
    print("ST QUALIFICATION / EXECUTION ALIGNMENT DIAGNOSTIC")
    print("=" * 88)
    print(f"SYMBOL\t{symbol}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print(f"FROZEN_CACHE_STATUS\t{frozen.cache_status}")
    print(f"OPPORTUNITY_CALIBRATION\t{calibration_source}")
    print(f"ENTRY_EXECUTION_EVENTS\t{len(entry_events)}")
    print(f"EXIT_EXECUTION_EVENTS\t{len(exit_events)}")
    print(f"WINDOW_BARS\t{args.window_bars}")
    print("TRADING_POLICY_MUTATION\tNONE")
    print()

    print("SUMMARY")
    print("-------")
    for key, value in counts.items():
        print(f"{key}\t{value}")

    print("\nQUALIFIED RUN DETAIL")
    print("--------------------")
    for number, run in enumerate(runs, 1):
        print(
            f"RUN {number:02d} | {run['start_as_of']} -> {run['end_as_of']} | bars={run['bars']} | "
            f"events_inside={run['events_inside']} | before={run['nearest_event_before_bars']} | "
            f"after={run['nearest_event_after_bars']} | arbiter={run['arbiter_selection_start']} | "
            f"target={run['target_identity_start']}"
        )

    print("\nFRESH EVENT DETAIL")
    print("------------------")
    for row in event_detail:
        print(
            f"{row['as_of']} | side={row['event_side']} | ST={row['st_presence']}/{row['st_stage']} | "
            f"qualified_same_bar={row['qualified_same_bar']} | q_before={row['nearest_qualified_before_bars']} | "
            f"q_after={row['nearest_qualified_after_bars']} | arbiter={row['arbiter_selection']}/{row['arbiter_state']}"
        )

    if args.json_out is not None:
        report = {
            "symbol": symbol,
            "snapshots": len(snapshots),
            "frozen_cache_status": frozen.cache_status,
            "calibration_source": str(calibration_source),
            "window_bars": args.window_bars,
            "counts": dict(counts),
            "qualified_runs": runs,
            "fresh_event_detail": event_detail,
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"JSON_REPORT\t{args.json_out}")

    print("ST_QUALIFICATION_EXECUTION_ALIGNMENT_OK")


if __name__ == "__main__":
    main()
