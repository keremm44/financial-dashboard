from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from statistics import median

from entry_reason_profile import _calibration, _causal_warmup_start
from st_entry_bottleneck_audit import _st_permission_axes
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.engine import DecisionEngineConfig, prepare_horizon_assessment
from financial_dashboard.decision.execution_detect import detect_30m_execution_events
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.horizon_profile import horizon_evaluation_profile
from financial_dashboard.decision.opportunity import OpportunityState, assess_opportunity
from financial_dashboard.decision.participation import ParticipationState, assess_participation
from financial_dashboard.decision.reaction import ReactionState, assess_reaction
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection
from financial_dashboard.decision.timeline_cache import load_frozen_decision_timeline


CANDIDATE_FAMILIES = (
    "REACTION_LONG",
    "REACTION_30M_LONG",
    "PARTICIPATION_1H_LONG",
    "PATTERN_30M_LONG",
    "CONTEXT_REACTION_UP",
    "CONTEXT_REVERSAL_UP",
)

# These pairs are semantically related/reused evidence and should not be interpreted
# as two independent confirmations even if both are true on the same snapshot.
DEPENDENT_PAIRS = {
    tuple(sorted(("REACTION_LONG", "REACTION_30M_LONG"))),
    tuple(sorted(("REACTION_LONG", "CONTEXT_REACTION_UP"))),
    tuple(sorted(("REACTION_30M_LONG", "CONTEXT_REACTION_UP"))),
}


def _value(value) -> str:
    return getattr(value, "value", str(value))


def _forward(snapshot_prices: list[float], index: int, bars: int) -> dict[str, float] | None:
    if index + bars >= len(snapshot_prices):
        return None
    entry = float(snapshot_prices[index])
    future = [float(value) for value in snapshot_prices[index + 1 : index + 1 + bars]]
    if not future or entry <= 0:
        return None
    ret = (future[-1] / entry - 1.0) * 100.0
    mfe = (max(future) / entry - 1.0) * 100.0
    mae = (min(future) / entry - 1.0) * 100.0
    return {"ret": ret, "mfe": mfe, "mae": mae}


def _pattern_long(snapshot) -> tuple[bool, bool, str]:
    projection = getattr(snapshot, "pattern_behavior", None)
    if projection is None:
        return False, False, "UNAVAILABLE"
    try:
        row = projection.for_timeframe("30m")
    except KeyError:
        return False, False, "UNAVAILABLE"
    phase = _value(getattr(row, "phase", "UNAVAILABLE"))
    direction = int(getattr(row, "classic_direction", 0) or 0)
    long_forming = direction > 0 and phase in {
        "FORMING",
        "MATURE_COMPRESSION",
        "BREAK_ATTEMPT",
        "BREAK_CONFIRMING",
        "POST_BREAK_RETEST",
        "BREAK_CONFIRMED",
        "RETEST_HELD",
    }
    long_confirmed = direction > 0 and phase in {"BREAK_CONFIRMED", "RETEST_HELD"}
    return long_forming, long_confirmed, phase


def _context_long_signals(snapshot) -> tuple[bool, bool, dict[str, str]]:
    axes = _st_permission_axes(snapshot)
    reaction = _value(axes.reaction)
    reaction_direction = _value(axes.reaction_direction)
    reversal = _value(axes.reversal)
    reversal_direction = _value(axes.reversal_direction)
    reaction_up = reaction in {"DEVELOPING", "ACTIVE"} and reaction_direction == "UP"
    reversal_up = reversal in {"CANDIDATE", "STRUCTURALLY_CONFIRMED"} and reversal_direction == "UP"
    return reaction_up, reversal_up, {
        "reaction": reaction,
        "reaction_direction": reaction_direction,
        "reversal": reversal,
        "reversal_direction": reversal_direction,
        "participation": _value(axes.participation),
        "pattern_readiness": _value(axes.pattern_readiness),
        "conflict": _value(axes.conflict),
    }


def _aggregate(rows: list[dict], predicate) -> dict[str, float | int | None]:
    selected = [row for row in rows if predicate(row)]
    result: dict[str, float | int | None] = {"count": len(selected)}
    for bars in (3, 6):
        outcomes = [row[f"forward_{bars}"] for row in selected if row[f"forward_{bars}"] is not None]
        if not outcomes:
            result.update({
                f"positive_rate_{bars}": None,
                f"median_ret_{bars}": None,
                f"median_mfe_{bars}": None,
                f"median_mae_{bars}": None,
            })
            continue
        result.update({
            f"positive_rate_{bars}": sum(item["ret"] > 0 for item in outcomes) / len(outcomes),
            f"median_ret_{bars}": median(item["ret"] for item in outcomes),
            f"median_mfe_{bars}": median(item["mfe"] for item in outcomes),
            f"median_mae_{bars}": median(item["mae"] for item in outcomes),
        })
    return result


def _print_stats(name: str, stats: dict[str, float | int | None], *, note: str = "") -> None:
    def fmt(value):
        if value is None:
            return "n/a"
        if isinstance(value, float):
            return f"{value:.3f}"
        return str(value)

    print(
        f"{name:<48} n={stats['count']:>4} | "
        f"3b pos={fmt(stats['positive_rate_3'])} ret={fmt(stats['median_ret_3'])}% "
        f"MFE={fmt(stats['median_mfe_3'])}% MAE={fmt(stats['median_mae_3'])}% | "
        f"6b pos={fmt(stats['positive_rate_6'])} ret={fmt(stats['median_ret_6'])}% "
        f"MFE={fmt(stats['median_mfe_6'])}% MAE={fmt(stats['median_mae_6'])}%"
        + (f" | {note}" if note else "")
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnostic-only Transition-Up evidence study. It measures which existing lower-timeframe "
            "domains add useful early-LONG information while Structure remains SHORT."
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
    opportunity_calibration, calibration_source = _calibration(
        args,
        cache_root=args.cache_root,
        symbol=symbol,
    )
    engine_config = DecisionEngineConfig(opportunity_calibration=opportunity_calibration)
    frozen = load_frozen_decision_timeline(store, symbol, config=history_config)
    snapshots = list(frozen.replay.snapshots)
    entry_events, exit_events = detect_30m_execution_events(snapshots)
    prices = [float(snapshot.current_price) for snapshot in snapshots]
    profile = horizon_evaluation_profile(DecisionHorizon.SHORT_TERM)

    rows: list[dict] = []
    episode_id = 0
    in_episode = False
    episode_first_rows: dict[tuple[int, str], dict] = {}

    for index, snapshot in enumerate(snapshots):
        prepared = prepare_horizon_assessment(
            snapshot,
            DecisionHorizon.SHORT_TERM,
            config=engine_config,
        )
        structural = prepared.structural
        is_transition_up = (
            structural.direction is StructuralDirection.SHORT
            and str(structural.native_state).strip().upper() in {"TRANSITION_UP", "STATE_TRANSITION_UP"}
        )
        if not is_transition_up:
            in_episode = False
            continue
        if not in_episode:
            episode_id += 1
            in_episode = True

        long_reaction = assess_reaction(
            StructuralDirection.LONG,
            order_blocks=snapshot.order_block_behavior,
            fvg_engulfing=snapshot.fvg_engulfing_lifecycle,
            timeframes=profile.reaction_timeframes,
        )
        long_reaction_30m = assess_reaction(
            StructuralDirection.LONG,
            order_blocks=snapshot.order_block_behavior,
            fvg_engulfing=snapshot.fvg_engulfing_lifecycle,
            timeframes=(profile.timing_timeframe,),
        )
        long_participation = assess_participation(
            StructuralDirection.LONG,
            snapshot.participation_behavior,
            timeframe=profile.participation_timeframe,
        )
        long_opportunity = assess_opportunity(
            StructuralDirection.LONG,
            snapshot.targeting,
            calibration=opportunity_calibration,
        )
        pattern_long, pattern_confirmed, pattern_phase = _pattern_long(snapshot)
        context_reaction_up, context_reversal_up, context_detail = _context_long_signals(snapshot)
        event = entry_events.get(snapshot.as_of)
        fresh_long_event = bool(event is not None and _value(event.side) == "LONG")

        signals = {
            "REACTION_LONG": long_reaction.state in {ReactionState.CONFIRMED, ReactionState.DEVELOPING},
            "REACTION_30M_LONG": long_reaction_30m.state in {ReactionState.CONFIRMED, ReactionState.DEVELOPING},
            "PARTICIPATION_1H_LONG": long_participation.state is ParticipationState.SUPPORTIVE,
            "PATTERN_30M_LONG": pattern_long,
            "CONTEXT_REACTION_UP": context_reaction_up,
            "CONTEXT_REVERSAL_UP": context_reversal_up,
        }
        opportunity_ok = long_opportunity.state in {OpportunityState.AMPLE, OpportunityState.MODERATE}
        row = {
            "index": index,
            "episode_id": episode_id,
            "as_of": str(snapshot.as_of),
            "price": float(snapshot.current_price),
            "st_direction": _value(structural.direction),
            "st_thesis": _value(structural.thesis_state),
            "st_native_state": structural.native_state,
            "relation": _value(prepared.structural_snapshot.relation),
            "fresh_long_event": fresh_long_event,
            "opportunity_ok": opportunity_ok,
            "opportunity_state": _value(long_opportunity.state),
            "opportunity_room_atr": long_opportunity.room_atr,
            "signals": signals,
            "reaction_state": _value(long_reaction.state),
            "reaction_30m_state": _value(long_reaction_30m.state),
            "participation_state": _value(long_participation.state),
            "pattern_phase": pattern_phase,
            "pattern_confirmed": pattern_confirmed,
            "context": context_detail,
            "forward_3": _forward(prices, index, 3),
            "forward_6": _forward(prices, index, 6),
        }
        rows.append(row)
        for family, active in signals.items():
            if active:
                episode_first_rows.setdefault((episode_id, family), row)
        for left, right in combinations(CANDIDATE_FAMILIES, 2):
            if signals[left] and signals[right]:
                key = f"{left}+{right}"
                episode_first_rows.setdefault((episode_id, key), row)

    signal_stats = {family: _aggregate(rows, lambda row, family=family: row["signals"][family]) for family in CANDIDATE_FAMILIES}
    pair_stats: dict[str, dict] = {}
    for left, right in combinations(CANDIDATE_FAMILIES, 2):
        key = f"{left}+{right}"
        pair_stats[key] = _aggregate(rows, lambda row, l=left, r=right: row["signals"][l] and row["signals"][r])

    opportunity_filtered_stats = {
        family: _aggregate(
            rows,
            lambda row, family=family: row["opportunity_ok"] and row["signals"][family],
        )
        for family in CANDIDATE_FAMILIES
    }
    fresh_stats = {
        family: _aggregate(
            rows,
            lambda row, family=family: row["fresh_long_event"] and row["signals"][family],
        )
        for family in CANDIDATE_FAMILIES
    }

    episode_rows_by_key: dict[str, list[dict]] = defaultdict(list)
    for (_, key), row in episode_first_rows.items():
        episode_rows_by_key[key].append(row)
    episode_stats = {
        key: _aggregate(values, lambda row: True)
        for key, values in episode_rows_by_key.items()
    }

    print("=" * 96)
    print("ST TRANSITION-UP DOMAIN EVIDENCE DIAGNOSTIC")
    print("=" * 96)
    print(f"SYMBOL\t{symbol}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print(f"TRANSITION_UP_SHORT_SNAPSHOTS\t{len(rows)}")
    print(f"TRANSITION_UP_EPISODES\t{episode_id}")
    print(f"ENTRY_EXECUTION_EVENTS\t{len(entry_events)}")
    print(f"EXIT_EXECUTION_EVENTS\t{len(exit_events)}")
    print(f"OPPORTUNITY_CALIBRATION\t{calibration_source}")
    print("TRADING_POLICY_MUTATION\tNONE")
    print("STRUCTURE_MUTATION\tNONE")
    print("DOMAIN_MUTATION\tNONE")
    print("OUTCOME_NOTE\tforward returns use decision-snapshot prices; diagnostic only")

    print("\nSINGLE DOMAIN SIGNAL QUALITY — ALL TRANSITION_UP SNAPSHOTS")
    print("---------------------------------------------------------")
    for family in CANDIDATE_FAMILIES:
        _print_stats(family, signal_stats[family])

    print("\nSINGLE DOMAIN + ECONOMIC ROOM (MODERATE/AMPLE)")
    print("----------------------------------------------")
    for family in CANDIDATE_FAMILIES:
        _print_stats(family, opportunity_filtered_stats[family])

    print("\nSINGLE DOMAIN WHEN FRESH LONG EXECUTION EXISTS")
    print("----------------------------------------------")
    for family in CANDIDATE_FAMILIES:
        _print_stats(family, fresh_stats[family])

    print("\nPAIR QUALITY — INDEPENDENCE-AWARE")
    print("---------------------------------")
    ranked_pairs = sorted(
        pair_stats.items(),
        key=lambda item: (
            -(item[1].get("count") or 0),
            -(item[1].get("positive_rate_6") or -1.0),
        ),
    )
    for key, stats in ranked_pairs:
        left, right = key.split("+")
        dependent = tuple(sorted((left, right))) in DEPENDENT_PAIRS
        note = "DEPENDENT/OVERLAPPING EVIDENCE — DO NOT COUNT AS 2" if dependent else "independent candidate pair"
        _print_stats(key, stats, note=note)

    print("\nEPISODE-FIRST SIGNAL QUALITY")
    print("----------------------------")
    for family in CANDIDATE_FAMILIES:
        stats = episode_stats.get(family, {"count": 0, **{f"positive_rate_{b}": None for b in (3, 6)}, **{f"median_ret_{b}": None for b in (3, 6)}, **{f"median_mfe_{b}": None for b in (3, 6)}, **{f"median_mae_{b}": None for b in (3, 6)}})
        _print_stats(family, stats)

    print("\nFRESH LONG EVENTS INSIDE TRANSITION_UP")
    print("--------------------------------------")
    fresh_rows = [row for row in rows if row["fresh_long_event"]]
    print(f"COUNT\t{len(fresh_rows)}")
    for row in fresh_rows:
        active = [name for name, value in row["signals"].items() if value]
        f3 = row["forward_3"]
        f6 = row["forward_6"]
        print(
            f"{row['as_of']} | opp={row['opportunity_state']}:{row['opportunity_room_atr']} | "
            f"signals={','.join(active) if active else 'NONE'} | "
            f"3b={None if f3 is None else round(f3['ret'], 3)}% | "
            f"6b={None if f6 is None else round(f6['ret'], 3)}%"
        )

    if args.json_out is not None:
        report = {
            "symbol": symbol,
            "snapshots": len(snapshots),
            "transition_up_short_snapshots": len(rows),
            "transition_up_episodes": episode_id,
            "entry_execution_events": len(entry_events),
            "exit_execution_events": len(exit_events),
            "signal_stats": signal_stats,
            "opportunity_filtered_stats": opportunity_filtered_stats,
            "fresh_stats": fresh_stats,
            "pair_stats": pair_stats,
            "episode_stats": episode_stats,
            "dependent_pairs": [list(pair) for pair in sorted(DEPENDENT_PAIRS)],
            "fresh_rows": fresh_rows,
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"JSON_REPORT\t{args.json_out}")

    print("ST_TRANSITION_UP_DOMAIN_DIAGNOSTIC_OK")


if __name__ == "__main__":
    main()
