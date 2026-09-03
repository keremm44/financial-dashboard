from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from entry_reason_profile import _causal_warmup_start
from financial_dashboard.context.pattern_behavior_projection import PatternBehaviorPhase
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.engine import DecisionEngineConfig, prepare_horizon_assessment
from financial_dashboard.decision.execution_detect import (
    _direction as _execution_pattern_direction,
    _effective_phase as _execution_effective_phase,
    _pattern_row as _execution_pattern_row,
    detect_30m_execution_events,
)
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.participation import assess_participation
from financial_dashboard.decision.reaction import assess_reaction
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection, ThesisState
from financial_dashboard.decision.timeline_cache import load_frozen_decision_timeline

from st_control_evidence_diagnostic import _transition_view


_PARTICIPATION_TIMEFRAMES = ("1h", "2h", "4h")
_REACTION_TIMEFRAMES = ("1h", "30m")
_SR_TIMEFRAMES = ("1h", "2h", "30m")
_STRUCTURE_TIMEFRAMES = ("1h", "2h", "30m")


def _value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _row(projection: Any, timeframe: str) -> Any | None:
    if projection is None:
        return None
    lookup = getattr(projection, "for_timeframe", None)
    if lookup is None:
        return None
    try:
        return lookup(timeframe)
    except (KeyError, AttributeError, TypeError):
        return None


def _direction_value(side: StructuralDirection) -> int:
    if side is StructuralDirection.LONG:
        return 1
    if side is StructuralDirection.SHORT:
        return -1
    return 0


def _structure_signature(snapshot: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    projection = snapshot.structure
    for timeframe in _STRUCTURE_TIMEFRAMES:
        row = _row(projection, timeframe)
        if row is None:
            payload[timeframe] = None
            continue
        external = getattr(row, "external", None)
        internal = getattr(row, "internal", None)
        current_events = tuple(
            sorted(
                (
                    str(event.event_type),
                    int(event.direction),
                    str(event.confirmation_status),
                    str(event.validity),
                    str(event.relevance),
                    str(event.outcome),
                    str(event.bos_maturity),
                )
                for event in row.events
                if str(event.validity).upper() == "VALID"
                and str(event.relevance).upper() == "CURRENT"
            )
        )
        payload[timeframe] = {
            "quality": row.data_quality.value,
            "external_state": None if external is None else str(external.state),
            "external_direction": None if external is None else int(external.direction),
            "internal_state": None if internal is None else str(internal.state),
            "internal_direction": None if internal is None else int(internal.direction),
            "current_events": current_events,
        }
    return payload


def _participation_signature(
    snapshot: Any,
    *,
    incumbent: StructuralDirection,
    challenger: StructuralDirection,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for timeframe in _PARTICIPATION_TIMEFRAMES:
        native = _row(snapshot.participation_behavior, timeframe)
        incumbent_read = assess_participation(
            incumbent,
            snapshot.participation_behavior,
            timeframe=timeframe,
        )
        challenger_read = assess_participation(
            challenger,
            snapshot.participation_behavior,
            timeframe=timeframe,
        )
        payload[timeframe] = {
            "incumbent_state": incumbent_read.state.value,
            "challenger_state": challenger_read.state.value,
            "native": None
            if native is None
            else {
                "quality": native.ref.data_quality.value,
                "participation_trend": native.participation_trend.value,
                "effort_result": native.effort_result.value,
                "absorption": native.absorption.value,
                "break_participation": native.break_participation.value,
                "participation_direction": int(native.participation_direction),
                "evidence_direction": int(native.evidence_direction),
                "break_direction": int(native.break_direction),
                "break_stage": str(native.break_stage),
                "controlled_pullback": bool(native.controlled_pullback),
                "controlled_reaction": bool(native.controlled_reaction),
                "heavy_conflict": bool(native.heavy_conflict),
                "effort_result_class": native.effort_result_class,
            },
        }
    return payload


def _pattern_signature(snapshot: Any) -> dict[str, Any]:
    row = _execution_pattern_row(snapshot, "30m")
    if row is None:
        return {"available": False}
    effective_phase = _execution_effective_phase(row)
    direction = _execution_pattern_direction(row)
    return {
        "available": True,
        "projected_phase": _value(getattr(row, "phase", None)),
        "effective_phase": _value(effective_phase),
        "direction": _value(direction),
        "native_state": getattr(row, "native_state", None),
        "break_state_code": getattr(row, "break_state_code", None),
        "retest_state_code": getattr(row, "retest_state_code", None),
        "recovered": bool(
            effective_phase is not None
            and getattr(row, "phase", None) is PatternBehaviorPhase.UNAVAILABLE
            and str(getattr(row, "native_state", "") or "").strip()
        ),
    }


def _reaction_signature(
    snapshot: Any,
    *,
    incumbent: StructuralDirection,
    challenger: StructuralDirection,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for timeframe in _REACTION_TIMEFRAMES:
        incumbent_read = assess_reaction(
            incumbent,
            order_blocks=snapshot.order_block_behavior,
            fvg_engulfing=snapshot.fvg_engulfing_lifecycle,
            timeframes=(timeframe,),
        )
        challenger_read = assess_reaction(
            challenger,
            order_blocks=snapshot.order_block_behavior,
            fvg_engulfing=snapshot.fvg_engulfing_lifecycle,
            timeframes=(timeframe,),
        )
        payload[timeframe] = {
            "incumbent_state": incumbent_read.state.value,
            "challenger_state": challenger_read.state.value,
            "incumbent_failure": bool(incumbent_read.failure_present),
            "challenger_failure": bool(challenger_read.failure_present),
            "incumbent_confirmation": bool(incumbent_read.confirmation_present),
            "challenger_confirmation": bool(challenger_read.confirmation_present),
        }
    return payload


def _sr_signature(snapshot: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for timeframe in _SR_TIMEFRAMES:
        row = _row(snapshot.support_resistance, timeframe)
        if row is None:
            payload[timeframe] = None
            continue
        payload[timeframe] = {
            "quality": row.ref.data_quality.value,
            "state": row.state,
            "break_direction": int(row.break_direction),
            "price_location": row.price_location,
            "role_reversal_support": (
                row.role_reversal_support_low,
                row.role_reversal_support_high,
            ),
            "role_reversal_resistance": (
                row.role_reversal_resistance_low,
                row.role_reversal_resistance_high,
            ),
        }
    return payload


def _signature(snapshot: Any, view: Any) -> dict[str, Any]:
    return {
        "structure": _structure_signature(snapshot),
        "participation": _participation_signature(
            snapshot,
            incumbent=view.incumbent,
            challenger=view.challenger,
        ),
        "pattern_30m": _pattern_signature(snapshot),
        "reaction": _reaction_signature(
            snapshot,
            incumbent=view.incumbent,
            challenger=view.challenger,
        ),
        "support_resistance": _sr_signature(snapshot),
    }


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key in sorted(value):
            child = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten(value[key], child))
        return result
    return {prefix: value}


def _meaningful_path(path: str) -> bool:
    excluded_suffixes = (
        ".quality",
        ".recovered",
    )
    if path.endswith(excluded_suffixes):
        return False
    return True


def _delta(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    left = _flatten(previous)
    right = _flatten(current)
    changes: list[dict[str, Any]] = []
    for path in sorted(set(left) | set(right)):
        if not _meaningful_path(path):
            continue
        old = left.get(path)
        new = right.get(path)
        if old == new:
            continue
        changes.append({"path": path, "from": old, "to": new})
    return changes


def _category(path: str) -> str:
    if path.startswith("participation."):
        return "PARTICIPATION"
    if path.startswith("pattern_30m."):
        return "PATTERN"
    if path.startswith("reaction."):
        return "REACTION"
    if path.startswith("support_resistance."):
        return "SUPPORT_RESISTANCE"
    if path.startswith("structure."):
        return "STRUCTURE"
    return "OTHER"


def _episode_outcome(
    snapshots: list[Any],
    *,
    end_index: int,
    view: Any,
    engine_config: DecisionEngineConfig,
) -> tuple[str, str | None]:
    next_index = end_index + 1
    if next_index >= len(snapshots):
        return "RIGHT_CENSORED", None
    next_snapshot = snapshots[next_index]
    next_prepared = prepare_horizon_assessment(
        next_snapshot,
        DecisionHorizon.SHORT_TERM,
        config=engine_config,
    )
    structural = next_prepared.structural
    if structural.direction is view.challenger and structural.thesis_state is ThesisState.INTACT:
        return "TARGET_SIDE_ESTABLISHED", str(next_snapshot.as_of)
    if structural.direction is view.incumbent and structural.thesis_state is ThesisState.INTACT:
        return "INCUMBENT_REGAINED", str(next_snapshot.as_of)
    return "OTHER_OR_UNRESOLVED", str(next_snapshot.as_of)


def _print_change(change: dict[str, Any]) -> str:
    return f"{change['path']} : {change['from']} -> {change['to']}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnostic-only Phase A1C ST control change-point audit. It reports causal "
            "within-episode native/derived-read-model deltas without creating a control "
            "state, score, threshold, gate or trading action."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--max-print-changes", type=int, default=80)
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

    episodes: list[dict[str, Any]] = []
    episode_id = 0
    active: dict[str, Any] | None = None
    active_view: Any | None = None
    previous_signature: dict[str, Any] | None = None

    def close_episode(end_index: int) -> None:
        nonlocal active, active_view, previous_signature
        if active is None or active_view is None:
            return
        outcome, target_confirm_at = _episode_outcome(
            snapshots,
            end_index=end_index,
            view=active_view,
            engine_config=engine_config,
        )
        active["end_index"] = end_index
        active["end_as_of"] = str(snapshots[end_index].as_of)
        active["bars"] = end_index - active["start_index"] + 1
        active["outcome"] = outcome
        active["target_confirm_at"] = target_confirm_at
        episodes.append(active)
        active = None
        active_view = None
        previous_signature = None

    for index, snapshot in enumerate(snapshots):
        prepared = prepare_horizon_assessment(
            snapshot,
            DecisionHorizon.SHORT_TERM,
            config=engine_config,
        )
        view = _transition_view(prepared.structural)
        if view is None:
            if active is not None:
                close_episode(index - 1)
            continue

        if active is None or active["kind"] != view.kind:
            if active is not None:
                close_episode(index - 1)
            episode_id += 1
            active = {
                "episode_id": episode_id,
                "kind": view.kind,
                "incumbent": view.incumbent.value,
                "challenger": view.challenger.value,
                "start_index": index,
                "start_as_of": str(snapshot.as_of),
                "changes": [],
                "category_counts": Counter(),
                "fresh_execution": [],
            }
            active_view = view
            previous_signature = _signature(snapshot, view)
        else:
            current_signature = _signature(snapshot, view)
            assert previous_signature is not None
            changes = _delta(previous_signature, current_signature)
            if changes:
                offset = index - active["start_index"]
                for change in changes:
                    category = _category(change["path"])
                    active["category_counts"][category] += 1
                    active["changes"].append(
                        {
                            "offset_bars": offset,
                            "as_of": str(snapshot.as_of),
                            "category": category,
                            **change,
                        }
                    )
            previous_signature = current_signature

        if snapshot.as_of in entry_events:
            event = entry_events[snapshot.as_of]
            active["fresh_execution"].append(
                {
                    "offset_bars": index - active["start_index"],
                    "as_of": str(snapshot.as_of),
                    "kind": "ENTRY",
                    "side": _value(getattr(event, "side", getattr(event, "direction", None))),
                }
            )
        if snapshot.as_of in exit_events:
            event = exit_events[snapshot.as_of]
            active["fresh_execution"].append(
                {
                    "offset_bars": index - active["start_index"],
                    "as_of": str(snapshot.as_of),
                    "kind": "EXIT",
                    "side": _value(getattr(event, "side", getattr(event, "direction", None))),
                }
            )

    if active is not None:
        close_episode(len(snapshots) - 1)

    outcome_category_counts: dict[str, Counter] = defaultdict(Counter)
    outcome_episode_presence: dict[str, Counter] = defaultdict(Counter)
    for episode in episodes:
        outcome = episode["outcome"]
        categories_seen: set[str] = set()
        for change in episode["changes"]:
            category = change["category"]
            outcome_category_counts[outcome][category] += 1
            categories_seen.add(category)
        for category in categories_seen:
            outcome_episode_presence[outcome][category] += 1
        episode["category_counts"] = dict(sorted(episode["category_counts"].items()))

    report = {
        "symbol": symbol,
        "snapshots": len(snapshots),
        "frozen_cache_status": frozen.cache_status,
        "episodes": episodes,
        "outcome_category_change_counts": {
            outcome: dict(sorted(counter.items()))
            for outcome, counter in sorted(outcome_category_counts.items())
        },
        "outcome_category_episode_presence": {
            outcome: dict(sorted(counter.items()))
            for outcome, counter in sorted(outcome_episode_presence.items())
        },
        "invariants": {
            "trading_policy_mutation": "NONE",
            "control_state_created": False,
            "score_created": False,
            "threshold_created": False,
            "execution_counted_as_control_evidence": False,
            "outcome_used_to_generate_deltas": False,
            "pattern_phase_semantics": "PRODUCTION_EXECUTION_EFFECTIVE_PHASE",
        },
    }

    print("=" * 112)
    print("ST CONTROL CHANGE-POINT / DELTA DIAGNOSTIC — PHASE A1C")
    print("=" * 112)
    print(f"SYMBOL\t{symbol}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print(f"FROZEN_CACHE_STATUS\t{frozen.cache_status}")
    print(f"EPISODES\t{len(episodes)}")
    print("TRADING_POLICY_MUTATION\tNONE")
    print("CONTROL_STATE_CREATED\tNO")
    print("OUTCOME_USED_FOR_DELTA_GENERATION\tNO")

    print("\nOUTCOME / DOMAIN CHANGE PRESENCE")
    print("--------------------------------")
    for outcome in sorted(outcome_episode_presence):
        total = sum(1 for episode in episodes if episode["outcome"] == outcome)
        print(f"{outcome} episodes={total}")
        for category, count in sorted(outcome_episode_presence[outcome].items()):
            print(f"  {category:<24} episodes_with_change={count}")

    print("\nEPISODE DELTA SEQUENCES")
    print("-----------------------")
    for episode in episodes:
        print(
            f"EP {episode['episode_id']:02d} {episode['kind']} | {episode['start_as_of']} -> "
            f"{episode['end_as_of']} | bars={episode['bars']} | outcome={episode['outcome']} | "
            f"target_confirm={episode['target_confirm_at']}"
        )
        print(f"  category_counts={episode['category_counts']}")
        shown = 0
        for change in episode["changes"]:
            if shown >= args.max_print_changes:
                remaining = len(episode["changes"]) - shown
                print(f"  ... {remaining} additional deltas omitted from console; retained in JSON")
                break
            print(
                f"  +{change['offset_bars']:>3} | {change['as_of']} | "
                f"{change['category']:<18} | {_print_change(change)}"
            )
            shown += 1
        for event in episode["fresh_execution"]:
            print(
                f"  +{event['offset_bars']:>3} | {event['as_of']} | EXECUTION_OVERLAP | "
                f"{event['kind']}:{event['side']} (not control evidence)"
            )

    print("\nDIAGNOSTIC BOUNDARY")
    print("-------------------")
    print("Deltas are current-bar changes only; future episode outcome is attached after the episode closes.")
    print("No delta is promoted to confirmation, score, threshold, gate, BUY/SELL action or lifecycle policy.")
    print("Fresh execution is overlap metadata only and is never included in control evidence deltas.")

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"JSON_REPORT\t{args.json_out}")

    print("ST_CONTROL_DELTA_DIAGNOSTIC_OK")


if __name__ == "__main__":
    main()
