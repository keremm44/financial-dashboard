from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.calibration import load_opportunity_calibration
from financial_dashboard.decision.engine import DecisionEngineConfig, assess_horizon_decision
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.scenario import assess_entry_scenario
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection, ThesisState
from financial_dashboard.decision.timeline_cache import DecisionTimelineCacheMiss, load_frozen_decision_timeline


STRUCTURAL_WAITS = {
    "STRUCTURAL_TRANSITION_TO_RESOLVE",
    "CANONICAL_STRUCTURAL_FOLLOW_THROUGH",
}


def _token(value) -> str:
    return str(getattr(value, "value", value))


def _compact(values) -> str:
    rows = tuple(str(item) for item in (values or ()) if str(item))
    return "-" if not rows else "; ".join(rows)


def _load_calibration(cache_root: Path, symbol: str):
    path = cache_root / "calibration" / "opportunity" / f"{normalize_symbol(symbol)}.json"
    if not path.exists():
        raise SystemExit(f"Missing opportunity calibration: {path}")
    return load_opportunity_calibration(path).calibration, path


def _forward_stats(snapshots, index: int, bars: int):
    entry = float(snapshots[index].current_price)
    end_index = min(len(snapshots) - 1, index + bars)
    future = snapshots[index + 1 : end_index + 1]
    if not future:
        return None
    prices = [float(row.current_price) for row in future]
    end_price = prices[-1]
    return {
        "bars": len(prices),
        "end": (end_price / entry - 1.0) * 100.0,
        "mfe": (max(prices) / entry - 1.0) * 100.0,
        "mae": (min(prices) / entry - 1.0) * 100.0,
    }


def _fmt_forward(stats) -> str:
    if stats is None:
        return "-"
    return (
        f"end={stats['end']:+.2f}% "
        f"mfe={stats['mfe']:+.2f}% "
        f"mae={stats['mae']:+.2f}% "
        f"bars={stats['bars']}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Audit historical ST LONG/TRANSITIONING states before softening structural confirmation. "
            "This script changes no decision rule; it measures where a softening could unlock trades "
            "and how price behaved afterward."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--show-all", action="store_true")
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

    calibration, calibration_path = _load_calibration(args.cache_root, symbol)
    config = DecisionEngineConfig(opportunity_calibration=calibration)

    rows = []
    for index, snapshot in enumerate(snapshots):
        assessment = assess_horizon_decision(
            snapshot,
            DecisionHorizon.SHORT_TERM,
            config=config,
            execution_event=None,
        )
        structural = assessment.structural
        if not (
            structural.direction is StructuralDirection.LONG
            and structural.thesis_state is ThesisState.TRANSITIONING
        ):
            continue

        scenario = assess_entry_scenario(
            snapshot,
            DecisionHorizon.SHORT_TERM,
            config=config,
            assessment=assessment,
        )
        eligibility_waits = set(assessment.eligibility.waiting_for)
        scenario_waits = set(scenario.waiting_for)
        all_waits = eligibility_waits | scenario_waits
        has_structural_wait = bool(all_waits & STRUCTURAL_WAITS)
        if not has_structural_wait:
            continue

        other_waits = all_waits - STRUCTURAL_WAITS
        blockers = set(assessment.eligibility.blockers) | set(scenario.blockers)
        opportunity = assessment.opportunity
        durability = assessment.durability
        timing = assessment.timing
        conflict = assessment.conflict
        target_path = snapshot.target_path(structural.direction)

        clean_unlock = not other_waits and not blockers
        supportive = bool(
            _token(timing.state) == "READY"
            and _token(conflict.state) in {"NONE", "LOW"}
            and _token(opportunity.state) in {"MODERATE", "AMPLE"}
            and _token(durability.state) not in {"BROKEN", "FRACTURED", "UNKNOWN"}
            and not blockers
        )

        rows.append(
            {
                "index": index,
                "as_of": pd.Timestamp(snapshot.as_of),
                "price": float(snapshot.current_price),
                "clean_unlock": clean_unlock,
                "supportive": supportive,
                "timing": _token(timing.state),
                "conflict": _token(conflict.state),
                "room": _token(opportunity.state),
                "room_atr": opportunity.room_atr,
                "durability": _token(durability.state),
                "path": _token(target_path.status),
                "eligibility": _token(assessment.eligibility.state),
                "scenario_stage": _token(scenario.stage),
                "structural_waits": tuple(sorted(all_waits & STRUCTURAL_WAITS)),
                "other_waits": tuple(sorted(other_waits)),
                "blockers": tuple(sorted(blockers)),
            }
        )

    print("ST TRANSITION SOFTENING RISK AUDIT")
    print("==================================")
    print(f"SYMBOL\t{symbol}")
    print(f"FROZEN_CACHE\t{frozen.cache_status}")
    print("DOMAIN_REPLAY\tNOT_RUN")
    print(f"CALIBRATION\t{calibration_path}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print()

    clean = [row for row in rows if row["clean_unlock"]]
    supportive = [row for row in rows if row["supportive"]]
    clean_supportive = [row for row in rows if row["clean_unlock"] and row["supportive"]]
    print("SUMMARY")
    print(f"  transition_rows={len(rows)}")
    print(f"  clean_structural_only_unlocks={len(clean)}")
    print(f"  supportive_transition_rows={len(supportive)}")
    print(f"  clean_and_supportive={len(clean_supportive)}")
    print()

    selected = rows if args.show_all else clean_supportive
    print("CANDIDATES" if not args.show_all else "ALL_TRANSITION_ROWS")
    print("----------")
    if not selected:
        print("NONE")
    for row in selected:
        index = row["index"]
        f8 = _forward_stats(snapshots, index, 8)
        f24 = _forward_stats(snapshots, index, 24)
        f40 = _forward_stats(snapshots, index, 40)
        room_atr = "-" if row["room_atr"] is None else f"{float(row['room_atr']):.3f}"
        print(
            f"{row['as_of']} price={row['price']:.2f} "
            f"clean={'YES' if row['clean_unlock'] else 'NO'} "
            f"supportive={'YES' if row['supportive'] else 'NO'}"
        )
        print(
            "  STATE "
            f"timing={row['timing']} conflict={row['conflict']} "
            f"room={row['room']}/{room_atr} durability={row['durability']} "
            f"path={row['path']} eligibility={row['eligibility']} scenario={row['scenario_stage']}"
        )
        print("  structural_waits=" + _compact(row["structural_waits"]))
        print("  other_waits=" + _compact(row["other_waits"]))
        print("  blockers=" + _compact(row["blockers"]))
        print("  FWD_8  " + _fmt_forward(f8))
        print("  FWD_24 " + _fmt_forward(f24))
        print("  FWD_40 " + _fmt_forward(f40))
        print()

    if clean_supportive:
        outcomes = []
        for row in clean_supportive:
            stats = _forward_stats(snapshots, row["index"], 24)
            if stats is not None:
                outcomes.append((row, stats))
        positive = sum(1 for _, stats in outcomes if stats["end"] > 0)
        hard_adverse = sum(1 for _, stats in outcomes if stats["mae"] <= -5.0)
        strong_follow = sum(1 for _, stats in outcomes if stats["mfe"] >= 5.0)
        print("CLEAN_SUPPORTIVE_24BAR_OUTCOME")
        print(f"  observed={len(outcomes)}")
        print(f"  positive_end={positive}")
        print(f"  reached_plus_5pct={strong_follow}")
        print(f"  suffered_minus_5pct_or_worse={hard_adverse}")
        print()

    print("READING GUIDE")
    print("-------------")
    print("1. clean=YES means removing only the two structural-transition waits would leave no other scenario/eligibility wait or blocker.")
    print("2. supportive=YES additionally requires ready timing, low/no conflict, moderate/ample room, and Stabil not broken/fractured/unknown.")
    print("3. FWD metrics are observational counterfactual diagnostics, not simulated trade P&L.")
    print("4. If clean+supportive rows frequently suffer >=5% adverse moves, broad softening is unsafe.")
    print("5. If June 17 appears clean+supportive while most comparable rows follow through, a narrow conditional softening is evidence-supported.")
    print("ST_TRANSITION_SOFTENING_RISK_AUDIT_OK")


if __name__ == "__main__":
    main()
