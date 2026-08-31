from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class AuditWindow:
    label: str
    start: str
    end: str


WINDOWS = (
    AuditWindow("MARCH_16_24", "2026-03-16 10:00", "2026-03-24 10:00"),
    AuditWindow("JUNE_12_19", "2026-06-12 10:00", "2026-06-19 10:00"),
    AuditWindow("JUNE_05_11", "2026-06-05 10:00", "2026-06-11 10:00"),
    AuditWindow("AUG_03_05", "2026-08-03 14:00", "2026-08-05 10:00"),
    AuditWindow("AUG_05_07", "2026-08-05 14:00", "2026-08-07 10:00"),
)


def _token(value) -> str:
    return str(getattr(value, "value", value))


def _compact(values) -> str:
    rows = tuple(str(item) for item in (values or ()) if str(item))
    return "-" if not rows else "; ".join(rows)


def _aligned(value: str, reference: pd.Timestamp) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if reference.tzinfo is not None and stamp.tzinfo is None:
        return stamp.tz_localize(reference.tzinfo)
    if reference.tzinfo is None and stamp.tzinfo is not None:
        return stamp.tz_localize(None)
    if reference.tzinfo is not None and stamp.tzinfo is not None:
        return stamp.tz_convert(reference.tzinfo)
    return stamp


def _load_calibration(cache_root: Path, symbol: str):
    path = cache_root / "calibration" / "opportunity" / f"{normalize_symbol(symbol)}.json"
    if not path.exists():
        raise SystemExit(f"Missing opportunity calibration: {path}")
    return load_opportunity_calibration(path).calibration, path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Frozen-cache audit of five candidate missed ASELS rises. "
            "Prints the full hourly Decision/lifecycle state without changing trading rules."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
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

    print("FIVE CANDIDATE MOVE AUDIT")
    print("=========================")
    print(f"SYMBOL\t{symbol}")
    print(f"FROZEN_CACHE\t{frozen.cache_status}")
    print("DOMAIN_REPLAY\tNOT_RUN")
    print(f"CALIBRATION\t{calibration_path}")
    print()

    for window in WINDOWS:
        start = _aligned(window.start, reference)
        end = _aligned(window.end, reference)
        action_counts: Counter[str] = Counter()
        waiting_counts: Counter[str] = Counter()
        blocker_counts: Counter[str] = Counter()
        reason_counts: Counter[str] = Counter()
        rows = 0

        print(f"MOVE {window.label} | {start} -> {end}")
        print("-" * 96)

        for snapshot in snapshots:
            as_of = pd.Timestamp(snapshot.as_of)
            if as_of < start:
                continue
            if as_of > end:
                break

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
            lifecycle_event = decisions_by_time.get(as_of)
            action = "-" if lifecycle_event is None else _token(lifecycle_event.action)
            target_path = snapshot.target_path(assessment.structural.direction)
            active = target_path.active_node
            lt = assessment.structural_snapshot.long_term
            opportunity = assessment.opportunity

            rows += 1
            action_counts[action] += 1
            if lifecycle_event is not None:
                waiting_counts.update(lifecycle_event.waiting_for)
                blocker_counts.update(lifecycle_event.blockers)
                reason_counts.update(lifecycle_event.reasons)

            print(f"{as_of} price={float(snapshot.current_price):.2f} action={action}")
            print(
                "  MARKET "
                f"ST={_token(assessment.structural.direction)}/{_token(assessment.structural.thesis_state)} "
                f"LT={_token(lt.direction)}/{_token(lt.thesis_state)} "
                f"scenario={_token(scenario.presence)}/{_token(scenario.stage)}/{_token(scenario.kind)}"
            )
            print(
                "  STABIL "
                f"authority={_token(stabil.state)} durability={_token(assessment.durability.state)} "
                f"quality={_token(assessment.durability.data_quality)}"
            )
            print("  stabil_reasons=" + _compact(assessment.durability.reasons))
            print(
                "  ENTRY "
                f"timing={_token(assessment.timing.state)} "
                f"conflict={_token(assessment.conflict.state)} "
                f"eligibility={_token(assessment.eligibility.state)} "
                f"execution={_token(assessment.execution.state)}"
            )
            print("  timing_wait=" + _compact(assessment.timing.waiting_for))
            print("  elig_wait=" + _compact(assessment.eligibility.waiting_for))
            print("  elig_blockers=" + _compact(assessment.eligibility.blockers))
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
            print(
                "  EXEC_EVENT "
                f"present={'YES' if as_of in entry_events else 'NO'} "
                f"event={entry_events.get(as_of) or '-'}"
            )
            if lifecycle_event is not None:
                print("  lifecycle_wait=" + _compact(lifecycle_event.waiting_for))
                print("  lifecycle_blockers=" + _compact(lifecycle_event.blockers))
                print("  lifecycle_reasons=" + _compact(lifecycle_event.reasons))
            print()

        print("SUMMARY")
        print(f"  rows={rows}")
        print("  actions=" + (", ".join(f"{k}:{v}" for k, v in action_counts.most_common()) or "-"))
        print("  top_waiting=" + (", ".join(f"{k}:{v}" for k, v in waiting_counts.most_common(8)) or "-"))
        print("  top_blockers=" + (", ".join(f"{k}:{v}" for k, v in blocker_counts.most_common(8)) or "-"))
        print("  top_reasons=" + (", ".join(f"{k}:{v}" for k, v in reason_counts.most_common(8)) or "-"))
        print()

    print("INTERPRETATION RULE")
    print("-------------------")
    print("Do not label a move as a system bug merely because price later rose.")
    print("For each move, separate: justified avoidance, plausible missed opportunity, and architectural over-filtering.")
    print("Any proposed loosening must be checked for false-BUY risk in other market states before implementation.")
    print("FIVE_CANDIDATE_MOVE_AUDIT_OK")


if __name__ == "__main__":
    main()
