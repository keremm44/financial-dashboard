from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from time import perf_counter

from entry_reason_profile import _calibration, _causal_warmup_start
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.arbiter import assess_entry_arbitration
from financial_dashboard.decision.engine import DecisionEngineConfig
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
    arbiter_when_event: Counter[str] = Counter()

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

        if scenario.presence is not ScenarioPresence.PRESENT:
            event_outcomes["EVENT_WHILE_ST_NOT_PRESENT"] += 1
            continue

        attribution = attribute_entry_bottlenecks(scenario)
        if scenario.stage is ScenarioStage.QUALIFIED:
            event_outcomes["EVENT_WHILE_ST_QUALIFIED"] += 1
        else:
            event_outcomes["EVENT_WHILE_ST_NOT_QUALIFIED"] += 1
            event_gate_sets[attribution.label] += 1

        arbitration = assess_entry_arbitration(snapshot, config=engine_config)
        arbiter_when_event[_value(arbitration.selection)] += 1
        entry = assess_entry_decision(
            snapshot,
            config=engine_config,
            execution_event=event,
        )
        if bool(entry.execution_event_consumed):
            event_outcomes["EVENT_CONSUMED"] += 1
        else:
            event_outcomes["EVENT_NOT_CONSUMED"] += 1
            event_outcomes[f"NOT_CONSUMED_ENTRY_ACTION:{_value(entry.action)}"] += 1

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
            "fresh_event_gate_sets": dict(event_gate_sets),
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
