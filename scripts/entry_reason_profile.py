from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from time import perf_counter

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.arbiter import arbitrate_entry_scenarios
from financial_dashboard.decision.engine import assess_horizon_decision
from financial_dashboard.decision.entry import compose_entry_decision
from financial_dashboard.decision.history_replay import HistoricalDecisionInputReplayRunner
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.persistent_state import PersistentObjectStore
from financial_dashboard.decision.scenario import ScenarioStage, assess_entry_scenario
from financial_dashboard.decision.structural import DecisionHorizon
from financial_dashboard.decision.timeline_cache import load_frozen_decision_timeline
from financial_dashboard.structure_location_replay import CausalBarClock


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
    clean_symbol = normalize_symbol(symbol)
    first_available: list[pd.Timestamp] = []
    for timeframe in ANALYSIS_TIMEFRAMES:
        frame = store.load(clean_symbol, timeframe)
        if frame.empty:
            raise SystemExit(f"No historical bars found for {clean_symbol} {timeframe}")
        first_timestamp = pd.Timestamp(frame.iloc[0]["timestamp"])
        first_available.append(pd.Timestamp(clock.available_at(first_timestamp, timeframe)))

    common_cutoff = max(first_available)
    decision_frame = store.load(clean_symbol, decision_timeframe)
    if decision_frame.empty:
        raise SystemExit(f"No historical bars found for {clean_symbol} {decision_timeframe}")

    warmup_start: pd.Timestamp | None = None
    for value in decision_frame["timestamp"]:
        timestamp = pd.Timestamp(value)
        if pd.Timestamp(clock.available_at(timestamp, decision_timeframe)) >= common_cutoff:
            warmup_start = timestamp
            break
    if warmup_start is None:
        raise SystemExit(
            "No decision bar exists after all required timeframe histories become causally available"
        )
    if requested_start is None:
        return warmup_start
    return max(warmup_start, _align_requested_start(requested_start, warmup_start))


def _value(value) -> str:
    return getattr(value, "value", str(value))


def _add_many(counter: Counter[str], values) -> None:
    for value in values:
        if value:
            counter[str(value)] += 1


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


def _count_conflict(
    *,
    prefix: str,
    assessment,
    state_counter: Counter[str],
    family_counter: Counter[str],
    material_family_counter: Counter[str],
    reason_counter: Counter[str],
) -> None:
    state_counter[f"{prefix}:{_value(assessment.conflict.state)}"] += 1
    for family in assessment.conflict.families:
        severity = _value(family.severity)
        family_counter[f"{prefix}:{family.family}:{severity}"] += 1
        if severity == "MATERIAL":
            material_family_counter[f"{prefix}:{family.family}"] += 1
        for reason in family.reasons:
            reason_counter[f"{prefix}:{family.family}:{reason}"] += 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Count causal Turn 4-6 scenario/arbitration/entry reasons from an already-frozen "
            "DecisionInput timeline. Domains are never replayed."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
    parser.add_argument("--top", type=int, default=30)
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

    runner = HistoricalDecisionInputReplayRunner(store)
    identity = runner._cache_identity(symbol=clean_symbol, config=config)
    cache_path = PersistentObjectStore(store.root).path_for(identity)
    cache_mb = cache_path.stat().st_size / (1024.0 * 1024.0) if cache_path.exists() else 0.0

    started = perf_counter()
    frozen = load_frozen_decision_timeline(store, clean_symbol, config=config)
    load_seconds = perf_counter() - started
    snapshots = frozen.replay.snapshots

    horizon_state: dict[str, Counter[str]] = {
        "LT presence": Counter(),
        "LT stage": Counter(),
        "LT kind": Counter(),
        "LT opportunity": Counter(),
        "LT eligibility": Counter(),
        "LT reasons": Counter(),
        "LT blockers": Counter(),
        "LT waiting": Counter(),
        "ST presence": Counter(),
        "ST stage": Counter(),
        "ST kind": Counter(),
        "ST opportunity": Counter(),
        "ST eligibility": Counter(),
        "ST reasons": Counter(),
        "ST blockers": Counter(),
        "ST waiting": Counter(),
    }
    conflict_state: Counter[str] = Counter()
    conflict_family: Counter[str] = Counter()
    conflict_material_family: Counter[str] = Counter()
    conflict_reasons: Counter[str] = Counter()
    arbiter_state: Counter[str] = Counter()
    arbiter_selection: Counter[str] = Counter()
    arbiter_reasons: Counter[str] = Counter()
    arbiter_waiting: Counter[str] = Counter()
    entry_action: Counter[str] = Counter()
    entry_stage: Counter[str] = Counter()
    entry_horizon: Counter[str] = Counter()
    entry_reasons: Counter[str] = Counter()
    entry_blockers: Counter[str] = Counter()
    entry_waiting: Counter[str] = Counter()

    decision_started = perf_counter()
    for snapshot in snapshots:
        lt = assess_entry_scenario(snapshot, DecisionHorizon.LONG_TERM)
        st = assess_entry_scenario(snapshot, DecisionHorizon.SHORT_TERM)
        lt_decision = assess_horizon_decision(snapshot, DecisionHorizon.LONG_TERM)
        st_decision = assess_horizon_decision(snapshot, DecisionHorizon.SHORT_TERM)

        for prefix, scenario, assessment in (
            ("LT", lt, lt_decision),
            ("ST", st, st_decision),
        ):
            horizon_state[f"{prefix} presence"][_value(scenario.presence)] += 1
            horizon_state[f"{prefix} stage"][_value(scenario.stage)] += 1
            horizon_state[f"{prefix} kind"][_value(scenario.kind)] += 1
            horizon_state[f"{prefix} opportunity"][_value(scenario.opportunity_state)] += 1
            horizon_state[f"{prefix} eligibility"][_value(scenario.eligibility_state)] += 1
            _add_many(horizon_state[f"{prefix} reasons"], scenario.reasons)
            _add_many(horizon_state[f"{prefix} blockers"], scenario.blockers)
            _add_many(horizon_state[f"{prefix} waiting"], scenario.waiting_for)
            _count_conflict(
                prefix=prefix,
                assessment=assessment,
                state_counter=conflict_state,
                family_counter=conflict_family,
                material_family_counter=conflict_material_family,
                reason_counter=conflict_reasons,
            )

        arbitration = arbitrate_entry_scenarios(lt, st)
        arbiter_state[_value(arbitration.state)] += 1
        arbiter_selection[_value(arbitration.selection)] += 1
        _add_many(arbiter_reasons, arbitration.reasons)
        _add_many(arbiter_waiting, arbitration.waiting_for)

        selected_assessment = None
        if arbitration.selected_scenario is not None and arbitration.selected_scenario.stage is ScenarioStage.QUALIFIED:
            selected_assessment = (
                lt_decision
                if arbitration.selected_horizon is DecisionHorizon.LONG_TERM
                else st_decision
            )
        entry = compose_entry_decision(
            arbitration,
            selected_assessment=selected_assessment,
            execution_event_consumed=False,
        )
        entry_action[_value(entry.action)] += 1
        entry_stage["NONE" if entry.scenario_stage is None else _value(entry.scenario_stage)] += 1
        entry_horizon["NONE" if entry.selected_horizon is None else _value(entry.selected_horizon)] += 1
        _add_many(entry_reasons, entry.reasons)
        _add_many(entry_blockers, entry.blockers)
        _add_many(entry_waiting, entry.waiting_for)

    decision_seconds = perf_counter() - decision_started

    print("=" * 72)
    print("ENTRY SCENARIO / READINESS REASON PROFILE")
    print("=" * 72)
    print(f"SYMBOL\t{clean_symbol}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print(f"FROZEN_CACHE_STATUS\t{frozen.cache_status}")
    print(f"FROZEN_CACHE_FILE_MB\t{cache_mb:.3f}")
    print(f"FROZEN_TIMELINE_LOAD_SECONDS\t{load_seconds:.3f}")
    print(f"REASON_PROFILE_SECONDS\t{decision_seconds:.3f}")
    print("DOMAIN_REPLAY_SECONDS\t0.000")

    for title in (
        "LT presence", "LT stage", "LT kind", "LT opportunity", "LT eligibility",
        "ST presence", "ST stage", "ST kind", "ST opportunity", "ST eligibility",
    ):
        _print_counter(title.upper(), horizon_state[title])

    for title in (
        "LT reasons", "LT blockers", "LT waiting",
        "ST reasons", "ST blockers", "ST waiting",
    ):
        _print_counter(title.upper(), horizon_state[title], top=args.top)

    _print_counter("CONFLICT STATE BY HORIZON", conflict_state)
    _print_counter("CONFLICT FAMILY / SEVERITY", conflict_family, top=args.top)
    _print_counter("MATERIAL CONFLICT FAMILY", conflict_material_family, top=args.top)
    _print_counter("CONFLICT FAMILY REASONS", conflict_reasons, top=args.top)
    _print_counter("ARBITER STATE", arbiter_state)
    _print_counter("ARBITER SELECTION", arbiter_selection)
    _print_counter("ARBITER REASONS", arbiter_reasons, top=args.top)
    _print_counter("ARBITER WAITING", arbiter_waiting, top=args.top)
    _print_counter("ENTRY ACTION", entry_action)
    _print_counter("ENTRY SELECTED HORIZON", entry_horizon)
    _print_counter("ENTRY SCENARIO STAGE", entry_stage)
    _print_counter("ENTRY REASONS", entry_reasons, top=args.top)
    _print_counter("ENTRY BLOCKERS", entry_blockers, top=args.top)
    _print_counter("ENTRY WAITING", entry_waiting, top=args.top)
    print("\nENTRY_REASON_PROFILE_OK")


if __name__ == "__main__":
    main()
