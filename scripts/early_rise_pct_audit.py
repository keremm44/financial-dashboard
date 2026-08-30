from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.canonical_events import canonical_decision_events_from_replay
from financial_dashboard.decision.engine import DecisionEngineConfig, assess_horizon_decision
from financial_dashboard.decision.execution_detect import detect_1h_execution_events
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.lifecycle_replay import replay_canonical_trade_lifecycle
from financial_dashboard.decision.scenario import assess_entry_scenario
from financial_dashboard.decision.stabil_authority import assess_stabil_authority
from financial_dashboard.decision.structural import DecisionHorizon
from financial_dashboard.decision.timeline_cache import DecisionTimelineCacheMiss, load_frozen_decision_timeline
from financial_dashboard.decision_audit.research import detect_large_market_moves

from buy_sell_backtest import _causal_warmup_start


def _enum(value) -> str:
    return str(getattr(value, "value", value))


def _compact(values, limit: int = 4) -> str:
    values = tuple(str(value) for value in values if value)
    return "-" if not values else "; ".join(values[:limit])


def _snapshot_at_or_after(snapshots, start, end, threshold_price):
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    for snapshot in snapshots:
        ts = pd.Timestamp(snapshot.as_of)
        if ts < start_ts:
            continue
        if ts > end_ts:
            break
        price = getattr(snapshot, "current_price", None)
        if price is not None and float(price) >= float(threshold_price):
            return snapshot
    return None


def _event_at(decisions_by_time, timestamp):
    return decisions_by_time.get(pd.Timestamp(timestamp))


def _print_checkpoint(snapshot, event, *, threshold_pct, threshold_price, config):
    st = assess_horizon_decision(
        snapshot,
        DecisionHorizon.SHORT_TERM,
        config=config,
        execution_event=None,
    )
    st_scenario = assess_entry_scenario(
        snapshot,
        DecisionHorizon.SHORT_TERM,
        config=config,
        assessment=st,
    )
    lt = assess_horizon_decision(
        snapshot,
        DecisionHorizon.LONG_TERM,
        config=config,
        execution_event=None,
    )
    lt_scenario = assess_entry_scenario(
        snapshot,
        DecisionHorizon.LONG_TERM,
        config=config,
        assessment=lt,
    )
    stabil = assess_stabil_authority(getattr(snapshot, "stabil_support", None))

    action = "-" if event is None else _enum(event.action)
    waiting = () if event is None else tuple(event.waiting_for)
    blockers = () if event is None else tuple(event.blockers)
    reasons = () if event is None else tuple(event.reasons)

    print(
        f"  +{threshold_pct:.0f}% first_seen={snapshot.as_of} "
        f"price={float(snapshot.current_price):.2f} threshold={threshold_price:.2f} action={action}"
    )
    print(
        "    ST "
        f"structure={_enum(st.structural.direction)}/{_enum(st.structural.thesis_state)} "
        f"scenario={_enum(st_scenario.presence)}/{_enum(st_scenario.stage)}/{_enum(st_scenario.kind)} "
        f"timing={_enum(st.timing.state)} opportunity={_enum(st.opportunity.state)} "
        f"eligibility={_enum(st.eligibility.state)} conflict={_enum(st.conflict.state)}"
    )
    print(
        "    STABIL "
        f"state={_enum(stabil.state)} quality={_enum(stabil.data_quality)}"
    )
    print(
        "    LT "
        f"structure={_enum(lt.structural.direction)}/{_enum(lt.structural.thesis_state)} "
        f"scenario={_enum(lt_scenario.presence)}/{_enum(lt_scenario.stage)}/{_enum(lt_scenario.kind)}"
    )
    print(f"    waiting={_compact(waiting)}")
    print(f"    blockers={_compact(blockers)}")
    print(f"    reasons={_compact(reasons)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Find price-only 4H rises, rank them from largest to smallest, then report what "
            "the frozen causal decision system said at each first +1%, +2%, +3%... checkpoint."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--min-move-pct", type=float, default=7.0)
    parser.add_argument("--reversal-pct", type=float, default=5.0)
    parser.add_argument("--checkpoint-step-pct", type=float, default=1.0)
    parser.add_argument("--from-move", type=int, default=1)
    parser.add_argument("--to-move", type=int, default=None)
    args = parser.parse_args()

    if args.min_move_pct <= 0.0:
        raise SystemExit("--min-move-pct must be > 0")
    if args.reversal_pct <= 0.0:
        raise SystemExit("--reversal-pct must be > 0")
    if args.checkpoint_step_pct <= 0.0:
        raise SystemExit("--checkpoint-step-pct must be > 0")
    if args.from_move < 1:
        raise SystemExit("--from-move must be >= 1")
    if args.to_move is not None and args.to_move < args.from_move:
        raise SystemExit("--to-move must be >= --from-move")

    store = ParquetOHLCVStore(args.cache_root)
    symbol = normalize_symbol(args.symbol)
    effective_start = _causal_warmup_start(store, symbol=symbol, requested_start=args.start)
    history_config = HistoricalDecisionInputConfig(start_at=effective_start, end_at=args.end)

    try:
        frozen = load_frozen_decision_timeline(store, symbol, config=history_config)
    except DecisionTimelineCacheMiss as exc:
        raise SystemExit(
            "FROZEN_DECISION_TIMELINE_CACHE_MISS\n"
            "This audit never replays domains; build the frozen timeline first."
        ) from exc

    snapshots = tuple(sorted(frozen.replay.snapshots, key=lambda item: pd.Timestamp(item.as_of)))
    if not snapshots:
        raise SystemExit("Frozen historical DecisionInput timeline contains no causal snapshots")

    entry_events, exit_events = detect_1h_execution_events(snapshots)
    decision_config = DecisionEngineConfig()
    lifecycle = replay_canonical_trade_lifecycle(
        snapshots,
        config=decision_config,
        entry_execution_events=entry_events,
        exit_execution_events=exit_events,
    )
    decisions = canonical_decision_events_from_replay(lifecycle)
    decisions_by_time = {pd.Timestamp(event.timestamp): event for event in decisions}

    bars_4h = store.load(symbol, "4h")
    if bars_4h.empty:
        raise SystemExit("4H bars are required")

    detected_moves = tuple(
        move
        for move in detect_large_market_moves(
            bars_4h,
            min_move_pct=float(args.min_move_pct),
            reversal_pct=float(args.reversal_pct),
        )
        if move.direction == "UP"
    )
    ranked_moves = tuple(
        sorted(
            detected_moves,
            key=lambda move: (-float(move.move_pct), pd.Timestamp(move.start_time)),
        )
    )

    first_index = args.from_move - 1
    last_index = len(ranked_moves) if args.to_move is None else min(args.to_move, len(ranked_moves))
    selected = tuple(enumerate(ranked_moves[first_index:last_index], start=args.from_move))

    print("EARLY RISE PERCENT AUDIT")
    print("========================")
    print(f"CACHE\t{frozen.cache_status}")
    print("DOMAIN_REPLAY\tNOT_RUN")
    print(f"SYMBOL\t{symbol}")
    print(f"MOVE_RULE\tprice-only 4H, min=+{args.min_move_pct:g}%, reversal={args.reversal_pct:g}%")
    print("MOVE_ORDER\tlargest rise to smallest rise")
    print(f"CHECKPOINTS\tfirst causal snapshot at every +{args.checkpoint_step_pct:g}% from move start")
    print(f"UP_MOVES_TOTAL\t{len(ranked_moves)}")
    if selected:
        print(f"SHOWING\tMOVE #{selected[0][0]} .. MOVE #{selected[-1][0]}")
    else:
        print("SHOWING\tNONE")
    print()

    for rank, move in selected:
        print(
            f"MOVE #{rank}  {move.start_time} -> {move.end_time} | "
            f"{move.start_price:.2f} -> {move.end_price:.2f} | {move.move_pct:+.2f}%"
        )
        threshold = float(args.checkpoint_step_pct)
        rendered = 0
        while threshold <= float(move.move_pct) + 1e-9:
            threshold_price = float(move.start_price) * (1.0 + threshold / 100.0)
            snapshot = _snapshot_at_or_after(
                snapshots,
                move.start_time,
                move.end_time,
                threshold_price,
            )
            if snapshot is None:
                print(f"  +{threshold:.0f}% NOT_SEEN_IN_CAUSAL_SNAPSHOTS threshold={threshold_price:.2f}")
            else:
                event = _event_at(decisions_by_time, snapshot.as_of)
                _print_checkpoint(
                    snapshot,
                    event,
                    threshold_pct=threshold,
                    threshold_price=threshold_price,
                    config=decision_config,
                )
            rendered += 1
            threshold += float(args.checkpoint_step_pct)
        if rendered == 0:
            print("  no percentage checkpoint rendered")
        print()

    print("EARLY_RISE_PERCENT_AUDIT_OK")


if __name__ == "__main__":
    main()
