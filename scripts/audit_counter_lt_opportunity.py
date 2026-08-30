from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import pandas as pd

from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.calibration import load_opportunity_calibration
from financial_dashboard.decision.engine import DecisionEngineConfig, assess_horizon_decision
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.stabil_authority import assess_stabil_authority
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection, ThesisState
from financial_dashboard.decision.timeline_cache import DecisionTimelineCacheMiss, load_frozen_decision_timeline


def _token(value) -> str:
    return str(getattr(value, "value", value))


def _aligned(value: str | None, reference: pd.Timestamp) -> pd.Timestamp | None:
    if value is None:
        return None
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
        return None, path
    return load_opportunity_calibration(path).calibration, path


def _unknown_kind(reasons: tuple[str, ...], room_atr: float | None) -> str:
    reason_set = set(reasons)
    if "TARGETING_UNAVAILABLE" in reason_set:
        return "UNKNOWN_TARGETING_UNAVAILABLE"
    if "NO_DIRECTIONAL_TARGET_OBSERVED_NOT_CLEAR_PATH" in reason_set:
        return "UNKNOWN_NO_DIRECTIONAL_TARGET"
    if "OPPORTUNITY_CALIBRATION_REQUIRED" in reason_set:
        return "UNKNOWN_CALIBRATION_REQUIRED_WITH_ROOM" if room_atr is not None else "UNKNOWN_CALIBRATION_REQUIRED"
    if "OPPORTUNITY_SIDE_UNRESOLVED" in reason_set:
        return "UNKNOWN_SIDE_UNRESOLVED"
    return "UNKNOWN_OTHER"


def _is_counter_lt(assessment) -> bool:
    lt = assessment.structural_snapshot.long_term
    return bool(
        assessment.structural.direction is StructuralDirection.LONG
        and lt.direction is StructuralDirection.SHORT
        and lt.thesis_state is ThesisState.INTACT
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Frozen-cache-only audit of counter-LT SHORT_TERM LONG decisions and Opportunity UNKNOWN semantics. "
            "No domain replay and no trading-rule mutation."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None, help="Optional inclusive timestamp/date filter")
    parser.add_argument("--end", default=None, help="Optional inclusive timestamp/date filter")
    parser.add_argument(
        "--dates",
        nargs="*",
        default=None,
        help="Optional calendar dates. If supplied, only snapshots on these dates are shown.",
    )
    parser.add_argument(
        "--all-st-long",
        action="store_true",
        help="Show every ST LONG snapshot in the selected window, not only counter-LT rows.",
    )
    args = parser.parse_args()

    store = ParquetOHLCVStore(args.cache_root)
    symbol = normalize_symbol(args.symbol)
    try:
        frozen = load_frozen_decision_timeline(store, symbol, config=HistoricalDecisionInputConfig())
    except DecisionTimelineCacheMiss as exc:
        raise SystemExit("FROZEN_DECISION_TIMELINE_CACHE_MISS; domains were NOT replayed") from exc

    snapshots = tuple(frozen.replay.snapshots)
    calibration, calibration_path = _load_calibration(args.cache_root, symbol)
    config = DecisionEngineConfig(opportunity_calibration=calibration)

    reference = pd.Timestamp(snapshots[0].as_of) if snapshots else pd.Timestamp.now()
    start = _aligned(args.start, reference)
    end = _aligned(args.end, reference)
    selected_dates = {pd.Timestamp(item).date() for item in (args.dates or ())}

    opportunity_counter: Counter[str] = Counter()
    waiting_counter: Counter[str] = Counter()
    gate_combo_counter: Counter[str] = Counter()
    rows = 0
    counter_rows = 0
    ready_counter_rows = 0
    ready_unknown_rows = 0

    print("COUNTER_LT_OPPORTUNITY_AUDIT")
    print("============================")
    print(f"SYMBOL\t{symbol}")
    print(f"FROZEN_CACHE\t{frozen.cache_status}")
    print("DOMAIN_REPLAY\tNOT_RUN")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print(f"CALIBRATION_FILE\t{calibration_path}")
    print(f"CALIBRATION_PRESENT\t{'YES' if calibration is not None else 'NO'}")
    if calibration is not None:
        print(
            "CALIBRATION_BOUNDS_ATR\t"
            f"none<={calibration.none_max_atr:.6g}; "
            f"compressed<={calibration.compressed_max_atr:.6g}; "
            f"moderate<={calibration.moderate_max_atr:.6g}; ample>moderate"
        )
    print(f"FILTER_START\t{start if start is not None else '-'}")
    print(f"FILTER_END\t{end if end is not None else '-'}")
    print(f"FILTER_DATES\t{','.join(str(item) for item in sorted(selected_dates)) if selected_dates else '-'}")
    print(f"MODE\t{'ALL_ST_LONG' if args.all_st_long else 'COUNTER_LT_ONLY'}")

    print("\nROWS")
    print("----")
    for snapshot in snapshots:
        as_of = pd.Timestamp(snapshot.as_of)
        if start is not None and as_of < start:
            continue
        if end is not None and as_of > end:
            continue
        if selected_dates and as_of.date() not in selected_dates:
            continue

        assessment = assess_horizon_decision(
            snapshot,
            DecisionHorizon.SHORT_TERM,
            config=config,
            execution_event=None,
        )
        if assessment.structural.direction is not StructuralDirection.LONG:
            continue

        counter_lt = _is_counter_lt(assessment)
        if not args.all_st_long and not counter_lt:
            continue

        rows += 1
        if counter_lt:
            counter_rows += 1

        opportunity = assessment.opportunity
        timing = assessment.timing
        conflict = assessment.conflict
        eligibility = assessment.eligibility
        lt = assessment.structural_snapshot.long_term
        stabil = assess_stabil_authority(getattr(snapshot, "stabil_support", None))

        target_present = opportunity.target_identity is not None
        targeting_present = getattr(snapshot, "targeting", None) is not None
        opportunity_state = _token(opportunity.state)
        unknown_kind = "-"
        if opportunity_state == "UNKNOWN":
            unknown_kind = _unknown_kind(tuple(opportunity.reasons), opportunity.room_atr)
            opportunity_counter[unknown_kind] += 1
        else:
            opportunity_counter[opportunity_state] += 1

        waiting = tuple(eligibility.waiting_for)
        waiting_counter.update(waiting)
        counter_waits = tuple(item for item in waiting if item.startswith("COUNTER_LT_ST_"))
        combo = "+".join(counter_waits) if counter_waits else "NO_COUNTER_LT_WAIT"
        gate_combo_counter[combo] += 1

        if counter_lt and _token(timing.state) == "READY":
            ready_counter_rows += 1
            if opportunity_state == "UNKNOWN":
                ready_unknown_rows += 1

        transition = assessment.st_transition
        transition_state = "NONE" if transition is None else _token(transition.state)
        own_thesis = "NO" if transition is None else ("YES" if transition.can_own_trade_thesis else "NO")

        print(
            f"{as_of} price={float(snapshot.current_price):.2f} "
            f"counter_lt={'YES' if counter_lt else 'NO'} "
            f"ST={_token(assessment.structural.direction)}/{_token(assessment.structural.thesis_state)} "
            f"LT={_token(lt.direction)}/{_token(lt.thesis_state)} "
            f"stabil={_token(stabil.state)} "
            f"transition={transition_state}/own={own_thesis} "
            f"timing={_token(timing.state)} conflict={_token(conflict.state)} "
            f"elig={_token(eligibility.state)} opp={opportunity_state}"
        )
        print(
            "  opportunity="
            f"room_atr={'-' if opportunity.room_atr is None else f'{opportunity.room_atr:.6g}'} "
            f"targeting={'YES' if targeting_present else 'NO'} "
            f"target={'YES' if target_present else 'NO'} "
            f"target_id={opportunity.target_identity or '-'} "
            f"quality={opportunity.target_quality or '-'} "
            f"semantics={opportunity.target_semantics or '-'} "
            f"hard_room={'YES' if opportunity.hard_room_constraint else 'NO'} "
            f"unknown_kind={unknown_kind}"
        )
        print("  opp_reasons=" + ("; ".join(opportunity.reasons) if opportunity.reasons else "-"))
        print("  waiting=" + ("; ".join(waiting) if waiting else "-"))
        print("  reasons=" + ("; ".join(eligibility.reasons) if eligibility.reasons else "-"))

    print("\nSUMMARY")
    print("-------")
    print(f"ROWS_SHOWN\t{rows}")
    print(f"COUNTER_LT_ROWS\t{counter_rows}")
    print(f"COUNTER_LT_TIMING_READY_ROWS\t{ready_counter_rows}")
    print(f"COUNTER_LT_TIMING_READY_OPPORTUNITY_UNKNOWN_ROWS\t{ready_unknown_rows}")

    print("\nOPPORTUNITY STATE / UNKNOWN CAUSE")
    print("---------------------------------")
    if opportunity_counter:
        for key, count in opportunity_counter.most_common():
            print(f"{key:<48} {count:>5}")
    else:
        print("None.")

    print("\nCOUNTER-LT WAIT COMBINATIONS")
    print("----------------------------")
    if gate_combo_counter:
        for key, count in gate_combo_counter.most_common():
            print(f"{key:<96} {count:>5}")
    else:
        print("None.")

    print("\nALL WAITING REASONS")
    print("-------------------")
    if waiting_counter:
        for key, count in waiting_counter.most_common(25):
            print(f"{key:<72} {count:>5}")
    else:
        print("None.")

    print("\nCOUNTER_LT_OPPORTUNITY_AUDIT_OK")


if __name__ == "__main__":
    main()
