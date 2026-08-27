from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from time import perf_counter

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.arbiter import assess_entry_arbitration
from financial_dashboard.decision.calibration import load_opportunity_calibration
from financial_dashboard.decision.engine import (
    DecisionEngineConfig,
    assess_horizon_decision,
)
from financial_dashboard.decision.entry import assess_entry_decision
from financial_dashboard.decision.history_replay import HistoricalDecisionInputReplayRunner
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.persistent_state import PersistentObjectStore
from financial_dashboard.decision.reaction import (
    ReactionRelevancePolicy,
    select_relevant_zones,
)
from financial_dashboard.decision.reaction import (
    _fvg_distance_atr as _reaction_fvg_distance_atr,
    _fvg_terminal as _reaction_fvg_terminal,
    _derived_age_bars as _reaction_derived_age_bars,
)
from financial_dashboard.decision.scenario import assess_entry_scenario
from financial_dashboard.decision.structural import DecisionHorizon
from financial_dashboard.decision.timeline_cache import load_frozen_decision_timeline
from financial_dashboard.structure_location_replay import CausalBarClock

_LT_FAILURE_TIMEFRAMES = ("1d", "4h", "2h", "1h")
_ST_FAILURE_TIMEFRAMES = ("4h", "2h", "1h", "30m")


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


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _age_bucket(age_bars: int | None, *, active: bool) -> str:
    if active:
        return "active"
    if age_bars is None:
        return "age:unknown"
    if age_bars <= 50:
        return "age:<=50"
    if age_bars <= 200:
        return "age:51-200"
    if age_bars <= 1000:
        return "age:201-1000"
    return "age:>1000"


def _distance_bucket(distance_atr: float | None) -> str:
    if distance_atr is None:
        return "dist:unknown"
    if distance_atr <= 0.0:
        return "dist:inside"
    if distance_atr <= 5.0:
        return "dist:<=5atr"
    return "dist:>5atr"


def _count_failure_sources(
    snapshot,
    *,
    direction_value: int,
    timeframes: tuple[str, ...],
    counter: Counter[str],
) -> None:
    """Count all-history failed zones per tf/type/age/distance bucket (legacy sphere).

    This is the KN-1 diagnostic: it never filters, so chronic old terminal
    failures remain visible next to the scoped reaction counters.
    """

    allowed = {tf.strip().lower() for tf in timeframes}
    ob = snapshot.order_block_behavior
    if ob is not None:
        for item in ob.observations:
            timeframe = item.timeframe.strip().lower()
            if timeframe not in allowed or (1 if item.bullish else -1) != direction_value:
                continue
            state = item.state.strip().upper()
            interaction = item.interaction.strip().upper()
            failed = interaction == "FAILED" or state in {"CONSUMED", "EXPIRED_CANDIDATE"}
            if not failed:
                continue
            counter[
                f"OB:{timeframe}:{_age_bucket(item.age_bars, active=bool(item.active))}"
                f":{_distance_bucket(item.distance_atr)}"
            ] += 1
    fvg = snapshot.fvg_engulfing_lifecycle
    if fvg is not None:
        price = float(snapshot.current_price)
        for row in fvg.fvg:
            timeframe = row.ref.timeframe.strip().lower()
            if timeframe not in allowed or int(row.direction) != direction_value:
                continue
            if not (row.failed_reaction or row.invalid or row.full_fill):
                continue
            counter[
                f"FVG:{timeframe}:{_age_bucket(_reaction_derived_age_bars(row.ref, timeframe), active=not _reaction_fvg_terminal(row))}"
                f":{_distance_bucket(_reaction_fvg_distance_atr(row, price))}"
            ] += 1


def _relevant_zone_count(snapshot, *, policy: ReactionRelevancePolicy | None) -> int:
    ob = snapshot.order_block_behavior
    fvg = snapshot.fvg_engulfing_lifecycle
    if policy is None:
        return (0 if ob is None else len(ob.observations)) + (
            0 if fvg is None else len(fvg.fvg) + len(fvg.engulfing)
        )
    filtered_ob, filtered_fvg = select_relevant_zones(
        ob,
        fvg,
        current_price=snapshot.current_price,
        policy=policy,
    )
    return (0 if filtered_ob is None else len(filtered_ob.observations)) + (
        0 if filtered_fvg is None else len(filtered_fvg.fvg) + len(filtered_fvg.engulfing)
    )


class _RunLengthTracker:
    """Track consecutive-True run lengths for one boolean stream."""

    def __init__(self) -> None:
        self._current = 0
        self.lengths: list[int] = []

    def push(self, value: bool | None) -> None:
        if value is None:
            self._flush()
            return
        if value:
            self._current += 1
        else:
            self._flush()

    def _flush(self) -> None:
        if self._current > 0:
            self.lengths.append(self._current)
        self._current = 0

    def finish(self) -> list[int]:
        self._flush()
        return self.lengths


def _print_stats_line(title: str, values: list[float]) -> None:
    if not values:
        print(f"{title}\tNO_SAMPLES")
        return
    print(
        f"{title}\tmin={min(values):.4g}\tp25={_percentile(values, 0.25):.4g}\t"
        f"med={_percentile(values, 0.50):.4g}\tp75={_percentile(values, 0.75):.4g}\t"
        f"p90={_percentile(values, 0.90):.4g}\tmax={max(values):.4g}"
    )


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
    parser.add_argument(
        "--opportunity-calibration",
        type=Path,
        default=None,
        help="Optional OpportunityCalibration JSON produced by build_opportunity_calibration.py",
    )
    parser.add_argument(
        "--legacy-reaction",
        action="store_true",
        help="Disable the reaction relevance/supersession scope (pre-fix behaviour, A/B diagnostics)",
    )
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

    relevance_policy = None if args.legacy_reaction else ReactionRelevancePolicy()
    calibration_label = "NONE"
    opportunity_calibration = None
    if args.opportunity_calibration is not None:
        record = load_opportunity_calibration(args.opportunity_calibration)
        opportunity_calibration = record.calibration
        calibration_label = str(args.opportunity_calibration)
    engine_config = DecisionEngineConfig(
        opportunity_calibration=opportunity_calibration,
        reaction_relevance=relevance_policy,
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

    # Faz 0 diagnostics (plan Bolum 6.5): KN-1/KN-4 evidence + clearability.
    failure_sources: Counter[str] = Counter()
    conflict_transitions: Counter[str] = Counter()
    relevant_sizes: list[int] = []
    lt_room_values: list[float] = []
    st_room_values: list[float] = []
    heavy_conflict_tracker = _RunLengthTracker()
    heavy_conflict_true_bars = 0
    warmup_snapshots = 0
    first_single_gate: tuple[int, str, str] | None = None
    prev_conflict_state: dict[str, str] = {}

    decision_started = perf_counter()
    for index, snapshot in enumerate(snapshots):
        # Each horizon chain is evaluated exactly once per snapshot; scenario,
        # arbitration and entry layers consume the shared results.
        lt_decision = assess_horizon_decision(
            snapshot, DecisionHorizon.LONG_TERM, config=engine_config
        )
        st_decision = assess_horizon_decision(
            snapshot, DecisionHorizon.SHORT_TERM, config=engine_config
        )
        lt = assess_entry_scenario(
            snapshot,
            DecisionHorizon.LONG_TERM,
            config=engine_config,
            assessment=lt_decision,
        )
        st = assess_entry_scenario(
            snapshot,
            DecisionHorizon.SHORT_TERM,
            config=engine_config,
            assessment=st_decision,
        )
        arbitration = assess_entry_arbitration(
            snapshot,
            config=engine_config,
            scenarios=(lt, st),
        )
        entry = assess_entry_decision(
            snapshot,
            config=engine_config,
            execution_event=None,
            arbitration=arbitration,
            assessments={
                DecisionHorizon.LONG_TERM: lt_decision,
                DecisionHorizon.SHORT_TERM: st_decision,
            },
        )

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
            current_state = _value(assessment.conflict.state)
            previous_state = prev_conflict_state.get(prefix)
            if previous_state is not None and previous_state != current_state:
                conflict_transitions[f"{prefix}:{previous_state}->{current_state}"] += 1
            prev_conflict_state[prefix] = current_state

        arbiter_state[_value(arbitration.state)] += 1
        arbiter_selection[_value(arbitration.selection)] += 1
        _add_many(arbiter_reasons, arbitration.reasons)
        _add_many(arbiter_waiting, arbitration.waiting_for)

        entry_action[_value(entry.action)] += 1
        entry_stage["NONE" if entry.scenario_stage is None else _value(entry.scenario_stage)] += 1
        entry_horizon["NONE" if entry.selected_horizon is None else _value(entry.selected_horizon)] += 1
        _add_many(entry_reasons, entry.reasons)
        _add_many(entry_blockers, entry.blockers)
        _add_many(entry_waiting, entry.waiting_for)

        # --- Faz 0 diagnostics -------------------------------------------------
        lt_direction = lt_decision.structural.direction
        st_direction = st_decision.structural.direction
        if str(lt_direction) == "LONG":
            _count_failure_sources(
                snapshot,
                direction_value=1,
                timeframes=_LT_FAILURE_TIMEFRAMES,
                counter=failure_sources,
            )
        if str(st_direction) == "LONG":
            _count_failure_sources(
                snapshot,
                direction_value=1,
                timeframes=_ST_FAILURE_TIMEFRAMES,
                counter=failure_sources,
            )

        relevant_sizes.append(_relevant_zone_count(snapshot, policy=relevance_policy))

        if lt_decision.opportunity.room_atr is not None:
            lt_room_values.append(float(lt_decision.opportunity.room_atr))
        if st_decision.opportunity.room_atr is not None:
            st_room_values.append(float(st_decision.opportunity.room_atr))

        participation = snapshot.participation_behavior
        heavy_now = False
        if participation is not None:
            try:
                heavy_now = bool(participation.for_timeframe("1h").heavy_conflict)
            except KeyError:
                heavy_now = False
        heavy_conflict_tracker.push(heavy_now)
        if heavy_now:
            heavy_conflict_true_bars += 1

        if _value(lt.presence) == "UNKNOWN":
            warmup_snapshots += 1

        if first_single_gate is None and len(entry.waiting_for) == 1:
            first_single_gate = (index, str(snapshot.as_of), entry.waiting_for[0])

    decision_seconds = perf_counter() - decision_started
    heavy_conflict_runs = heavy_conflict_tracker.finish()

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
    print(
        "REACTION_RELEVANCE\t"
        + ("LEGACY_UNBOUNDED" if relevance_policy is None else relevance_policy.label)
    )
    print(f"OPPORTUNITY_CALIBRATION\t{calibration_label}")
    print(f"WARMUP_SNAPSHOTS\t{warmup_snapshots}")
    print(
        "NOTE\tBUY is impossible in this profile (execution_event=None); READY is the "
        "strongest reachable action"
    )

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

    print("\nREACTION FAILURE SOURCES (legacy sphere, KN-1 diagnostic)")
    print("-" * 60)
    if not failure_sources:
        print("None.")
    else:
        rows = failure_sources.most_common(args.top)
        width = max(len(key) for key, _ in rows)
        for key, count in rows:
            print(f"{key:<{width}}  {count:8d}")

    print("\nCONFLICT TRANSITIONS (clear-rate)")
    print("-" * 40)
    if not conflict_transitions:
        print("None.  # MATERIAL never cleared once entered (expected pre-fix)")
    else:
        rows = conflict_transitions.most_common(args.top)
        width = max(len(key) for key, _ in rows)
        for key, count in rows:
            print(f"{key:<{width}}  {count:6d}")

    print("\nHEAVY_CONFLICT RUN LENGTHS (1h participation, KN-4 diagnostic)")
    print("-" * 60)
    print(f"TRUE_SNAPSHOTS\t{heavy_conflict_true_bars}")
    if heavy_conflict_runs:
        print(f"RUNS\t{len(heavy_conflict_runs)}")
        _print_stats_line("RUN_LENGTHS", [float(value) for value in heavy_conflict_runs])
    else:
        print("RUNS\t0")

    print("\nOPPORTUNITY ROOM ATR DISTRIBUTION")
    print("-" * 36)
    _print_stats_line("LT_ROOM_ATR", lt_room_values)
    _print_stats_line("ST_ROOM_ATR", st_room_values)

    print("\nREACTION RELEVANT SET SIZE")
    print("-" * 28)
    _print_stats_line("RELEVANT_ZONES", [float(value) for value in relevant_sizes])

    print("\nFIRST SINGLE-GATE SNAPSHOT")
    print("-" * 30)
    if first_single_gate is None:
        print("None.  # entry never reached exactly one waiting gate")
    else:
        gate_index, gate_as_of, gate_name = first_single_gate
        print(f"INDEX\t{gate_index}")
        print(f"AS_OF\t{gate_as_of}")
        print(f"GATE\t{gate_name}")

    print("\nENTRY_REASON_PROFILE_OK")


if __name__ == "__main__":
    main()
