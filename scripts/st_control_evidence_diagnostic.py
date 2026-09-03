from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from entry_reason_profile import _causal_warmup_start
from financial_dashboard.context.envelope import FactRef
from financial_dashboard.context.lineage import build_lineage_groups, unknown_lineage_refs
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
from financial_dashboard.decision.structural import (
    DecisionHorizon,
    StructuralDirection,
    ThesisState,
)
from financial_dashboard.decision.timeline_cache import load_frozen_decision_timeline


_TRANSITION_UP = frozenset({"TRANSITION_UP", "STATE_TRANSITION_UP"})
_TRANSITION_DOWN = frozenset({"TRANSITION_DOWN", "STATE_TRANSITION_DOWN"})
_EVIDENCE_TIMEFRAMES = ("1h", "2h", "30m")
_REACTION_TIMEFRAMES = ("1h", "30m")


@dataclass(frozen=True, slots=True)
class TransitionView:
    kind: str
    incumbent: StructuralDirection
    challenger: StructuralDirection


@dataclass(frozen=True, slots=True)
class EpisodeAccumulator:
    episode_id: int
    kind: str
    incumbent: str
    challenger: str
    start_index: int
    start_as_of: str


def _value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _transition_view(structural: Any) -> TransitionView | None:
    if structural.thesis_state is not ThesisState.TRANSITIONING:
        return None
    native_state = str(structural.native_state or "").strip().upper()
    if (
        structural.direction is StructuralDirection.SHORT
        and structural.transition_target is StructuralDirection.LONG
        and native_state in _TRANSITION_UP
    ):
        return TransitionView("UP", StructuralDirection.SHORT, StructuralDirection.LONG)
    if (
        structural.direction is StructuralDirection.LONG
        and structural.transition_target is StructuralDirection.SHORT
        and native_state in _TRANSITION_DOWN
    ):
        return TransitionView("DOWN", StructuralDirection.LONG, StructuralDirection.SHORT)
    return None


def _ref_payload(ref: FactRef, *, as_of: Any) -> dict[str, Any]:
    return {
        "domain": ref.domain.value,
        "fact_type": ref.fact_type,
        "timeframe": ref.timeframe,
        "native_id": ref.native_id,
        "native_state": ref.native_state,
        "origin_time": str(ref.origin_time),
        "confirmed_at": None if ref.confirmed_at is None else str(ref.confirmed_at),
        "available_at": str(ref.available_at),
        "available_now": bool(ref.is_available_at(as_of)),
        "lineage_id": ref.lineage_id,
        "causal_family": ref.causal_family.value,
        "source_family": ref.source_family.value,
        "data_quality": ref.data_quality.value,
    }


def _unique_refs(refs: Iterable[FactRef]) -> tuple[FactRef, ...]:
    by_key = {ref.deterministic_key: ref for ref in refs}
    return tuple(sorted(by_key.values(), key=lambda ref: ref.deterministic_key))


def _safe_for_timeframe(projection: Any, timeframe: str) -> Any | None:
    if projection is None:
        return None
    lookup = getattr(projection, "for_timeframe", None)
    if lookup is None:
        return None
    try:
        return lookup(timeframe)
    except (KeyError, AttributeError, TypeError):
        return None


def _structural_payload(snapshot: Any, prepared: Any) -> tuple[dict[str, Any], tuple[FactRef, ...]]:
    market_state = snapshot.market_state.short_term
    nodes: list[dict[str, Any]] = []
    refs: list[FactRef] = list(prepared.structural.source_refs)
    for node in market_state.structural_map.nodes:
        node_refs = tuple(node.current_external_refs)
        refs.extend(node_refs)
        nodes.append(
            {
                "timeframe": node.timeframe,
                "role": node.role.value,
                "data_quality": node.data_quality.value,
                "external_state": node.external_state,
                "external_direction": node.external_direction,
                "internal_state": node.internal_state,
                "internal_direction": node.internal_direction,
                "current_external_refs": [
                    _ref_payload(ref, as_of=snapshot.as_of) for ref in node_refs
                ],
            }
        )
    return (
        {
            "direction": prepared.structural.direction.value,
            "thesis_state": prepared.structural.thesis_state.value,
            "native_state": prepared.structural.native_state,
            "transition_target": _value(prepared.structural.transition_target),
            "bridge_state": market_state.structural_map.bridge_state.value,
            "structural_regime": market_state.structural_map.structural_regime.value,
            "participation_propagation": market_state.participation_propagation.value,
            "nodes": nodes,
        },
        _unique_refs(refs),
    )


def _participation_payload(
    snapshot: Any,
    *,
    incumbent: StructuralDirection,
    challenger: StructuralDirection,
) -> tuple[dict[str, Any], tuple[FactRef, ...]]:
    rows: dict[str, Any] = {}
    refs: list[FactRef] = []
    for timeframe in ("1h", "2h", "4h"):
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
        refs.extend(incumbent_read.source_refs)
        refs.extend(challenger_read.source_refs)
        native_row = _safe_for_timeframe(snapshot.participation_behavior, timeframe)
        rows[timeframe] = {
            "incumbent_state": incumbent_read.state.value,
            "incumbent_reasons": incumbent_read.reasons,
            "challenger_state": challenger_read.state.value,
            "challenger_reasons": challenger_read.reasons,
            "native": None
            if native_row is None
            else {
                "participation_trend": native_row.participation_trend.value,
                "effort_result": native_row.effort_result.value,
                "absorption": native_row.absorption.value,
                "break_participation": native_row.break_participation.value,
                "participation_direction": native_row.participation_direction,
                "break_direction": native_row.break_direction,
                "controlled_pullback": native_row.controlled_pullback,
                "controlled_reaction": native_row.controlled_reaction,
                "heavy_conflict": native_row.heavy_conflict,
                "ref": _ref_payload(native_row.ref, as_of=snapshot.as_of),
            },
        }
    return rows, _unique_refs(refs)


def _pattern_payload(snapshot: Any) -> tuple[dict[str, Any], tuple[FactRef, ...]]:
    row = _execution_pattern_row(snapshot, "30m")
    if row is None:
        return {"available": False}, ()
    phase = _execution_effective_phase(row)
    side = _execution_pattern_direction(row)
    ref = getattr(row, "ref", None)
    refs = () if ref is None else (ref,)
    return (
        {
            "available": True,
            "projected_phase": _value(getattr(row, "phase", None)),
            "effective_phase": _value(phase),
            "direction": _value(side),
            "classic_direction": getattr(row, "classic_direction", None),
            "native_state": getattr(row, "native_state", None),
            "break_state_code": getattr(row, "break_state_code", None),
            "break_level": getattr(row, "break_level", None),
            "retest_state_code": getattr(row, "retest_state_code", None),
            "production_recovery_used": bool(
                phase is not None
                and getattr(row, "phase", None) is PatternBehaviorPhase.UNAVAILABLE
                and str(getattr(row, "native_state", "") or "").strip()
            ),
            "ref": None if ref is None else _ref_payload(ref, as_of=snapshot.as_of),
        },
        refs,
    )


def _support_resistance_payload(snapshot: Any) -> tuple[dict[str, Any], tuple[FactRef, ...]]:
    rows: dict[str, Any] = {}
    refs: list[FactRef] = []
    for timeframe in _EVIDENCE_TIMEFRAMES:
        row = _safe_for_timeframe(snapshot.support_resistance, timeframe)
        if row is None:
            continue
        refs.append(row.ref)
        rows[timeframe] = {
            "state": row.state,
            "break_direction": row.break_direction,
            "break_boundary": row.break_boundary,
            "price_location": row.price_location,
            "role_reversal_support": (
                row.role_reversal_support_low,
                row.role_reversal_support_high,
            ),
            "role_reversal_resistance": (
                row.role_reversal_resistance_low,
                row.role_reversal_resistance_high,
            ),
            "ref": _ref_payload(row.ref, as_of=snapshot.as_of),
        }
    return rows, _unique_refs(refs)


def _liquidity_payload(snapshot: Any) -> tuple[dict[str, Any], tuple[FactRef, ...]]:
    rows: dict[str, Any] = {}
    refs: list[FactRef] = []
    projection = snapshot.liquidity
    if projection is None:
        return rows, ()
    for timeframe in _EVIDENCE_TIMEFRAMES:
        items = projection.behavior_for_timeframe(timeframe)
        payload: list[dict[str, Any]] = []
        for item in items:
            refs.append(item.ref)
            payload.append(
                {
                    "pool_identity": item.pool_identity,
                    "side": item.side,
                    "maturity": item.maturity,
                    "relation": item.relation,
                    "removal": item.removal,
                    "distance_atr": item.distance_atr,
                    "ref": _ref_payload(item.ref, as_of=snapshot.as_of),
                }
            )
        if payload:
            rows[timeframe] = payload
    return rows, _unique_refs(refs)


def _zone_payload(snapshot: Any) -> tuple[dict[str, Any], tuple[FactRef, ...]]:
    payload: dict[str, Any] = {"order_blocks": [], "fvg": [], "engulfing": []}
    refs: list[FactRef] = []
    if snapshot.order_block_behavior is not None:
        for item in snapshot.order_block_behavior.observations:
            if item.timeframe not in _REACTION_TIMEFRAMES:
                continue
            refs.append(item.ref)
            payload["order_blocks"].append(
                {
                    "timeframe": item.timeframe,
                    "identity": item.identity,
                    "direction": "LONG" if item.bullish else "SHORT",
                    "state": item.state,
                    "interaction": item.interaction,
                    "active": item.active,
                    "terminal_reason": item.terminal_reason,
                    "ref": _ref_payload(item.ref, as_of=snapshot.as_of),
                }
            )
    lifecycle = snapshot.fvg_engulfing_lifecycle
    if lifecycle is not None:
        for item in lifecycle.fvg:
            if item.ref.timeframe not in _REACTION_TIMEFRAMES:
                continue
            refs.append(item.ref)
            payload["fvg"].append(
                {
                    "timeframe": item.ref.timeframe,
                    "identity": item.identity,
                    "direction": item.direction,
                    "state": item.state,
                    "reaction_confirmed": item.reaction_confirmed,
                    "failed_reaction": item.failed_reaction,
                    "full_fill": item.full_fill,
                    "invalid": item.invalid,
                    "ref": _ref_payload(item.ref, as_of=snapshot.as_of),
                }
            )
        for item in lifecycle.engulfing:
            if item.ref.timeframe not in _REACTION_TIMEFRAMES:
                continue
            refs.append(item.ref)
            payload["engulfing"].append(
                {
                    "timeframe": item.ref.timeframe,
                    "identity": item.identity,
                    "direction": item.direction,
                    "state": item.state,
                    "continuation_confirmed": item.continuation_confirmed,
                    "weakened": item.weakened,
                    "invalid": item.invalid,
                    "ref": _ref_payload(item.ref, as_of=snapshot.as_of),
                }
            )
    return payload, _unique_refs(refs)


def _reaction_payload(
    snapshot: Any,
    *,
    incumbent: StructuralDirection,
    challenger: StructuralDirection,
) -> tuple[dict[str, Any], tuple[FactRef, ...]]:
    payload: dict[str, Any] = {}
    refs: list[FactRef] = []
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
        refs.extend(incumbent_read.source_refs)
        refs.extend(challenger_read.source_refs)
        payload[timeframe] = {
            "incumbent_state": incumbent_read.state.value,
            "incumbent_reasons": incumbent_read.reasons,
            "challenger_state": challenger_read.state.value,
            "challenger_reasons": challenger_read.reasons,
        }
    return payload, _unique_refs(refs)


def _break_relations_payload(snapshot: Any) -> dict[str, Any]:
    view = snapshot.break_relations
    return {
        "evidence": [
            {
                "domain": item.domain.value,
                "timeframe": item.timeframe,
                "direction": item.direction,
                "level": item.level,
                "state": item.state,
                "lineage_id": item.ref.lineage_id,
            }
            for item in view.evidence
        ],
        "relations": [
            {
                "left": f"{item.left.domain.value}:{item.left.timeframe}:{item.left.ref.native_id}",
                "right": f"{item.right.domain.value}:{item.right.timeframe}:{item.right.ref.native_id}",
                "relation": item.relation.value,
                "independently_countable": item.independently_countable,
                "reasons": item.reasons,
            }
            for item in view.relations
        ],
    }


def _lineage_payload(refs: Iterable[FactRef], *, as_of: Any) -> dict[str, Any]:
    unique = _unique_refs(refs)
    groups = build_lineage_groups(unique)
    unknown = unknown_lineage_refs(unique)
    duplicate_groups = [group for group in groups if len(group.members) > 1]
    return {
        "ref_count": len(unique),
        "known_lineage_ref_count": len(unique) - len(unknown),
        "unknown_lineage_ref_count": len(unknown),
        "known_lineage_group_count": len(groups),
        "duplicate_known_lineage_group_count": len(duplicate_groups),
        "unknown_refs": [_ref_payload(ref, as_of=as_of) for ref in unknown],
        "duplicate_known_groups": [
            {
                "lineage_id": group.lineage_id,
                "causal_family": group.causal_family.value,
                "members": [_ref_payload(ref, as_of=as_of) for ref in group.members],
            }
            for group in duplicate_groups
        ],
    }


def _episode_outcome(
    snapshots: list[Any],
    *,
    end_index: int,
    view: TransitionView,
    engine_config: DecisionEngineConfig,
) -> str:
    next_index = end_index + 1
    if next_index >= len(snapshots):
        return "RIGHT_CENSORED"
    next_prepared = prepare_horizon_assessment(
        snapshots[next_index],
        DecisionHorizon.SHORT_TERM,
        config=engine_config,
    )
    structural = next_prepared.structural
    if structural.direction is view.challenger and structural.thesis_state is ThesisState.INTACT:
        return "TARGET_SIDE_ESTABLISHED"
    if structural.direction is view.incumbent and structural.thesis_state is ThesisState.INTACT:
        return "INCUMBENT_REGAINED"
    return "OTHER_OR_UNRESOLVED"


def _episode_return(snapshots: list[Any], start_index: int, end_index: int) -> float | None:
    if start_index < 0 or end_index >= len(snapshots) or end_index <= start_index:
        return None
    start = float(snapshots[start_index].current_price)
    end = float(snapshots[end_index].current_price)
    if start <= 0:
        return None
    return (end / start - 1.0) * 100.0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnostic-only ST control-transfer evidence inventory. It studies causal native "
            "evidence during symmetric TRANSITION_UP / TRANSITION_DOWN episodes without "
            "creating a control state or changing trading policy."
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

    transition_rows: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    active: EpisodeAccumulator | None = None
    active_view: TransitionView | None = None
    episode_id = 0
    episode_row_indices: list[int] = []

    evidence_presence = Counter()
    lineage_totals = Counter()
    relation_counts = Counter()

    def close_episode(end_index: int) -> None:
        nonlocal active, active_view, episode_row_indices
        if active is None or active_view is None:
            return
        rows = [transition_rows[index] for index in episode_row_indices]
        first = rows[0]
        last = rows[-1]
        episodes.append(
            {
                "episode_id": active.episode_id,
                "kind": active.kind,
                "incumbent": active.incumbent,
                "challenger": active.challenger,
                "start_as_of": active.start_as_of,
                "end_as_of": last["as_of"],
                "bars": len(rows),
                "outcome": _episode_outcome(
                    snapshots,
                    end_index=end_index,
                    view=active_view,
                    engine_config=engine_config,
                ),
                "episode_return_pct": _episode_return(
                    snapshots,
                    active.start_index,
                    end_index,
                ),
                "first_bridge_state": first["structure"]["bridge_state"],
                "last_bridge_state": last["structure"]["bridge_state"],
                "fresh_entry_events_inside": sum(row["fresh_entry_event"] for row in rows),
                "fresh_exit_events_inside": sum(row["fresh_exit_event"] for row in rows),
                "max_unknown_lineage_refs": max(
                    row["lineage"]["unknown_lineage_ref_count"] for row in rows
                ),
                "first_row_index": episode_row_indices[0],
                "last_row_index": episode_row_indices[-1],
            }
        )
        active = None
        active_view = None
        episode_row_indices = []

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

        if active is None or active.kind != view.kind:
            if active is not None:
                close_episode(index - 1)
            episode_id += 1
            active = EpisodeAccumulator(
                episode_id=episode_id,
                kind=view.kind,
                incumbent=view.incumbent.value,
                challenger=view.challenger.value,
                start_index=index,
                start_as_of=str(snapshot.as_of),
            )
            active_view = view

        structure_payload, structure_refs = _structural_payload(snapshot, prepared)
        participation_payload, participation_refs = _participation_payload(
            snapshot,
            incumbent=view.incumbent,
            challenger=view.challenger,
        )
        pattern_payload, pattern_refs = _pattern_payload(snapshot)
        sr_payload, sr_refs = _support_resistance_payload(snapshot)
        liquidity_payload, liquidity_refs = _liquidity_payload(snapshot)
        zone_payload, zone_refs = _zone_payload(snapshot)
        reaction_payload, _reaction_refs = _reaction_payload(
            snapshot,
            incumbent=view.incumbent,
            challenger=view.challenger,
        )
        break_payload = _break_relations_payload(snapshot)

        native_refs = _unique_refs(
            (
                *structure_refs,
                *participation_refs,
                *pattern_refs,
                *sr_refs,
                *liquidity_refs,
                *zone_refs,
            )
        )
        lineage_payload = _lineage_payload(native_refs, as_of=snapshot.as_of)

        for relation in break_payload["relations"]:
            relation_counts[relation["relation"]] += 1
        lineage_totals["refs"] += lineage_payload["ref_count"]
        lineage_totals["unknown"] += lineage_payload["unknown_lineage_ref_count"]
        lineage_totals["duplicate_groups"] += lineage_payload[
            "duplicate_known_lineage_group_count"
        ]

        if pattern_payload.get("available"):
            evidence_presence[f"{view.kind}:PATTERN_30M"] += 1
        if sr_payload:
            evidence_presence[f"{view.kind}:SUPPORT_RESISTANCE"] += 1
        if liquidity_payload:
            evidence_presence[f"{view.kind}:LIQUIDITY"] += 1
        if any(zone_payload.values()):
            evidence_presence[f"{view.kind}:ZONES"] += 1
        if participation_payload:
            evidence_presence[f"{view.kind}:PARTICIPATION"] += 1

        row = {
            "episode_id": active.episode_id,
            "snapshot_index": index,
            "as_of": str(snapshot.as_of),
            "price": float(snapshot.current_price),
            "transition_kind": view.kind,
            "incumbent": view.incumbent.value,
            "challenger": view.challenger.value,
            "structure": structure_payload,
            "participation": participation_payload,
            "pattern_30m": pattern_payload,
            "support_resistance": sr_payload,
            "liquidity": liquidity_payload,
            "zones": zone_payload,
            "reaction_read_model": reaction_payload,
            "break_relations": break_payload,
            "lineage": lineage_payload,
            "fresh_entry_event": snapshot.as_of in entry_events,
            "fresh_exit_event": snapshot.as_of in exit_events,
            "execution_note": "Fresh execution is reported for overlap only and is not control evidence.",
        }
        transition_rows.append(row)
        episode_row_indices.append(len(transition_rows) - 1)

    if active is not None:
        close_episode(len(snapshots) - 1)

    by_kind = Counter(row["transition_kind"] for row in transition_rows)
    episode_by_kind = Counter(item["kind"] for item in episodes)
    outcome_by_kind = Counter(f"{item['kind']}:{item['outcome']}" for item in episodes)

    report = {
        "symbol": symbol,
        "snapshots": len(snapshots),
        "frozen_cache_status": frozen.cache_status,
        "entry_execution_events": len(entry_events),
        "exit_execution_events": len(exit_events),
        "transition_snapshots": dict(sorted(by_kind.items())),
        "transition_episodes": dict(sorted(episode_by_kind.items())),
        "episode_outcomes": dict(sorted(outcome_by_kind.items())),
        "evidence_presence": dict(sorted(evidence_presence.items())),
        "lineage_totals": dict(lineage_totals),
        "break_relation_counts": dict(sorted(relation_counts.items())),
        "episodes": episodes,
        "transition_rows": transition_rows,
        "invariants": {
            "trading_policy_mutation": "NONE",
            "structure_mutation": "NONE",
            "qualification_mutation": "NONE",
            "control_state_created": False,
            "context_permission_counted_as_native_evidence": False,
            "opportunity_timing_counted_as_control_evidence": False,
            "execution_counted_as_control_evidence": False,
            "unknown_lineage_counted_as_independent": False,
            "pattern_phase_semantics": "PRODUCTION_EXECUTION_EFFECTIVE_PHASE",
        },
    }

    print("=" * 104)
    print("ST CONTROL EVIDENCE / TRANSITION EPISODE DIAGNOSTIC — PHASE A")
    print("=" * 104)
    print(f"SYMBOL\t{symbol}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print(f"FROZEN_CACHE_STATUS\t{frozen.cache_status}")
    print(f"ENTRY_EXECUTION_EVENTS\t{len(entry_events)}")
    print(f"EXIT_EXECUTION_EVENTS\t{len(exit_events)}")
    print(f"TRANSITION_UP_SNAPSHOTS\t{by_kind.get('UP', 0)}")
    print(f"TRANSITION_DOWN_SNAPSHOTS\t{by_kind.get('DOWN', 0)}")
    print(f"TRANSITION_UP_EPISODES\t{episode_by_kind.get('UP', 0)}")
    print(f"TRANSITION_DOWN_EPISODES\t{episode_by_kind.get('DOWN', 0)}")
    print("TRADING_POLICY_MUTATION\tNONE")
    print("STRUCTURE_MUTATION\tNONE")
    print("QUALIFICATION_MUTATION\tNONE")
    print("CONTROL_STATE_CREATED\tNO")
    print("UNKNOWN_LINEAGE_INDEPENDENCE\tFAIL_CLOSED")
    print("PATTERN_PARITY\tPRODUCTION_EXECUTION_EFFECTIVE_PHASE")

    print("\nEPISODE OUTCOMES")
    print("----------------")
    for key, count in sorted(outcome_by_kind.items()):
        print(f"{key:<40} {count:>5}")

    print("\nNATIVE EVIDENCE AVAILABILITY DURING TRANSITIONS")
    print("-----------------------------------------------")
    for key, count in sorted(evidence_presence.items()):
        print(f"{key:<40} {count:>5}")

    print("\nLINEAGE COVERAGE")
    print("----------------")
    print(f"NATIVE_REFS_OBSERVED\t{lineage_totals.get('refs', 0)}")
    print(f"UNKNOWN_LINEAGE_REFS\t{lineage_totals.get('unknown', 0)}")
    print(f"DUPLICATE_KNOWN_LINEAGE_GROUPS\t{lineage_totals.get('duplicate_groups', 0)}")
    print("NOTE\tUnknown lineage is never promoted to independent confirmation")

    print("\nBREAK RELATION SUMMARY")
    print("----------------------")
    for key, count in sorted(relation_counts.items()):
        print(f"{key:<24} {count:>5}")

    print("\nTRANSITION EPISODE DETAIL")
    print("-------------------------")
    for item in episodes:
        ret = item["episode_return_pct"]
        ret_text = "n/a" if ret is None else f"{ret:.3f}%"
        print(
            f"EP {item['episode_id']:02d} {item['kind']} | {item['start_as_of']} -> "
            f"{item['end_as_of']} | bars={item['bars']} | outcome={item['outcome']} | "
            f"ret={ret_text} | bridge={item['first_bridge_state']}->{item['last_bridge_state']} | "
            f"fresh_entry={item['fresh_entry_events_inside']} fresh_exit={item['fresh_exit_events_inside']} | "
            f"max_unknown_lineage={item['max_unknown_lineage_refs']}"
        )

    print("\nDIAGNOSTIC BOUNDARY")
    print("-------------------")
    print("No control state, score, vote, BUY/SELL action or gate is produced.")
    print("Context/Permission, Opportunity, Timing and Execution are not native control evidence.")
    print("Reaction is reported only as a derived side-relative read model over OB/FVG/Engulfing refs.")
    print("Pattern uses the same causal DATA_LIMITED native-state recovery as production execution.")

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"JSON_REPORT\t{args.json_out}")

    print("ST_CONTROL_EVIDENCE_DIAGNOSTIC_OK")


if __name__ == "__main__":
    main()
