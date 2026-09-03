from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from entry_reason_profile import _causal_warmup_start
from financial_dashboard.context.envelope import ContextDataQuality, FactRef
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
from financial_dashboard.decision.participation import ParticipationState, assess_participation
from financial_dashboard.decision.reaction import ReactionState, assess_reaction
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection, ThesisState
from financial_dashboard.decision.timeline_cache import load_frozen_decision_timeline

from st_control_evidence_diagnostic import _transition_view


_ACTIVE_PATTERN_PHASES = frozenset(
    {
        PatternBehaviorPhase.BREAK_ATTEMPT,
        PatternBehaviorPhase.BREAK_CONFIRMING,
        PatternBehaviorPhase.BREAK_CONFIRMED,
        PatternBehaviorPhase.POST_BREAK_RETEST,
        PatternBehaviorPhase.RETEST_HELD,
        PatternBehaviorPhase.BREAK_FAILED,
    }
)
_CONFIRMED_PATTERN_PHASES = frozenset(
    {PatternBehaviorPhase.BREAK_CONFIRMED, PatternBehaviorPhase.RETEST_HELD}
)
_ACTIVE_SR_STATES = frozenset({"RANGE_BREAK_CANDIDATE", "RANGE_BREAK_CONFIRMED"})


def _value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _unique_refs(refs: Iterable[FactRef]) -> tuple[FactRef, ...]:
    by_key = {ref.deterministic_key: ref for ref in refs}
    return tuple(sorted(by_key.values(), key=lambda ref: ref.deterministic_key))


def _valid(ref: FactRef | None, *, as_of: Any) -> bool:
    return bool(
        ref is not None
        and ref.data_quality is ContextDataQuality.VALID
        and ref.is_available_at(as_of)
    )


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


def _structural_2h_audit(snapshot: Any) -> dict[str, Any]:
    raw = _row(snapshot.structure, "2h")
    bridge = None
    for node in snapshot.market_state.short_term.structural_map.nodes:
        if node.timeframe == "2h":
            bridge = node
            break
    raw_external = None if raw is None else raw.external
    return {
        "raw_present": raw is not None,
        "raw_quality": None if raw is None else raw.data_quality.value,
        "raw_external_present": raw_external is not None,
        "raw_external_state": None if raw_external is None else str(raw_external.state),
        "raw_external_direction": None if raw_external is None else int(raw_external.direction),
        "map_node_present": bridge is not None,
        "map_quality": None if bridge is None else bridge.data_quality.value,
        "map_external_state": None if bridge is None else bridge.external_state,
        "map_external_direction": None if bridge is None else bridge.external_direction,
        "map_bridge_state": snapshot.market_state.short_term.structural_map.bridge_state.value,
    }


def _pattern_audit(snapshot: Any) -> tuple[dict[str, Any], tuple[FactRef, ...]]:
    row = _execution_pattern_row(snapshot, "30m")
    if row is None:
        return {"present": False}, ()
    ref = getattr(row, "ref", None)
    phase = _execution_effective_phase(row)
    direction = _execution_pattern_direction(row)
    projected_phase = getattr(row, "phase", None)
    return (
        {
            "present": True,
            "raw_quality": None if ref is None else ref.data_quality.value,
            "projected_phase": _value(projected_phase),
            "effective_phase": _value(phase),
            "direction": _value(direction),
            "break_state_code": getattr(row, "break_state_code", None),
            "break_level": getattr(row, "break_level", None),
            "active_effective_break": phase in _ACTIVE_PATTERN_PHASES,
            "confirmed_effective_break": phase in _CONFIRMED_PATTERN_PHASES,
            "production_recovery_used": bool(
                ref is not None
                and ref.data_quality is ContextDataQuality.DATA_LIMITED
                and str(getattr(row, "native_state", "") or "").strip()
                and phase is not None
                and phase is not PatternBehaviorPhase.UNAVAILABLE
            ),
        },
        () if ref is None else (ref,),
    )


def _sr_audit(snapshot: Any) -> tuple[dict[str, Any], tuple[FactRef, ...]]:
    result: dict[str, Any] = {}
    refs: list[FactRef] = []
    for timeframe in ("1h", "2h", "30m"):
        item = _row(snapshot.support_resistance, timeframe)
        if item is None:
            continue
        refs.append(item.ref)
        result[timeframe] = {
            "usable": _valid(item.ref, as_of=snapshot.as_of),
            "state": item.state,
            "break_direction": int(item.break_direction),
            "active_break": str(item.state or "").upper() in _ACTIVE_SR_STATES
            and int(item.break_direction) in {-1, 1},
            "break_boundary": item.break_boundary,
            "price_location": item.price_location,
            "role_reversal_support": [
                item.role_reversal_support_low,
                item.role_reversal_support_high,
            ],
            "role_reversal_resistance": [
                item.role_reversal_resistance_low,
                item.role_reversal_resistance_high,
            ],
        }
    return result, _unique_refs(refs)


def _participation_audit(
    snapshot: Any,
    incumbent: StructuralDirection,
    challenger: StructuralDirection,
) -> tuple[dict[str, Any], tuple[FactRef, ...]]:
    result: dict[str, Any] = {}
    refs: list[FactRef] = []
    for timeframe in ("1h", "2h", "4h"):
        inc = assess_participation(incumbent, snapshot.participation_behavior, timeframe=timeframe)
        ch = assess_participation(challenger, snapshot.participation_behavior, timeframe=timeframe)
        refs.extend(inc.source_refs)
        refs.extend(ch.source_refs)
        native = _row(snapshot.participation_behavior, timeframe)
        result[timeframe] = {
            "incumbent": inc.state.value,
            "incumbent_unsupported_break": inc.unsupported_break,
            "challenger": ch.state.value,
            "challenger_unsupported_break": ch.unsupported_break,
            "native_usable": False if native is None else _valid(native.ref, as_of=snapshot.as_of),
            "native": None
            if native is None
            else {
                "participation_trend": native.participation_trend.value,
                "effort_result": native.effort_result.value,
                "absorption": native.absorption.value,
                "break_participation": native.break_participation.value,
                "participation_direction": int(native.participation_direction),
                "break_direction": int(native.break_direction),
                "controlled_pullback": bool(native.controlled_pullback),
                "controlled_reaction": bool(native.controlled_reaction),
                "heavy_conflict": bool(native.heavy_conflict),
            },
        }
    return result, _unique_refs(refs)


def _reaction_audit(
    snapshot: Any,
    incumbent: StructuralDirection,
    challenger: StructuralDirection,
) -> tuple[dict[str, Any], tuple[FactRef, ...]]:
    result: dict[str, Any] = {}
    refs: list[FactRef] = []
    for timeframe in ("1h", "30m"):
        inc = assess_reaction(
            incumbent,
            order_blocks=snapshot.order_block_behavior,
            fvg_engulfing=snapshot.fvg_engulfing_lifecycle,
            timeframes=(timeframe,),
        )
        ch = assess_reaction(
            challenger,
            order_blocks=snapshot.order_block_behavior,
            fvg_engulfing=snapshot.fvg_engulfing_lifecycle,
            timeframes=(timeframe,),
        )
        refs.extend(inc.source_refs)
        refs.extend(ch.source_refs)
        result[timeframe] = {
            "incumbent": inc.state.value,
            "challenger": ch.state.value,
            "incumbent_failure_present": inc.failure_present,
            "challenger_confirmation_present": ch.confirmation_present,
            "challenger_developing_present": ch.developing_present,
        }
    return result, _unique_refs(refs)


def _zone_refs(snapshot: Any) -> tuple[FactRef, ...]:
    refs: list[FactRef] = []
    if snapshot.order_block_behavior is not None:
        refs.extend(
            item.ref
            for item in snapshot.order_block_behavior.observations
            if item.timeframe in {"1h", "30m"}
        )
    lifecycle = snapshot.fvg_engulfing_lifecycle
    if lifecycle is not None:
        refs.extend(item.ref for item in lifecycle.fvg if item.ref.timeframe in {"1h", "30m"})
        refs.extend(
            item.ref for item in lifecycle.engulfing if item.ref.timeframe in {"1h", "30m"}
        )
    return _unique_refs(refs)


def _liquidity_refs(snapshot: Any) -> tuple[FactRef, ...]:
    projection = snapshot.liquidity
    if projection is None:
        return ()
    refs: list[FactRef] = []
    for timeframe in ("1h", "2h", "30m"):
        refs.extend(item.ref for item in projection.behavior_for_timeframe(timeframe))
    return _unique_refs(refs)


def _structure_refs(snapshot: Any) -> tuple[FactRef, ...]:
    refs: list[FactRef] = []
    for timeframe in ("1h", "2h", "30m"):
        item = _row(snapshot.structure, timeframe)
        if item is None:
            continue
        refs.extend(
            event.ref
            for event in item.events
            if event.confirmation_status == "CONFIRMED"
            and event.validity == "VALID"
            and event.relevance == "CURRENT"
            and event.ref.is_available_at(snapshot.as_of)
        )
    return _unique_refs(refs)


def _lineage_summary(refs_by_domain: dict[str, tuple[FactRef, ...]]) -> dict[str, Any]:
    domain_payload: dict[str, Any] = {}
    all_refs: list[FactRef] = []
    for domain, refs in sorted(refs_by_domain.items()):
        unique = _unique_refs(refs)
        known = [ref for ref in unique if ref.lineage_id is not None]
        unknown = [ref for ref in unique if ref.lineage_id is None]
        domain_payload[domain] = {
            "unique_refs": len(unique),
            "known_lineage_refs": len(known),
            "unknown_lineage_refs": len(unknown),
            "known_lineages": len({ref.lineage_id for ref in known}),
        }
        all_refs.extend(unique)
    all_unique = _unique_refs(all_refs)
    return {
        "by_domain": domain_payload,
        "unique_refs": len(all_unique),
        "known_lineage_refs": sum(ref.lineage_id is not None for ref in all_unique),
        "unknown_lineage_refs": sum(ref.lineage_id is None for ref in all_unique),
        "known_lineages": len({ref.lineage_id for ref in all_unique if ref.lineage_id is not None}),
    }


def _event_labels(row: dict[str, Any], view: Any) -> list[str]:
    labels: list[str] = []
    challenger = view.challenger.value

    for timeframe, item in row["participation"].items():
        if item["incumbent"] in {ParticipationState.WEAK.value, ParticipationState.OPPOSING.value}:
            labels.append(f"INCUMBENT_PARTICIPATION_{item['incumbent']}:{timeframe}")
        if item["challenger"] is ParticipationState.SUPPORTIVE.value:
            labels.append(f"CHALLENGER_PARTICIPATION_SUPPORTIVE:{timeframe}")
        native = item["native"]
        if native is not None and native["effort_result"] == "WEAK_RESULT":
            labels.append(f"NATIVE_WEAK_EFFORT_RESULT:{timeframe}")
        if native is not None and native["break_participation"] in {"UNSUPPORTED", "RECLAIMED"}:
            labels.append(
                f"NATIVE_BREAK_{native['break_participation']}:{timeframe}:DIR={native['break_direction']}"
            )
        if native is not None and native["absorption"] in {"CANDIDATE", "CONFIRMED"}:
            labels.append(f"NATIVE_ABSORPTION_{native['absorption']}:{timeframe}")

    for timeframe, item in row["reaction"].items():
        if item["incumbent"] == ReactionState.FAILED.value or item["incumbent_failure_present"]:
            labels.append(f"INCUMBENT_REACTION_FAILED:{timeframe}")
        if item["challenger"] == ReactionState.DEVELOPING.value:
            labels.append(f"CHALLENGER_REACTION_DEVELOPING:{timeframe}")
        if item["challenger"] == ReactionState.CONFIRMED.value:
            labels.append(f"CHALLENGER_REACTION_CONFIRMED:{timeframe}")

    pattern = row["pattern"]
    if pattern.get("active_effective_break") and pattern.get("direction") == challenger:
        labels.append(f"CHALLENGER_PATTERN_{pattern['effective_phase']}:30m")
    if pattern.get("effective_phase") == PatternBehaviorPhase.BREAK_FAILED.value:
        labels.append(f"PATTERN_BREAK_FAILED:30m:DIR={pattern.get('direction')}")

    challenger_sign = 1 if challenger == StructuralDirection.LONG.value else -1
    for timeframe, item in row["support_resistance"].items():
        if item["usable"] and item["active_break"] and item["break_direction"] == challenger_sign:
            labels.append(f"CHALLENGER_SR_BREAK:{timeframe}:{item['state']}")

    if row["break_relation_count"]:
        labels.append("CROSS_DOMAIN_BREAK_RELATION_PRESENT")
    if row["fresh_entry_event"]:
        labels.append("FRESH_LONG_EXECUTION")
    if row["fresh_exit_event"]:
        labels.append("FRESH_SHORT_EXECUTION")
    return sorted(set(labels))


def _episode_outcome(snapshots: list[Any], end_index: int, view: Any, config: Any) -> str:
    if end_index + 1 >= len(snapshots):
        return "RIGHT_CENSORED"
    prepared = prepare_horizon_assessment(
        snapshots[end_index + 1], DecisionHorizon.SHORT_TERM, config=config
    )
    if prepared.structural.direction is view.challenger and prepared.structural.thesis_state is ThesisState.INTACT:
        return "TARGET_SIDE_ESTABLISHED"
    if prepared.structural.direction is view.incumbent and prepared.structural.thesis_state is ThesisState.INTACT:
        return "INCUMBENT_REGAINED"
    return "OTHER_OR_UNRESOLVED"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Phase A1B diagnostic-only ST transition episode timeline. It adds first-event timing, "
            "direct 2H bridge parity, break-relation availability reasons and episode-unique lineage "
            "coverage without creating a control state or changing trading policy."
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
    start = _causal_warmup_start(store, symbol=symbol, requested_start=args.start)
    history = HistoricalDecisionInputConfig(
        pattern_profile=args.pattern_profile,
        max_bars=args.max_bars,
        start_at=start,
        end_at=args.end,
    )
    frozen = load_frozen_decision_timeline(store, symbol, config=history)
    snapshots = list(frozen.replay.snapshots)
    entry_events, exit_events = detect_30m_execution_events(snapshots)
    config = DecisionEngineConfig()

    episodes: list[dict[str, Any]] = []
    bridge_counts = Counter()
    break_inputs = Counter()
    active: dict[str, Any] | None = None

    def close_episode(end_index: int) -> None:
        nonlocal active
        if active is None:
            return
        rows = active["rows"]
        first_by_label: dict[str, dict[str, Any]] = {}
        for offset, row in enumerate(rows):
            for label in row["event_labels"]:
                first_by_label.setdefault(
                    label,
                    {"bar_offset": offset, "as_of": row["as_of"], "price": row["price"]},
                )

        refs_by_domain: dict[str, list[FactRef]] = defaultdict(list)
        for row in rows:
            for domain, refs in row["refs_by_domain"].items():
                refs_by_domain[domain].extend(refs)
        lineage = _lineage_summary(
            {domain: _unique_refs(refs) for domain, refs in refs_by_domain.items()}
        )

        next_confirmation = None
        if end_index + 1 < len(snapshots):
            next_confirmation = str(snapshots[end_index + 1].as_of)

        episodes.append(
            {
                "episode_id": active["episode_id"],
                "kind": active["view"].kind,
                "incumbent": active["view"].incumbent.value,
                "challenger": active["view"].challenger.value,
                "start_as_of": rows[0]["as_of"],
                "end_as_of": rows[-1]["as_of"],
                "bars": len(rows),
                "outcome": _episode_outcome(snapshots, end_index, active["view"], config),
                "target_confirmation_as_of": next_confirmation,
                "first_events": dict(sorted(first_by_label.items())),
                "lineage_episode_unique": lineage,
                "bridge_first": rows[0]["bridge"],
                "bridge_last": rows[-1]["bridge"],
                "break_relation_snapshots": sum(row["break_relation_count"] > 0 for row in rows),
                "pattern_recovery_snapshots": sum(
                    bool(row["pattern"].get("production_recovery_used")) for row in rows
                ),
                "fresh_entry_events": sum(row["fresh_entry_event"] for row in rows),
                "fresh_exit_events": sum(row["fresh_exit_event"] for row in rows),
            }
        )
        active = None

    episode_id = 0
    for index, snapshot in enumerate(snapshots):
        prepared = prepare_horizon_assessment(snapshot, DecisionHorizon.SHORT_TERM, config=config)
        view = _transition_view(prepared.structural)
        if view is None:
            if active is not None:
                close_episode(index - 1)
            continue
        if active is None or active["view"].kind != view.kind:
            if active is not None:
                close_episode(index - 1)
            episode_id += 1
            active = {"episode_id": episode_id, "view": view, "rows": []}

        bridge = _structural_2h_audit(snapshot)
        bridge_counts[f"RAW_PRESENT:{bridge['raw_present']}"] += 1
        bridge_counts[f"RAW_QUALITY:{bridge['raw_quality']}"] += 1
        bridge_counts[f"RAW_EXTERNAL:{bridge['raw_external_present']}"] += 1
        bridge_counts[f"MAP_QUALITY:{bridge['map_quality']}"] += 1
        bridge_counts[f"MAP_BRIDGE_STATE:{bridge['map_bridge_state']}"] += 1

        pattern, pattern_refs = _pattern_audit(snapshot)
        sr, sr_refs = _sr_audit(snapshot)
        participation, participation_refs = _participation_audit(
            snapshot, view.incumbent, view.challenger
        )
        reaction, _reaction_refs = _reaction_audit(snapshot, view.incumbent, view.challenger)

        break_view = snapshot.break_relations
        for item in break_view.evidence:
            break_inputs[f"EVIDENCE:{item.domain.value}:{item.timeframe}"] += 1
        for relation in break_view.relations:
            break_inputs[f"RELATION:{relation.relation.value}"] += 1
        if pattern.get("active_effective_break"):
            break_inputs["PATTERN_EFFECTIVE_ACTIVE_BREAK"] += 1
        if pattern.get("production_recovery_used"):
            break_inputs["PATTERN_EFFECTIVE_RECOVERED"] += 1
        for timeframe, item in sr.items():
            if item["usable"] and item["active_break"]:
                break_inputs[f"SR_ACTIVE_BREAK:{timeframe}"] += 1

        refs_by_domain = {
            "STRUCTURE": _structure_refs(snapshot),
            "PARTICIPATION": participation_refs,
            "PATTERN": pattern_refs,
            "SUPPORT_RESISTANCE": sr_refs,
            "LIQUIDITY": _liquidity_refs(snapshot),
            "ZONES": _zone_refs(snapshot),
        }

        row_payload = {
            "as_of": str(snapshot.as_of),
            "price": float(snapshot.current_price),
            "bridge": bridge,
            "pattern": pattern,
            "support_resistance": sr,
            "participation": participation,
            "reaction": reaction,
            "break_relation_count": len(break_view.relations),
            "fresh_entry_event": snapshot.as_of in entry_events,
            "fresh_exit_event": snapshot.as_of in exit_events,
            "refs_by_domain": refs_by_domain,
        }
        row_payload["event_labels"] = _event_labels(row_payload, view)
        active["rows"].append(row_payload)

    if active is not None:
        close_episode(len(snapshots) - 1)

    outcome_counts = Counter(f"{ep['kind']}:{ep['outcome']}" for ep in episodes)
    report = {
        "symbol": symbol,
        "snapshots": len(snapshots),
        "frozen_cache_status": frozen.cache_status,
        "episodes": episodes,
        "episode_outcomes": dict(sorted(outcome_counts.items())),
        "bridge_parity_counts": dict(sorted(bridge_counts.items())),
        "break_input_counts": dict(sorted(break_inputs.items())),
        "invariants": {
            "trading_policy_mutation": "NONE",
            "structure_mutation": "NONE",
            "qualification_mutation": "NONE",
            "control_state_created": False,
            "unknown_lineage_counted_as_independent": False,
            "snapshot_ref_repetition_removed_in_episode_lineage": True,
            "pattern_phase_semantics": "PRODUCTION_EXECUTION_EFFECTIVE_PHASE",
        },
    }

    print("=" * 112)
    print("ST CONTROL EVIDENCE EPISODE TIMELINE / LINEAGE DIAGNOSTIC — PHASE A1B")
    print("=" * 112)
    print(f"SYMBOL\t{symbol}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print(f"FROZEN_CACHE_STATUS\t{frozen.cache_status}")
    print(f"EPISODES\t{len(episodes)}")
    print("TRADING_POLICY_MUTATION\tNONE")
    print("CONTROL_STATE_CREATED\tNO")

    print("\n2H BRIDGE DIRECT PARITY")
    print("-----------------------")
    for key, count in sorted(bridge_counts.items()):
        print(f"{key:<42} {count:>5}")
    print("NOTE\tRaw 2H projection and derived ST bridge are reported separately; no repair is performed.")

    print("\nBREAK RELATION INPUT AUDIT")
    print("--------------------------")
    if not break_inputs:
        print("NONE")
    else:
        for key, count in sorted(break_inputs.items()):
            print(f"{key:<48} {count:>5}")
    print("NOTE\tProduction-effective Pattern activity is shown separately from break_relations eligibility.")

    print("\nEPISODE-FIRST ECONOMIC EVENT TIMELINE")
    print("-------------------------------------")
    for ep in episodes:
        print(
            f"EP {ep['episode_id']:02d} {ep['kind']} | {ep['start_as_of']} -> {ep['end_as_of']} | "
            f"bars={ep['bars']} | outcome={ep['outcome']} | target_confirm={ep['target_confirmation_as_of']}"
        )
        if not ep["first_events"]:
            print("  FIRST_EVENTS: NONE")
        else:
            for label, payload in ep["first_events"].items():
                print(
                    f"  +{payload['bar_offset']:>3} bars | {payload['as_of']} | {label}"
                )

    print("\nEPISODE-UNIQUE LINEAGE COVERAGE")
    print("-------------------------------")
    for ep in episodes:
        lineage = ep["lineage_episode_unique"]
        print(
            f"EP {ep['episode_id']:02d} {ep['kind']} | unique={lineage['unique_refs']} "
            f"known={lineage['known_lineage_refs']} unknown={lineage['unknown_lineage_refs']} "
            f"known_origins={lineage['known_lineages']}"
        )
        for domain, payload in lineage["by_domain"].items():
            print(
                f"  {domain:<20} unique={payload['unique_refs']:<5} known={payload['known_lineage_refs']:<5} "
                f"unknown={payload['unknown_lineage_refs']:<5} origins={payload['known_lineages']}"
            )

    print("\nDIAGNOSTIC BOUNDARY")
    print("-------------------")
    print("No control state, score, threshold, vote, gate, BUY/SELL action or lifecycle policy is produced.")
    print("First-event labels are descriptive observations, not confirmations or weighted evidence.")
    print("Unknown lineage remains unknown and is never promoted to independence.")

    if args.json_out is not None:
        serializable = json.loads(json.dumps(report, default=str))
        # Internal FactRef objects are useful while aggregating but must not leak into JSON.
        for ep in serializable["episodes"]:
            pass
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
        print(f"JSON_REPORT\t{args.json_out}")

    print("ST_CONTROL_EPISODE_TIMELINE_DIAGNOSTIC_OK")


if __name__ == "__main__":
    main()
