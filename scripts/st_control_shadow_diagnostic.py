from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from entry_reason_profile import _causal_warmup_start
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.engine import DecisionEngineConfig, prepare_horizon_assessment
from financial_dashboard.decision.execution_detect import detect_30m_execution_events
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.st_control import assess_short_term_control
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection, ThesisState
from financial_dashboard.decision.timeline_cache import load_frozen_decision_timeline


def _opposite(side: StructuralDirection) -> StructuralDirection | None:
    if side is StructuralDirection.LONG:
        return StructuralDirection.SHORT
    if side is StructuralDirection.SHORT:
        return StructuralDirection.LONG
    return None


def _episode_outcome(
    snapshots: list[Any],
    *,
    end_index: int,
    incumbent: StructuralDirection,
    challenger: StructuralDirection,
    config: DecisionEngineConfig,
) -> str:
    next_index = end_index + 1
    if next_index >= len(snapshots):
        return "RIGHT_CENSORED"
    prepared = prepare_horizon_assessment(
        snapshots[next_index],
        DecisionHorizon.SHORT_TERM,
        config=config,
    )
    structural = prepared.structural
    if structural.direction is challenger and structural.thesis_state is ThesisState.INTACT:
        return "TARGET_SIDE_ESTABLISHED"
    if structural.direction is incumbent and structural.thesis_state is ThesisState.INTACT:
        return "INCUMBENT_REGAINED"
    return "OTHER_OR_UNRESOLVED"


def _compress_path(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    previous = object()
    for row in rows:
        value = row[key]
        if value == previous:
            continue
        result.append(
            {
                "as_of": row["as_of"],
                "bar_offset": row["bar_offset"],
                "value": value,
            }
        )
        previous = value
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Shadow-only ShortTermControlAssessment replay diagnostic. The control read-model "
            "is evaluated beside the canonical Decision chain and never wired into policy."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
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
    frozen = load_frozen_decision_timeline(store, symbol, config=history_config)
    snapshots = list(frozen.replay.snapshots)
    entry_events, exit_events = detect_30m_execution_events(snapshots)
    engine_config = DecisionEngineConfig()

    rows: list[dict[str, Any]] = []
    state_counts = Counter()
    incumbent_counts = Counter()
    challenger_counts = Counter()
    quality_counts = Counter()
    role_counts = Counter()
    entry_overlap = Counter()
    exit_overlap = Counter()
    causality_violations: list[dict[str, Any]] = []

    for index, snapshot in enumerate(snapshots):
        prepared = prepare_horizon_assessment(
            snapshot,
            DecisionHorizon.SHORT_TERM,
            config=engine_config,
        )
        control = assess_short_term_control(snapshot, structural=prepared.structural)
        for ref in control.source_refs:
            if not ref.is_available_at(snapshot.as_of):
                causality_violations.append(
                    {
                        "as_of": str(snapshot.as_of),
                        "native_id": ref.native_id,
                        "available_at": str(ref.available_at),
                    }
                )

        state_counts[control.control_state.value] += 1
        incumbent_counts[control.incumbent_condition.value] += 1
        challenger_counts[control.challenger_condition.value] += 1
        quality_counts[control.data_quality.value] += 1
        for item in control.evidence:
            role_counts[item.role.value] += 1
        if snapshot.as_of in entry_events:
            entry_overlap[control.control_state.value] += 1
        if snapshot.as_of in exit_events:
            exit_overlap[control.control_state.value] += 1

        rows.append(
            {
                "snapshot_index": index,
                "as_of": str(snapshot.as_of),
                "structure_direction": prepared.structural.direction.value,
                "structure_thesis_state": prepared.structural.thesis_state.value,
                "structure_transition_target": (
                    None
                    if prepared.structural.transition_target is None
                    else prepared.structural.transition_target.value
                ),
                "control_state": control.control_state.value,
                "incumbent_condition": control.incumbent_condition.value,
                "challenger_condition": control.challenger_condition.value,
                "control_quality": control.data_quality.value,
                "roles": sorted({item.role.value for item in control.evidence}),
                "source_ref_count": len(control.source_refs),
                "unknown_lineage_ref_count": len(control.unresolved_lineage_refs),
                "fresh_entry_overlap": snapshot.as_of in entry_events,
                "fresh_exit_overlap": snapshot.as_of in exit_events,
            }
        )

    episodes: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None
    for row in rows:
        transitioning = row["structure_thesis_state"] == ThesisState.TRANSITIONING.value
        target_token = row["structure_transition_target"]
        if not transitioning or target_token is None:
            if active is not None:
                episodes.append(active)
                active = None
            continue
        incumbent = StructuralDirection(row["structure_direction"])
        challenger = StructuralDirection(target_token)
        if active is None or active["incumbent"] != incumbent.value or active["challenger"] != challenger.value:
            if active is not None:
                episodes.append(active)
            active = {
                "incumbent": incumbent.value,
                "challenger": challenger.value,
                "start_index": row["snapshot_index"],
                "end_index": row["snapshot_index"],
                "rows": [],
            }
        active["end_index"] = row["snapshot_index"]
        active["rows"].append(row)
    if active is not None:
        episodes.append(active)

    episode_reports: list[dict[str, Any]] = []
    outcome_state_counts = Counter()
    outcome_resolution_counts = Counter()
    for episode_id, episode in enumerate(episodes, start=1):
        incumbent = StructuralDirection(episode["incumbent"])
        challenger = StructuralDirection(episode["challenger"])
        episode_rows = []
        for row in episode["rows"]:
            item = dict(row)
            item["bar_offset"] = row["snapshot_index"] - episode["start_index"]
            episode_rows.append(item)
        outcome = _episode_outcome(
            snapshots,
            end_index=episode["end_index"],
            incumbent=incumbent,
            challenger=challenger,
            config=engine_config,
        )
        resolution_index = episode["end_index"] + 1
        resolution_row = rows[resolution_index] if resolution_index < len(rows) else None
        resolution_control_state = (
            None if resolution_row is None else resolution_row["control_state"]
        )
        if resolution_control_state is not None:
            outcome_resolution_counts[f"{outcome}:{resolution_control_state}"] += 1
        unique_states = sorted({row["control_state"] for row in episode_rows})
        for state in unique_states:
            outcome_state_counts[f"{outcome}:{state}"] += 1
        episode_reports.append(
            {
                "episode_id": episode_id,
                "incumbent": incumbent.value,
                "challenger": challenger.value,
                "start_as_of": episode_rows[0]["as_of"],
                "end_as_of": episode_rows[-1]["as_of"],
                "bars": len(episode_rows),
                "outcome": outcome,
                "control_path": _compress_path(episode_rows, "control_state"),
                "incumbent_path": _compress_path(episode_rows, "incumbent_condition"),
                "challenger_path": _compress_path(episode_rows, "challenger_condition"),
                "states_seen": unique_states,
                "resolution_as_of": None if resolution_row is None else resolution_row["as_of"],
                "resolution_control_state": resolution_control_state,
                "resolution_roles": [] if resolution_row is None else resolution_row["roles"],
                "fresh_entry_overlaps": sum(row["fresh_entry_overlap"] for row in episode_rows),
                "fresh_exit_overlaps": sum(row["fresh_exit_overlap"] for row in episode_rows),
            }
        )

    report = {
        "symbol": symbol,
        "snapshots": len(snapshots),
        "frozen_cache_status": frozen.cache_status,
        "entry_execution_events": len(entry_events),
        "exit_execution_events": len(exit_events),
        "state_counts": dict(sorted(state_counts.items())),
        "incumbent_condition_counts": dict(sorted(incumbent_counts.items())),
        "challenger_condition_counts": dict(sorted(challenger_counts.items())),
        "quality_counts": dict(sorted(quality_counts.items())),
        "evidence_role_counts": dict(sorted(role_counts.items())),
        "entry_overlap_by_control_state": dict(sorted(entry_overlap.items())),
        "exit_overlap_by_control_state": dict(sorted(exit_overlap.items())),
        "outcome_state_presence": dict(sorted(outcome_state_counts.items())),
        "outcome_resolution_state": dict(sorted(outcome_resolution_counts.items())),
        "episodes": episode_reports,
        "causality_violations": causality_violations,
        "invariants": {
            "trading_policy_mutation": "NONE",
            "engine_wiring": "NO",
            "scenario_mutation": "NONE",
            "eligibility_mutation": "NONE",
            "qualification_mutation": "NONE",
            "arbiter_mutation": "NONE",
            "buy_sell_mutation": "NONE",
            "lifecycle_mutation": "NONE",
            "timing_consumed_by_control": False,
            "opportunity_consumed_by_control": False,
            "execution_consumed_by_control": False,
            "context_permission_consumed_by_control": False,
            "unknown_lineage_counted_as_independent": False,
            "stateless_per_snapshot": True,
        },
    }

    print("=" * 112)
    print("SHADOW SHORT-TERM CONTROL ASSESSMENT — TUR 2")
    print("=" * 112)
    print(f"SYMBOL\t{symbol}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print(f"FROZEN_CACHE_STATUS\t{frozen.cache_status}")
    print(f"ENTRY_EXECUTION_EVENTS\t{len(entry_events)}")
    print(f"EXIT_EXECUTION_EVENTS\t{len(exit_events)}")
    print("TRADING_POLICY_MUTATION\tNONE")
    print("ENGINE_WIRING\tNO")
    print("CONTROL_PERSISTENCE\tNONE")
    print("CONTROL_SCORE_VOTE\tNONE")
    print(f"CAUSALITY_VIOLATIONS\t{len(causality_violations)}")

    print("\nCONTROL STATE DISTRIBUTION")
    print("--------------------------")
    for key, count in sorted(state_counts.items()):
        print(f"{key:<32} {count:>6}")

    print("\nINCUMBENT / CHALLENGER CONDITIONS")
    print("---------------------------------")
    for key, count in sorted(incumbent_counts.items()):
        print(f"INCUMBENT:{key:<24} {count:>6}")
    for key, count in sorted(challenger_counts.items()):
        print(f"CHALLENGER:{key:<23} {count:>6}")

    print("\nEVIDENCE ROLE DISTRIBUTION")
    print("--------------------------")
    for key, count in sorted(role_counts.items()):
        print(f"{key:<36} {count:>6}")

    print("\nTRANSITION EPISODE CONTROL PATHS")
    print("--------------------------------")
    for item in episode_reports:
        path = " -> ".join(
            f"+{point['bar_offset']}:{point['value']}" for point in item["control_path"]
        )
        print(
            f"EP {item['episode_id']:02d} {item['incumbent']}->{item['challenger']} | "
            f"{item['start_as_of']} -> {item['end_as_of']} | bars={item['bars']} | "
            f"outcome={item['outcome']}"
        )
        print(f"  CONTROL {path}")
        if item["resolution_control_state"] is None:
            print("  RESOLUTION RIGHT_CENSORED")
        else:
            roles = ",".join(item["resolution_roles"]) or "NONE"
            print(
                f"  RESOLUTION next:{item['resolution_control_state']} @ {item['resolution_as_of']} "
                f"roles={roles}"
            )
        print(
            "  EXECUTION_OVERLAP "
            f"entry={item['fresh_entry_overlaps']} exit={item['fresh_exit_overlaps']} "
            "(not control evidence)"
        )

    print("\nOUTCOME / IN-TRANSITION CONTROL STATE PRESENCE")
    print("-----------------------------------------------")
    for key, count in sorted(outcome_state_counts.items()):
        print(f"{key:<64} {count:>4}")

    print("\nOUTCOME / RESOLUTION CONTROL STATE")
    print("----------------------------------")
    for key, count in sorted(outcome_resolution_counts.items()):
        print(f"{key:<64} {count:>4}")

    print("\nSHADOW BOUNDARY")
    print("---------------")
    print("Control is evaluated beside canonical Decision and is not carried into PreparedHorizonAssessment.")
    print("Timing, Opportunity, Execution, Context and Permission are not control inputs.")
    print("Fresh execution is correlated only after control evaluation.")
    print("Unknown lineage is reported but never converted into independent confirmation.")

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"JSON_REPORT\t{args.json_out}")

    if causality_violations:
        raise SystemExit("ST_CONTROL_SHADOW_CAUSALITY_VIOLATION")
    print("ST_CONTROL_SHADOW_DIAGNOSTIC_OK")


if __name__ == "__main__":
    main()
