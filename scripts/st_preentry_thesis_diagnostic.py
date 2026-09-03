from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from types import SimpleNamespace

from entry_reason_profile import _calibration, _causal_warmup_start
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.engine import DecisionEngineConfig, prepare_horizon_assessment
from financial_dashboard.decision.execution_detect import detect_30m_execution_events
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.opportunity import OpportunityState, assess_opportunity
from financial_dashboard.decision.reaction import assess_reaction
from financial_dashboard.decision.scenario import ScenarioKind, prepare_entry_scenario
from financial_dashboard.decision.st_thesis_identity import (
    STThesisFamily,
    _pullback_candidate,
    _sr_candidate,
)
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection
from financial_dashboard.decision.timing import assess_timing
from financial_dashboard.decision.timeline_cache import load_frozen_decision_timeline


def _value(value) -> str:
    return getattr(value, "value", str(value))


def _lineage(refs) -> tuple[str, ...]:
    values = {
        ref.lineage_id or f"{ref.domain.value}:{ref.timeframe}:{ref.native_id}"
        for ref in refs
    }
    return tuple(sorted(values))


def _candidate_payload(candidate) -> dict | None:
    if candidate is None:
        return None
    return {
        "family": _value(candidate.family),
        "mission": _value(candidate.mission),
        "reason": candidate.reason,
        "anchor_kind": _value(candidate.anchor.kind),
        "anchor_identity": candidate.anchor.identity,
        "anchor_timeframe": candidate.anchor.timeframe,
        "anchor_low": float(candidate.anchor.low),
        "anchor_high": float(candidate.anchor.high),
        "lineage": _lineage(candidate.anchor.source_refs),
    }


def _forward(prices: list[float], index: int, bars: int) -> dict[str, float] | None:
    if index + bars >= len(prices):
        return None
    entry = float(prices[index])
    if entry <= 0:
        return None
    future = [float(value) for value in prices[index + 1 : index + 1 + bars]]
    if not future:
        return None
    return {
        "ret": (future[-1] / entry - 1.0) * 100.0,
        "mfe": (max(future) / entry - 1.0) * 100.0,
        "mae": (min(future) / entry - 1.0) * 100.0,
    }


def _aggregate(rows: list[dict], predicate) -> dict[str, float | int | None]:
    selected = [row for row in rows if predicate(row)]
    result: dict[str, float | int | None] = {"count": len(selected)}
    for bars in (3, 6):
        outcomes = [row[f"forward_{bars}"] for row in selected if row[f"forward_{bars}"] is not None]
        if not outcomes:
            result.update(
                {
                    f"positive_rate_{bars}": None,
                    f"median_ret_{bars}": None,
                    f"median_mfe_{bars}": None,
                    f"median_mae_{bars}": None,
                }
            )
            continue
        result.update(
            {
                f"positive_rate_{bars}": sum(item["ret"] > 0 for item in outcomes) / len(outcomes),
                f"median_ret_{bars}": median(item["ret"] for item in outcomes),
                f"median_mfe_{bars}": median(item["mfe"] for item in outcomes),
                f"median_mae_{bars}": median(item["mae"] for item in outcomes),
            }
        )
    return result


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _print_stats(name: str, stats: dict) -> None:
    print(
        f"{name:<38} n={stats['count']:>4} | "
        f"3b pos={_fmt(stats['positive_rate_3'])} ret={_fmt(stats['median_ret_3'])}% "
        f"MFE={_fmt(stats['median_mfe_3'])}% MAE={_fmt(stats['median_mae_3'])}% | "
        f"6b pos={_fmt(stats['positive_rate_6'])} ret={_fmt(stats['median_ret_6'])}% "
        f"MFE={_fmt(stats['median_mfe_6'])}% MAE={_fmt(stats['median_mae_6'])}%"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnostic-only pre-entry ST economic-thesis study. It reuses the existing canonical "
            "ST thesis candidate functions before BUY and measures whether they identify useful "
            "economic setups, especially while Structure is SHORT + TRANSITION_UP."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
    parser.add_argument("--auto-calibration", action="store_true")
    parser.add_argument("--opportunity-calibration", type=Path, default=None)
    parser.add_argument("--opportunity-none-max-atr", type=float, default=None)
    parser.add_argument("--opportunity-compressed-max-atr", type=float, default=None)
    parser.add_argument("--opportunity-moderate-max-atr", type=float, default=None)
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
    calibration, calibration_source = _calibration(
        args,
        cache_root=args.cache_root,
        symbol=symbol,
    )
    engine_config = DecisionEngineConfig(opportunity_calibration=calibration)
    frozen = load_frozen_decision_timeline(store, symbol, config=history_config)
    snapshots = list(frozen.replay.snapshots)
    entry_events, exit_events = detect_30m_execution_events(snapshots)
    prices = [float(snapshot.current_price) for snapshot in snapshots]

    rows: list[dict] = []
    episode_id = 0
    in_transition_up = False
    episode_first: dict[tuple[int, str], dict] = {}

    for index, snapshot in enumerate(snapshots):
        prepared = prepare_horizon_assessment(
            snapshot,
            DecisionHorizon.SHORT_TERM,
            config=engine_config,
        )
        production_scenario = prepare_entry_scenario(
            snapshot,
            DecisionHorizon.SHORT_TERM,
            config=engine_config,
        ).scenario
        structural = prepared.structural
        native_state = str(structural.native_state).strip().upper()
        transition_up = (
            structural.direction is StructuralDirection.SHORT
            and native_state in {"TRANSITION_UP", "STATE_TRANSITION_UP"}
        )
        if transition_up and not in_transition_up:
            episode_id += 1
        in_transition_up = transition_up

        # Reuse production's existing thesis primitives. The prospective pullback probe
        # intentionally supplies only ScenarioKind.CONTINUATION so we can ask whether
        # the native 1h buyer-regain evidence exists even when production scenario
        # presence is currently ABSENT due to SHORT Structure. It is diagnostic only
        # and never enters eligibility/action code.
        sr_candidate = _sr_candidate(snapshot)
        production_pullback = _pullback_candidate(snapshot, production_scenario)
        prospective_pullback = _pullback_candidate(
            snapshot,
            SimpleNamespace(kind=ScenarioKind.CONTINUATION),
        )

        prospective_candidates = tuple(
            candidate
            for candidate in (sr_candidate, prospective_pullback)
            if candidate is not None
        )
        prospective_families = tuple(sorted({_value(candidate.family) for candidate in prospective_candidates}))
        if len(prospective_candidates) == 1 and len(prospective_families) == 1:
            thesis_state = "SUPPORTED"
            thesis_family = prospective_families[0]
        elif not prospective_candidates:
            thesis_state = "ABSENT"
            thesis_family = STThesisFamily.UNRESOLVED.value
        else:
            thesis_state = "AMBIGUOUS"
            thesis_family = STThesisFamily.UNRESOLVED.value

        candidate_refs = tuple(
            ref
            for candidate in prospective_candidates
            for ref in candidate.anchor.source_refs
        )
        thesis_lineage = set(_lineage(candidate_refs))

        event = entry_events.get(snapshot.as_of)
        event_lineage = set(() if event is None else _lineage(event.source_refs))
        lineage_overlap = tuple(sorted(thesis_lineage & event_lineage))

        long_opportunity = assess_opportunity(
            StructuralDirection.LONG,
            snapshot.targeting,
            calibration=calibration,
        )
        timing_reaction = assess_reaction(
            StructuralDirection.LONG,
            order_blocks=snapshot.order_block_behavior,
            fvg_engulfing=snapshot.fvg_engulfing_lifecycle,
            timeframes=("30m",),
        )
        prospective_timing = assess_timing(
            DecisionHorizon.SHORT_TERM,
            StructuralDirection.LONG,
            prepared.structural_snapshot.relation,
            reaction=timing_reaction,
            pattern=snapshot.pattern_behavior,
            timeframe="30m",
        )

        row = {
            "index": index,
            "episode_id": episode_id if transition_up else None,
            "as_of": str(snapshot.as_of),
            "price": float(snapshot.current_price),
            "transition_up": transition_up,
            "st_direction": _value(structural.direction),
            "st_thesis_state": _value(structural.thesis_state),
            "st_native_state": structural.native_state,
            "transition_target": _value(getattr(structural, "transition_target", None)),
            "production_presence": _value(production_scenario.presence),
            "production_stage": _value(production_scenario.stage),
            "production_kind": _value(production_scenario.kind),
            "production_target": production_scenario.active_target_identity,
            "preentry_thesis_state": thesis_state,
            "preentry_thesis_family": thesis_family,
            "sr_candidate": _candidate_payload(sr_candidate),
            "production_pullback_candidate": _candidate_payload(production_pullback),
            "prospective_pullback_candidate": _candidate_payload(prospective_pullback),
            "thesis_lineage": tuple(sorted(thesis_lineage)),
            "fresh_long_event": event is not None,
            "execution_lineage": tuple(sorted(event_lineage)),
            "thesis_execution_lineage_overlap": lineage_overlap,
            "opportunity_state": _value(long_opportunity.state),
            "opportunity_room_atr": long_opportunity.room_atr,
            "opportunity_ok": long_opportunity.state in {OpportunityState.MODERATE, OpportunityState.AMPLE},
            "timing_state": _value(prospective_timing.state),
            "timing_effect": _value(prospective_timing.entry_effect),
            "timing_waiting_for": prospective_timing.waiting_for,
            "forward_3": _forward(prices, index, 3),
            "forward_6": _forward(prices, index, 6),
        }
        rows.append(row)

        if transition_up and thesis_state == "SUPPORTED":
            episode_first.setdefault((episode_id, thesis_family), row)

    transition_rows = [row for row in rows if row["transition_up"]]
    fresh_transition_rows = [row for row in transition_rows if row["fresh_long_event"]]
    supported_transition_rows = [row for row in transition_rows if row["preentry_thesis_state"] == "SUPPORTED"]

    family_names = tuple(family.value for family in STThesisFamily if family is not STThesisFamily.UNRESOLVED)
    family_stats = {
        family: _aggregate(
            transition_rows,
            lambda row, family=family: row["preentry_thesis_state"] == "SUPPORTED"
            and row["preentry_thesis_family"] == family,
        )
        for family in family_names
    }
    family_room_stats = {
        family: _aggregate(
            transition_rows,
            lambda row, family=family: row["preentry_thesis_state"] == "SUPPORTED"
            and row["preentry_thesis_family"] == family
            and row["opportunity_ok"],
        )
        for family in family_names
    }
    family_event_stats = {
        family: _aggregate(
            transition_rows,
            lambda row, family=family: row["preentry_thesis_state"] == "SUPPORTED"
            and row["preentry_thesis_family"] == family
            and row["fresh_long_event"],
        )
        for family in family_names
    }

    episode_rows_by_family: dict[str, list[dict]] = defaultdict(list)
    for (_, family), row in episode_first.items():
        episode_rows_by_family[family].append(row)
    episode_stats = {
        family: _aggregate(values, lambda row: True)
        for family, values in episode_rows_by_family.items()
    }

    print("=" * 100)
    print("ST PRE-ENTRY ECONOMIC THESIS DIAGNOSTIC")
    print("=" * 100)
    print(f"SYMBOL\t{symbol}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print(f"FROZEN_CACHE_STATUS\t{frozen.cache_status}")
    print(f"OPPORTUNITY_CALIBRATION\t{calibration_source}")
    print(f"ENTRY_EXECUTION_EVENTS\t{len(entry_events)}")
    print(f"EXIT_EXECUTION_EVENTS\t{len(exit_events)}")
    print(f"TRANSITION_UP_SNAPSHOTS\t{len(transition_rows)}")
    print(f"TRANSITION_UP_EPISODES\t{episode_id}")
    print("TRADING_POLICY_MUTATION\tNONE")
    print("STRUCTURE_MUTATION\tNONE")
    print("QUALIFICATION_MUTATION\tNONE")
    print("PROSPECTIVE_PULLBACK_NOTE\tdiagnostic-only buyer-regain probe; it does not create a production scenario")
    print("OUTCOME_NOTE\tforward returns use decision-snapshot prices; hindsight diagnostic only")

    state_counts = Counter(row["preentry_thesis_state"] for row in transition_rows)
    family_counts = Counter(row["preentry_thesis_family"] for row in supported_transition_rows)
    print("\nTRANSITION_UP PRE-ENTRY THESIS COVERAGE")
    print("---------------------------------------")
    for key, count in sorted(state_counts.items()):
        print(f"{key}\t{count}")
    for key, count in sorted(family_counts.items()):
        print(f"FAMILY_{key}\t{count}")

    print("\nTHESIS FAMILY QUALITY — ALL TRANSITION_UP SNAPSHOTS")
    print("---------------------------------------------------")
    for family in family_names:
        _print_stats(family, family_stats[family])

    print("\nTHESIS FAMILY + PROSPECTIVE LONG ROOM (MODERATE/AMPLE)")
    print("------------------------------------------------------")
    for family in family_names:
        _print_stats(family, family_room_stats[family])

    print("\nTHESIS FAMILY WHEN FRESH LONG EXECUTION EXISTS")
    print("----------------------------------------------")
    for family in family_names:
        _print_stats(family, family_event_stats[family])

    print("\nEPISODE-FIRST SUPPORTED THESIS QUALITY")
    print("--------------------------------------")
    for family in family_names:
        stats = episode_stats.get(family, _aggregate([], lambda row: True))
        _print_stats(family, stats)

    print("\nFRESH LONG EVENTS INSIDE TRANSITION_UP")
    print("--------------------------------------")
    print(f"COUNT\t{len(fresh_transition_rows)}")
    for row in fresh_transition_rows:
        f3 = row["forward_3"]
        f6 = row["forward_6"]
        print(
            f"{row['as_of']} | thesis={row['preentry_thesis_state']}:{row['preentry_thesis_family']} | "
            f"prod={row['production_presence']}/{row['production_stage']} | "
            f"opp={row['opportunity_state']}:{row['opportunity_room_atr']} | "
            f"timing={row['timing_effect']} | lineage_overlap={row['thesis_execution_lineage_overlap'] or 'NONE'} | "
            f"3b={None if f3 is None else round(f3['ret'], 3)}% | "
            f"6b={None if f6 is None else round(f6['ret'], 3)}%"
        )

    print("\nSUPPORTED THESIS + FRESH EVENT + ROOM")
    print("-------------------------------------")
    combined = [
        row
        for row in transition_rows
        if row["preentry_thesis_state"] == "SUPPORTED"
        and row["fresh_long_event"]
        and row["opportunity_ok"]
    ]
    print(f"COUNT\t{len(combined)}")
    for row in combined:
        print(
            f"{row['as_of']} | family={row['preentry_thesis_family']} | "
            f"timing={row['timing_effect']} | prod={row['production_presence']}/{row['production_stage']}"
        )

    overlap_count = sum(bool(row["thesis_execution_lineage_overlap"]) for row in transition_rows)
    print("\nLINEAGE CHECK")
    print("-------------")
    print(f"THESIS_EXECUTION_OVERLAP_SNAPSHOTS\t{overlap_count}")
    print("NOTE\tAny overlap means thesis and fresh execution must not be counted as independent evidence")

    if args.json_out is not None:
        report = {
            "symbol": symbol,
            "snapshots": len(snapshots),
            "transition_up_snapshots": len(transition_rows),
            "transition_up_episodes": episode_id,
            "entry_execution_events": len(entry_events),
            "exit_execution_events": len(exit_events),
            "state_counts": dict(state_counts),
            "family_counts": dict(family_counts),
            "family_stats": family_stats,
            "family_room_stats": family_room_stats,
            "family_event_stats": family_event_stats,
            "episode_stats": episode_stats,
            "transition_rows": transition_rows,
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        print(f"JSON_REPORT\t{args.json_out}")

    print("ST_PREENTRY_THESIS_DIAGNOSTIC_OK")


if __name__ == "__main__":
    main()
