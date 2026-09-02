from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from time import perf_counter

from entry_reason_profile import _calibration, _causal_warmup_start
from financial_dashboard.context.axes import evaluate_context_axes
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.arbiter import assess_entry_arbitration
from financial_dashboard.decision.engine import (
    DecisionEngineConfig,
    _decision_structure_projection,
    _permission_policy,
)
from financial_dashboard.decision.entry import assess_entry_decision
from financial_dashboard.decision.entry_bottleneck_audit import (
    attribute_entry_bottlenecks,
    diagnostic_episode_key,
)
from financial_dashboard.decision.execution_detect import detect_30m_execution_events
from financial_dashboard.decision.history_replay import HistoricalDecisionInputReplayRunner
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.persistent_state import PersistentObjectStore
from financial_dashboard.decision.scenario import (
    ScenarioPresence,
    ScenarioStage,
    prepare_entry_scenario,
)
from financial_dashboard.decision.structural import DecisionHorizon
from financial_dashboard.decision.timeline_cache import load_frozen_decision_timeline


def _print_counter(title: str, counter: Counter[str], *, top: int | None = None) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    if not counter:
        print("None.")
        return
    rows = counter.most_common(top)
    width = max(len(key) for key, _ in rows)
    for key, count in rows:
        print(f"{key:<{width}}  {count:6d}")


def _value(value) -> str:
    return getattr(value, "value", str(value))


def _refs(values) -> list[dict[str, str | None]]:
    rows: list[dict[str, str | None]] = []
    for ref in values:
        rows.append(
            {
                "domain": _value(getattr(ref, "domain", None)),
                "timeframe": str(getattr(ref, "timeframe", "")),
                "native_id": str(getattr(ref, "native_id", "")),
                "native_state": str(getattr(ref, "native_state", "")),
                "available_at": str(getattr(ref, "available_at", "")),
                "lineage_id": getattr(ref, "lineage_id", None),
                "data_quality": _value(getattr(ref, "data_quality", None)),
            }
        )
    return rows


def _st_permission_axes(snapshot):
    anchor_timeframe, trigger_timeframes = _permission_policy(DecisionHorizon.SHORT_TERM)
    return evaluate_context_axes(
        structural=_decision_structure_projection(snapshot.structure),
        zones=snapshot.qualified_zones,
        anchor_timeframe=anchor_timeframe,
        liquidity=snapshot.liquidity,
        participation=snapshot.participation,
        pattern=snapshot.pattern,
        volatility=snapshot.volatility,
        ham=snapshot.ham,
        trigger_timeframes=trigger_timeframes,
    )


def _event_detail(
    snapshot,
    *,
    event,
    prepared,
    attribution,
    arbitration,
    entry,
) -> dict[str, object]:
    scenario = prepared.scenario
    assessment = prepared.assessment
    axes = _st_permission_axes(snapshot)
    target_path = snapshot.target_path(assessment.structural.direction)
    active = target_path.active_node
    return {
        "as_of": str(snapshot.as_of),
        "event": {
            "state": _value(event.state),
            "side": _value(event.side),
            "timeframe": event.timeframe,
            "observed_at": str(event.observed_at),
            "available_at": str(event.available_at),
            "reason": event.reason,
            "source_refs": _refs(event.source_refs),
        },
        "st_scenario": {
            "presence": _value(scenario.presence),
            "stage": _value(scenario.stage),
            "kind": _value(scenario.kind),
            "reasons": list(scenario.reasons),
            "blockers": list(scenario.blockers),
            "waiting_for": list(scenario.waiting_for),
            "active_target_identity": scenario.active_target_identity,
            "bottleneck_families": [item.value for item in attribution.families],
            "bottleneck_tokens": list(attribution.tokens),
        },
        "structure": {
            "direction": _value(assessment.structural.direction),
            "thesis_state": _value(assessment.structural.thesis_state),
            "native_state": assessment.structural.native_state,
            "reasons": list(assessment.structural.reasons),
            "source_refs": _refs(assessment.structural.source_refs),
        },
        "permission": {
            "scope": _value(assessment.permission.scope),
            "permitted_side": _value(assessment.permission.permitted_side),
            "gate_state": _value(assessment.permission.gate_state),
            "allowed_reasons": list(assessment.permission.allowed_reasons),
            "blocking_reasons": list(assessment.permission.blocking_reasons),
            "waiting_for": list(assessment.permission.waiting_for),
            "source_refs": list(assessment.permission.source_refs),
            "axes": {
                "structural_thesis": _value(axes.structural_thesis),
                "structural_direction": _value(axes.structural_direction),
                "continuation": _value(axes.continuation),
                "reaction": _value(axes.reaction),
                "reaction_direction": _value(axes.reaction_direction),
                "reversal": _value(axes.reversal),
                "reversal_direction": _value(axes.reversal_direction),
                "participation": _value(axes.participation),
                "volatility": _value(axes.volatility),
                "pattern_readiness": _value(axes.pattern_readiness),
                "mtf": _value(axes.mtf),
                "ham_readiness": _value(axes.ham_readiness),
                "conflict": _value(axes.conflict),
                "reasons": [
                    {
                        "code": reason.code,
                        "detail": reason.detail,
                        "source_refs": list(reason.source_refs),
                    }
                    for reason in axes.reasons
                ],
            },
        },
        "timing": {
            "state": _value(assessment.timing.state),
            "setup_trigger_state": _value(assessment.timing.setup_trigger.state),
            "reasons": list(assessment.timing.reasons),
            "setup_reasons": list(assessment.timing.setup_trigger.reasons),
            "waiting_for": list(assessment.timing.waiting_for),
            "source_refs": _refs(assessment.timing.source_refs),
        },
        "opportunity": {
            "state": _value(assessment.opportunity.state),
            "room_atr": assessment.opportunity.room_atr,
            "target_identity": assessment.opportunity.target_identity,
            "target_quality": assessment.opportunity.target_quality,
            "reasons": list(assessment.opportunity.reasons),
            "source_lineage": list(assessment.opportunity.source_lineage),
        },
        "conflict": {
            "state": _value(assessment.conflict.state),
            "reasons": list(assessment.conflict.reasons),
            "families": [
                {
                    "family": item.family,
                    "severity": _value(item.severity),
                    "reasons": list(item.reasons),
                    "lineage_ids": list(item.lineage_ids),
                }
                for item in assessment.conflict.families
            ],
        },
        "target_path": {
            "status": _value(target_path.status),
            "reasons": list(target_path.reasons),
            "active_node": None
            if active is None
            else {
                "identity": active.identity,
                "state": _value(active.state),
                "distance_atr": active.distance_atr,
                "roles": [_value(item) for item in active.roles],
                "sources": [_value(item) for item in active.sources],
                "timeframes": list(active.timeframes),
                "lineage_ids": list(active.lineage_ids),
            },
        },
        "arbitration": {
            "state": _value(arbitration.state),
            "selection": _value(arbitration.selection),
            "selected_horizon": None
            if arbitration.selected_horizon is None
            else _value(arbitration.selected_horizon),
            "reasons": list(arbitration.reasons),
            "waiting_for": list(arbitration.waiting_for),
        },
        "entry": {
            "action": _value(entry.action),
            "selected_horizon": None
            if entry.selected_horizon is None
            else _value(entry.selected_horizon),
            "scenario_stage": None
            if entry.scenario_stage is None
            else _value(entry.scenario_stage),
            "execution_state": None
            if entry.execution_state is None
            else _value(entry.execution_state),
            "execution_event_consumed": bool(entry.execution_event_consumed),
            "reasons": list(entry.reasons),
            "blockers": list(entry.blockers),
            "waiting_for": list(entry.waiting_for),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnostic-only ST entry bottleneck attribution from an exact frozen "
            "DecisionInput timeline. Trading policy is never modified."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
    parser.add_argument("--top", type=int, default=30)
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
    config = HistoricalDecisionInputConfig(
        pattern_profile=args.pattern_profile,
        max_bars=args.max_bars,
        start_at=effective_start,
        end_at=args.end,
    )
    opportunity_calibration, calibration_source = _calibration(
        args,
        cache_root=args.cache_root,
        symbol=clean_symbol,
    )
    engine_config = DecisionEngineConfig(opportunity_calibration=opportunity_calibration)

    runner = HistoricalDecisionInputReplayRunner(store)
    identity = runner._cache_identity(symbol=clean_symbol, config=config)
    cache_path = PersistentObjectStore(store.root).path_for(identity)
    cache_mb = cache_path.stat().st_size / (1024.0 * 1024.0) if cache_path.exists() else 0.0

    started = perf_counter()
    frozen = load_frozen_decision_timeline(store, clean_symbol, config=config)
    load_seconds = perf_counter() - started
    snapshots = frozen.replay.snapshots
    entry_execution_events, exit_execution_events = detect_30m_execution_events(snapshots)

    stage_counts: Counter[str] = Counter()
    gate_sets: Counter[str] = Counter()
    single_family: Counter[str] = Counter()
    token_counts: Counter[str] = Counter()
    event_gate_sets: Counter[str] = Counter()
    event_outcomes: Counter[str] = Counter()
    event_non_present_reasons: Counter[str] = Counter()
    arbiter_when_event: Counter[str] = Counter()
    fresh_event_details: list[dict[str, object]] = []

    episode_counts: Counter[str] = Counter()
    current_episode_key: tuple[str, str] | None = None
    episode_had_early_event = False
    episode_later_qualified_without_event = False

    def close_episode() -> None:
        nonlocal episode_had_early_event, episode_later_qualified_without_event
        if current_episode_key is None:
            return
        episode_counts["EPISODES_WITH_TARGET_CONTEXT"] += 1
        if episode_had_early_event:
            episode_counts["EARLY_EVENT_BEFORE_QUALIFICATION"] += 1
        if episode_later_qualified_without_event:
            episode_counts["LATER_QUALIFIED_WITHOUT_NEW_EVENT"] += 1
        if episode_had_early_event and episode_later_qualified_without_event:
            episode_counts["POTENTIAL_MOVING_GOALPOST_STARVATION"] += 1
        episode_had_early_event = False
        episode_later_qualified_without_event = False

    decision_started = perf_counter()
    st_present = 0
    st_qualified = 0
    for snapshot in snapshots:
        prepared = prepare_entry_scenario(
            snapshot,
            DecisionHorizon.SHORT_TERM,
            config=engine_config,
        )
        scenario = prepared.scenario
        event = entry_execution_events.get(snapshot.as_of)

        if scenario.presence is ScenarioPresence.PRESENT:
            st_present += 1
            stage_counts[_value(scenario.stage)] += 1
            attribution = attribute_entry_bottlenecks(scenario)
            if scenario.stage is ScenarioStage.QUALIFIED:
                st_qualified += 1
                gate_sets["NONE"] += 1
            else:
                gate_sets[attribution.label] += 1
                if attribution.is_single_family:
                    single_family[attribution.label] += 1
                for token in attribution.tokens:
                    token_counts[token] += 1

            key = diagnostic_episode_key(scenario)
            if key != current_episode_key:
                close_episode()
                current_episode_key = key

            if event is not None and scenario.stage is not ScenarioStage.QUALIFIED:
                episode_had_early_event = True
            if (
                episode_had_early_event
                and scenario.stage is ScenarioStage.QUALIFIED
                and event is None
            ):
                episode_later_qualified_without_event = True
        else:
            if current_episode_key is not None:
                close_episode()
                current_episode_key = None

        if event is None:
            continue

        attribution = attribute_entry_bottlenecks(scenario)
        arbitration = assess_entry_arbitration(snapshot, config=engine_config)
        arbiter_when_event[_value(arbitration.selection)] += 1
        entry = assess_entry_decision(
            snapshot,
            config=engine_config,
            execution_event=event,
        )
        if bool(entry.execution_event_consumed):
            event_outcomes["EVENT_CONSUMED"] += 1
            selected = (
                "UNRESOLVED"
                if entry.selected_horizon is None
                else _value(entry.selected_horizon)
            )
            event_outcomes[f"EVENT_CONSUMED_BY:{selected}"] += 1
        else:
            event_outcomes["EVENT_NOT_CONSUMED"] += 1
            event_outcomes[f"NOT_CONSUMED_ENTRY_ACTION:{_value(entry.action)}"] += 1

        if scenario.presence is not ScenarioPresence.PRESENT:
            event_outcomes["EVENT_WHILE_ST_NOT_PRESENT"] += 1
            event_non_present_reasons.update(
                scenario.reasons
                or scenario.presence_waiting_for
                or ("UNCLASSIFIED_ST_NON_PRESENCE",)
            )
        elif scenario.stage is ScenarioStage.QUALIFIED:
            event_outcomes["EVENT_WHILE_ST_QUALIFIED"] += 1
        else:
            event_outcomes["EVENT_WHILE_ST_NOT_QUALIFIED"] += 1
            event_gate_sets[attribution.label] += 1

        fresh_event_details.append(
            _event_detail(
                snapshot,
                event=event,
                prepared=prepared,
                attribution=attribution,
                arbitration=arbitration,
                entry=entry,
            )
        )

    close_episode()
    decision_seconds = perf_counter() - decision_started

    print("=" * 76)
    print("ST ENTRY BOTTLENECK / FRESH-EVENT AUDIT")
    print("=" * 76)
    print(f"SYMBOL\t{clean_symbol}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print(f"ST_PRESENT\t{st_present}")
    print(f"ST_QUALIFIED\t{st_qualified}")
    print(f"FROZEN_CACHE_STATUS\t{frozen.cache_status}")
    print(f"FROZEN_CACHE_FILE_MB\t{cache_mb:.3f}")
    print(f"OPPORTUNITY_CALIBRATION\t{calibration_source}")
    print(f"ENTRY_EXECUTION_EVENTS\t{len(entry_execution_events)}")
    print(f"EXIT_EXECUTION_EVENTS\t{len(exit_execution_events)}")
    print(f"FROZEN_TIMELINE_LOAD_SECONDS\t{load_seconds:.3f}")
    print(f"AUDIT_SECONDS\t{decision_seconds:.3f}")
    print("DOMAIN_REPLAY_SECONDS\t0.000")
    print("TRADING_POLICY_MUTATION\tNONE")

    _print_counter("ST PRESENT STAGE", stage_counts)
    _print_counter("UNSATISFIED GATE FAMILY SETS", gate_sets, top=args.top)
    _print_counter("SINGLE-FAMILY BOTTLENECKS", single_family, top=args.top)
    _print_counter("CANONICAL WAIT/BLOCK TOKENS", token_counts, top=args.top)
    _print_counter("FRESH ENTRY EVENT OUTCOMES", event_outcomes, top=args.top)
    _print_counter("FRESH EVENT ST NON-PRESENCE REASONS", event_non_present_reasons, top=args.top)
    _print_counter("FRESH EVENT GATE SET WHEN NOT QUALIFIED", event_gate_sets, top=args.top)
    _print_counter("ARBITER SELECTION WHEN FRESH EVENT EXISTS", arbiter_when_event, top=args.top)
    _print_counter("TARGET-CONTEXT EPISODE PROXY", episode_counts, top=args.top)

    if args.json_out is not None:
        payload = {
            "symbol": clean_symbol,
            "snapshots": len(snapshots),
            "st_present": st_present,
            "st_qualified": st_qualified,
            "frozen_cache_status": frozen.cache_status,
            "opportunity_calibration": calibration_source,
            "entry_execution_events": len(entry_execution_events),
            "exit_execution_events": len(exit_execution_events),
            "stage_counts": dict(stage_counts),
            "gate_family_sets": dict(gate_sets),
            "single_family_bottlenecks": dict(single_family),
            "canonical_tokens": dict(token_counts),
            "fresh_event_outcomes": dict(event_outcomes),
            "fresh_event_st_non_presence_reasons": dict(event_non_present_reasons),
            "fresh_event_gate_sets": dict(event_gate_sets),
            "fresh_event_details": fresh_event_details,
            "arbiter_when_event": dict(arbiter_when_event),
            "target_context_episode_proxy": dict(episode_counts),
            "diagnostic_only": True,
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nJSON_REPORT\t{args.json_out}")

    print("\nST_ENTRY_BOTTLENECK_AUDIT_OK")


if __name__ == "__main__":
    main()
