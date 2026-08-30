from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.calibration import load_opportunity_calibration
from financial_dashboard.decision.canonical_events import canonical_decision_events_from_replay
from financial_dashboard.decision.engine import DecisionEngineConfig, assess_horizon_decision
from financial_dashboard.decision.execution_detect import detect_1h_execution_events
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.lifecycle_replay import replay_canonical_trade_lifecycle
from financial_dashboard.decision.scenario import assess_entry_scenario
from financial_dashboard.decision.stabil_authority import assess_stabil_authority
from financial_dashboard.decision.structural import DecisionHorizon
from financial_dashboard.decision.timeline_cache import DecisionTimelineCacheMiss, load_frozen_decision_timeline


def _token(value) -> str:
    return str(getattr(value, "value", value))


def _aligned(value: str, reference: pd.Timestamp) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if reference.tzinfo is not None and stamp.tzinfo is None:
        return stamp.tz_localize(reference.tzinfo)
    if reference.tzinfo is None and stamp.tzinfo is not None:
        return stamp.tz_localize(None)
    if reference.tzinfo is not None and stamp.tzinfo is not None:
        return stamp.tz_convert(reference.tzinfo)
    return stamp


def _compact(values) -> str:
    rows = tuple(str(item) for item in (values or ()) if str(item))
    return "-" if not rows else "; ".join(rows)


def _load_calibration(cache_root: Path, symbol: str):
    path = cache_root / "calibration" / "opportunity" / f"{normalize_symbol(symbol)}.json"
    if not path.exists():
        raise SystemExit(f"Missing opportunity calibration: {path}")
    return load_opportunity_calibration(path).calibration, path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Focused frozen-cache audit around the 2026-07-08 false ST long entry. "
            "Shows the exact causal state that allowed the trade without changing any rule."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default="2026-07-07 11:00")
    parser.add_argument("--end", default="2026-07-09 18:00")
    parser.add_argument("--focus", default="2026-07-08 18:00")
    args = parser.parse_args()

    store = ParquetOHLCVStore(args.cache_root)
    symbol = normalize_symbol(args.symbol)
    try:
        frozen = load_frozen_decision_timeline(
            store,
            symbol,
            config=HistoricalDecisionInputConfig(),
        )
    except DecisionTimelineCacheMiss as exc:
        raise SystemExit("FROZEN_DECISION_TIMELINE_CACHE_MISS; domains were NOT replayed") from exc

    snapshots = tuple(sorted(frozen.replay.snapshots, key=lambda row: pd.Timestamp(row.as_of)))
    if not snapshots:
        raise SystemExit("Frozen timeline is empty")

    reference = pd.Timestamp(snapshots[0].as_of)
    start = _aligned(args.start, reference)
    end = _aligned(args.end, reference)
    focus = _aligned(args.focus, reference)

    calibration, calibration_path = _load_calibration(args.cache_root, symbol)
    config = DecisionEngineConfig(opportunity_calibration=calibration)

    entry_events, exit_events = detect_1h_execution_events(snapshots)
    lifecycle = replay_canonical_trade_lifecycle(
        snapshots,
        config=config,
        entry_execution_events=entry_events,
        exit_execution_events=exit_events,
    )
    decisions = canonical_decision_events_from_replay(lifecycle)
    decisions_by_time = {pd.Timestamp(event.timestamp): event for event in decisions}

    print("JULY 08 FALSE ENTRY AUDIT")
    print("=========================")
    print(f"SYMBOL\t{symbol}")
    print(f"FROZEN_CACHE\t{frozen.cache_status}")
    print("DOMAIN_REPLAY\tNOT_RUN")
    print(f"CALIBRATION\t{calibration_path}")
    print(f"WINDOW\t{start} -> {end}")
    print(f"FOCUS\t{focus}")
    print()

    focus_seen = False
    for snapshot in snapshots:
        as_of = pd.Timestamp(snapshot.as_of)
        if as_of < start or as_of > end:
            continue

        assessment = assess_horizon_decision(
            snapshot,
            DecisionHorizon.SHORT_TERM,
            config=config,
            execution_event=entry_events.get(as_of),
        )
        scenario = assess_entry_scenario(
            snapshot,
            DecisionHorizon.SHORT_TERM,
            config=config,
            assessment=assessment,
        )
        stabil = assess_stabil_authority(getattr(snapshot, "stabil_support", None))
        event = decisions_by_time.get(as_of)
        action = "-" if event is None else _token(event.action)
        marker = " <<< FOCUS" if as_of == focus else ""
        if as_of == focus:
            focus_seen = True

        lt = assessment.structural_snapshot.long_term
        opportunity = assessment.opportunity
        durability = assessment.durability
        timing = assessment.timing
        conflict = assessment.conflict
        execution = assessment.execution
        target_path = snapshot.target_path(assessment.structural.direction)
        active = target_path.active_node

        print(
            f"{as_of} price={float(snapshot.current_price):.2f} action={action}{marker}"
        )
        print(
            "  MARKET "
            f"ST={_token(assessment.structural.direction)}/{_token(assessment.structural.thesis_state)} "
            f"LT={_token(lt.direction)}/{_token(lt.thesis_state)} "
            f"scenario={_token(scenario.presence)}/{_token(scenario.stage)}/{_token(scenario.kind)}"
        )
        print(
            "  STABIL "
            f"authority={_token(stabil.state)} durability={_token(durability.state)} "
            f"quality={_token(durability.data_quality)}"
        )
        print("  stabil_reasons=" + _compact(durability.reasons))
        print(
            "  ENTRY "
            f"timing={_token(timing.state)} conflict={_token(conflict.state)} "
            f"eligibility={_token(assessment.eligibility.state)} "
            f"execution={_token(execution.state)}"
        )
        print("  timing_wait=" + _compact(timing.waiting_for))
        print("  elig_wait=" + _compact(assessment.eligibility.waiting_for))
        print("  elig_blockers=" + _compact(assessment.eligibility.blockers))
        print("  elig_reasons=" + _compact(assessment.eligibility.reasons))
        print(
            "  ROOM "
            f"state={_token(opportunity.state)} "
            f"room_atr={'-' if opportunity.room_atr is None else f'{opportunity.room_atr:.3f}'} "
            f"target={opportunity.target_identity or '-'} "
            f"semantics={opportunity.target_semantics or '-'} "
            f"hard={'YES' if opportunity.hard_room_constraint else 'NO'}"
        )
        print(
            "  PATH "
            f"status={_token(target_path.status)} "
            f"active={'-' if active is None else active.identity} "
            f"active_state={'-' if active is None else _token(active.state)}"
        )
        print("  scenario_wait=" + _compact(scenario.waiting_for))
        print("  scenario_blockers=" + _compact(scenario.blockers))
        print("  scenario_reasons=" + _compact(scenario.reasons))
        print(
            "  EXEC_EVENT "
            f"present={'YES' if as_of in entry_events else 'NO'} "
            f"event={entry_events.get(as_of) or '-'}"
        )
        if event is not None:
            print("  lifecycle_wait=" + _compact(event.waiting_for))
            print("  lifecycle_blockers=" + _compact(event.blockers))
            print("  lifecycle_reasons=" + _compact(event.reasons))
        print()

    if not focus_seen:
        print("WARNING: focus timestamp was not present in frozen snapshots")

    print("READING GUIDE")
    print("-------------")
    print("1. If ST is still weak/transitioning at focus, the false entry came from transition authority being too permissive.")
    print("2. If Stabil support is broken/fractured at focus, the support guard failed upstream or was bypassed.")
    print("3. If timing/room/conflict are all healthy but target path is unresolved/defended, inspect scenario/path authority.")
    print("4. If the only newly-satisfied fact at focus is EXEC_EVENT, execution freshness may be opening a weak thesis.")
    print("5. Compare the hours before focus: the first field that flips from WAIT/BLOCKED to eligible identifies the causal unlock.")
    print("JULY_08_FALSE_ENTRY_AUDIT_OK")


if __name__ == "__main__":
    main()
