from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.calibration import load_opportunity_calibration
from financial_dashboard.decision.canonical_events import canonical_decision_events_from_replay
from financial_dashboard.decision.engine import (
    DecisionEngineConfig,
    _decision_structure_projection,
    _execution_channel_quality,
)
from financial_dashboard.decision.execution_detect import detect_1h_execution_events
from financial_dashboard.decision.exit import _short_term_position_exit, refine_short_term_exit_with_stabil
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.lifecycle_replay import replay_canonical_trade_lifecycle
from financial_dashboard.decision.stabil_authority import assess_stabil_authority
from financial_dashboard.decision.structural import build_horizon_structural_snapshot
from financial_dashboard.decision.timeline_cache import DecisionTimelineCacheMiss, load_frozen_decision_timeline
from financial_dashboard.decision.trade_exit import assess_long_exit_execution, exit_click_event
from financial_dashboard.decision_audit.research import detect_large_market_moves


@dataclass(frozen=True, slots=True)
class ExitRow:
    as_of: pd.Timestamp
    price: float
    stage: str
    health: str
    execution: str
    hypothetical_action: str
    structural_reasons: tuple[str, ...]
    waiting_for: tuple[str, ...]
    st_structure: str
    lt_structure: str
    stabil: str
    real_action: str
    exit_event: bool


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


def _nearest_snapshot_index(snapshots, target) -> int:
    target_ts = pd.Timestamp(target)
    return min(
        range(len(snapshots)),
        key=lambda i: abs(pd.Timestamp(snapshots[i].as_of) - target_ts),
    )


def _pct_from_peak(peak_price: float, price: float) -> float:
    return (float(price) / float(peak_price) - 1.0) * 100.0


def _hours_from_peak(peak_time, as_of) -> float:
    return (pd.Timestamp(as_of) - pd.Timestamp(peak_time)).total_seconds() / 3600.0


def _first(rows, predicate):
    return next((row for row in rows if predicate(row)), None)


def _format_event(row, hours) -> str:
    if row is None:
        return "-"
    return f"{row.as_of}@{row.price:.2f} ({hours:+.1f}h)"


def _hypothetical_st_exit_row(snapshot, *, exit_event, real_action: str) -> ExitRow:
    structural_snapshot = build_horizon_structural_snapshot(
        _decision_structure_projection(snapshot.structure)
    )
    structural = _short_term_position_exit(structural_snapshot)
    stabil = assess_stabil_authority(getattr(snapshot, "stabil_support", None))
    structural = refine_short_term_exit_with_stabil(
        structural,
        structural_snapshot.short_term,
        stabil,
    )
    click = exit_click_event(exit_event)
    channel_available = _execution_channel_quality(snapshot, "1h") is ContextDataQuality.VALID
    armed = _token(structural.stage) == "EXIT_READY"
    execution = assess_long_exit_execution(
        structural,
        as_of=snapshot.as_of,
        event=click if armed else None,
        execution_timeframe="1h",
        channel_available=channel_available,
    )
    action = "SELL" if _token(execution.state) == "CONFIRMED" else "HOLD"
    st = structural_snapshot.short_term
    lt = structural_snapshot.long_term
    return ExitRow(
        as_of=pd.Timestamp(snapshot.as_of),
        price=float(snapshot.current_price),
        stage=_token(structural.stage),
        health=_token(structural.position_health),
        execution=_token(execution.state),
        hypothetical_action=action,
        structural_reasons=tuple(structural.reasons),
        waiting_for=tuple(dict.fromkeys((*structural.waiting_for, *execution.waiting_for))),
        st_structure=f"{_token(st.direction)}/{_token(st.thesis_state)}",
        lt_structure=f"{_token(lt.direction)}/{_token(lt.thesis_state)}",
        stabil=_token(stabil.state),
        real_action=real_action,
        exit_event=exit_event is not None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only SELL audit around the peaks of the largest price-only 4H rises. "
            "It evaluates current short-term exit behavior as if a long position existed."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--min-move-pct", type=float, default=7.0)
    parser.add_argument("--reversal-pct", type=float, default=5.0)
    parser.add_argument("--pre-bars", type=int, default=6)
    parser.add_argument("--post-bars", type=int, default=40)
    parser.add_argument("--detail-drop-pct", type=float, default=5.0)
    parser.add_argument("--detail-giveback-pct", type=float, default=3.0)
    args = parser.parse_args()

    if args.top < 1 or args.pre_bars < 0 or args.post_bars < 1:
        raise SystemExit("Invalid --top/--pre-bars/--post-bars values")

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
    entry_events, exit_events = detect_1h_execution_events(snapshots)
    lifecycle = replay_canonical_trade_lifecycle(
        snapshots,
        config=config,
        entry_execution_events=entry_events,
        exit_execution_events=exit_events,
    )
    decisions = canonical_decision_events_from_replay(lifecycle)
    decisions_by_time = {pd.Timestamp(event.timestamp): event for event in decisions}

    bars_4h = store.load(symbol, "4h")
    if bars_4h.empty:
        raise SystemExit("4H bars are required")
    moves = tuple(
        sorted(
            (
                move
                for move in detect_large_market_moves(
                    bars_4h,
                    min_move_pct=float(args.min_move_pct),
                    reversal_pct=float(args.reversal_pct),
                )
                if move.direction == "UP"
            ),
            key=lambda move: (-float(move.move_pct), pd.Timestamp(move.start_time)),
        )[: args.top]
    )

    print("TOP-20 SELL BEHAVIOR AUDIT")
    print("==========================")
    print(f"SYMBOL\t{symbol}")
    print(f"FROZEN_CACHE\t{frozen.cache_status}")
    print("DOMAIN_REPLAY\tNOT_RUN")
    print(f"CALIBRATION\t{calibration_path}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print(f"WINDOW\tpre={args.pre_bars} hourly rows; post={args.post_bars} hourly rows")
    print()

    summaries = []
    detailed = []

    for rank, move in enumerate(moves, start=1):
        anchor = _nearest_snapshot_index(snapshots, move.end_time)
        start_i = max(0, anchor - args.pre_bars)
        end_i = min(len(snapshots), anchor + args.post_bars + 1)
        rows: list[ExitRow] = []
        for snapshot in snapshots[start_i:end_i]:
            as_of = pd.Timestamp(snapshot.as_of)
            event = decisions_by_time.get(as_of)
            real_action = "-" if event is None else _token(event.action)
            rows.append(
                _hypothetical_st_exit_row(
                    snapshot,
                    exit_event=exit_events.get(as_of),
                    real_action=real_action,
                )
            )

        peak_time = pd.Timestamp(move.end_time)
        peak_price = float(move.end_price)
        first_watch = _first(rows, lambda row: row.stage == "EXIT_WATCH")
        first_ready = _first(rows, lambda row: row.stage == "EXIT_READY")
        first_sell = _first(rows, lambda row: row.hypothetical_action == "SELL")
        real_sell = _first(rows, lambda row: row.real_action == "SELL")
        post_rows = [row for row in rows if row.as_of >= peak_time] or rows[args.pre_bars:]
        min_post = min((row.price for row in post_rows), default=peak_price)
        max_post = max((row.price for row in post_rows), default=peak_price)
        max_drop = max(0.0, -_pct_from_peak(peak_price, min_post))
        post_retake_pct = max(0.0, _pct_from_peak(peak_price, max_post))
        sell_giveback = None if first_sell is None else max(0.0, -_pct_from_peak(peak_price, first_sell.price))
        watch_hours = None if first_watch is None else _hours_from_peak(peak_time, first_watch.as_of)
        ready_hours = None if first_ready is None else _hours_from_peak(peak_time, first_ready.as_of)
        sell_hours = None if first_sell is None else _hours_from_peak(peak_time, first_sell.as_of)

        item = {
            "rank": rank,
            "move_pct": float(move.move_pct),
            "peak_time": peak_time,
            "peak_price": peak_price,
            "first_watch": first_watch,
            "first_ready": first_ready,
            "first_sell": first_sell,
            "real_sell": real_sell,
            "watch_hours": watch_hours,
            "ready_hours": ready_hours,
            "sell_hours": sell_hours,
            "sell_giveback": sell_giveback,
            "max_drop": max_drop,
            "post_retake_pct": post_retake_pct,
            "rows": rows,
        }
        summaries.append(item)
        if max_drop >= args.detail_drop_pct or (
            sell_giveback is not None and sell_giveback >= args.detail_giveback_pct
        ):
            detailed.append(item)

    print("SUMMARY")
    print("-------")
    for item in summaries:
        watch_text = _format_event(item["first_watch"], item["watch_hours"]) if item["first_watch"] else "-"
        ready_text = _format_event(item["first_ready"], item["ready_hours"]) if item["first_ready"] else "-"
        sell_text = _format_event(item["first_sell"], item["sell_hours"]) if item["first_sell"] else "-"
        real_sell = item["real_sell"]
        real_sell_text = "-" if real_sell is None else f"{real_sell.as_of}@{real_sell.price:.2f}"
        giveback_text = "-" if item["sell_giveback"] is None else f"{item['sell_giveback']:.2f}%"
        print(
            f"#{item['rank']:02d} move=+{item['move_pct']:.2f}% "
            f"peak={item['peak_time']}@{item['peak_price']:.2f} "
            f"post_drop={item['max_drop']:.2f}% retake={item['post_retake_pct']:.2f}%"
        )
        print(f"  first_watch={watch_text}")
        print(f"  first_ready={ready_text}")
        print(f"  hypothetical_sell={sell_text} giveback={giveback_text} real_sell={real_sell_text}")
    print()

    print("DETAILED_WINDOWS")
    print("----------------")
    if not detailed:
        print("No window crossed the detail thresholds.")
    for item in detailed:
        print(
            f"MOVE #{item['rank']:02d} +{item['move_pct']:.2f}% | "
            f"peak={item['peak_time']} price={item['peak_price']:.2f} "
            f"post_drop={item['max_drop']:.2f}%"
        )
        for row in item["rows"]:
            delta_h = _hours_from_peak(item["peak_time"], row.as_of)
            giveback = max(0.0, -_pct_from_peak(item["peak_price"], row.price))
            print(
                f"  {row.as_of} dT={delta_h:+.1f}h price={row.price:.2f} "
                f"giveback={giveback:.2f}% stage={row.stage} health={row.health} "
                f"exec={row.execution} hypo={row.hypothetical_action} real={row.real_action}"
            )
            print(
                f"    MARKET ST={row.st_structure} LT={row.lt_structure} "
                f"STABIL={row.stabil} exit_event={'YES' if row.exit_event else 'NO'}"
            )
            print("    reasons=" + _compact(row.structural_reasons))
            print("    waiting=" + _compact(row.waiting_for))
        print()

    late_sell = sum(
        1 for item in summaries
        if item["sell_giveback"] is not None and item["sell_giveback"] >= args.detail_giveback_pct
    )
    no_sell_after_big_drop = sum(
        1 for item in summaries
        if item["max_drop"] >= args.detail_drop_pct and item["first_sell"] is None
    )
    early_sell_then_retake = sum(
        1 for item in summaries
        if item["first_sell"] is not None
        and item["sell_hours"] is not None
        and item["sell_hours"] < 0.0
        and item["post_retake_pct"] > 0.0
    )
    print("AGGREGATE")
    print("---------")
    print(f"moves={len(summaries)}")
    print(f"detailed={len(detailed)}")
    print(f"late_sell_giveback_ge_{args.detail_giveback_pct:g}pct={late_sell}")
    print(f"big_drop_without_hypothetical_sell={no_sell_after_big_drop}")
    print(f"pre_peak_sell_then_peak_retake={early_sell_then_retake}")
    print()
    print("READING GUIDE")
    print("-------------")
    print("1. Read-only counterfactual SELL audit; trading rules are unchanged.")
    print("2. hypo=SELL means the current ST exit engine would sell if an ST long existed at that hour.")
    print("3. The window starts before the detected 4H peak, so early deterioration is visible.")
    print("4. Pre-peak SELL followed by recovery above the peak warns about over-sensitive exits.")
    print("5. Large post-peak drop with late/no SELL is the main architectural-delay candidate.")
    print("TOP20_SELL_BEHAVIOR_AUDIT_OK")


if __name__ == "__main__":
    main()
