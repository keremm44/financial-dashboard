from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.calibration import load_opportunity_calibration
from financial_dashboard.decision.engine import DecisionEngineConfig, assess_horizon_decision
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.opportunity import OpportunityState
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection
from financial_dashboard.decision.timeline_cache import DecisionTimelineCacheMiss, load_frozen_decision_timeline
from financial_dashboard.decision_audit.research import detect_large_market_moves

from buy_sell_backtest import _causal_warmup_start


def _enum(value) -> str:
    return str(getattr(value, "value", value))


def _load_calibration(cache_root: Path, symbol: str):
    path = cache_root / "calibration" / "opportunity" / f"{normalize_symbol(symbol)}.json"
    if not path.exists():
        raise SystemExit(f"MISSING_OPPORTUNITY_CALIBRATION: {path}")
    return load_opportunity_calibration(path).calibration


def _nearest_upside(snapshot):
    targeting = getattr(snapshot, "targeting", None)
    if targeting is None:
        return None
    return getattr(targeting, "nearest_upside_target", None)


def _target_price(cluster) -> float:
    anchor = getattr(cluster, "liquidity_anchor", None)
    if anchor is not None:
        return float(anchor)
    core_low = getattr(cluster, "core_low", None)
    core_high = getattr(cluster, "core_high", None)
    if core_low is not None and core_high is not None:
        return (float(core_low) + float(core_high)) / 2.0
    return (float(cluster.envelope_low) + float(cluster.envelope_high)) / 2.0


def _future_snapshots(snapshots, start_ts, end_ts):
    for item in snapshots:
        ts = pd.Timestamp(item.as_of)
        if ts <= start_ts:
            continue
        if ts > end_ts:
            break
        yield item


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure whether nearby upside liquidity targets that suppress entries are actually cleared during price-only 4H rises."
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--min-move-pct", type=float, default=7.0)
    parser.add_argument("--reversal-pct", type=float, default=5.0)
    parser.add_argument("--states", nargs="+", default=["NONE", "COMPRESSED"])
    parser.add_argument("--show", type=int, default=60)
    args = parser.parse_args()

    symbol = normalize_symbol(args.symbol)
    store = ParquetOHLCVStore(args.cache_root)
    effective_start = _causal_warmup_start(store, symbol=symbol, requested_start=args.start)
    history_config = HistoricalDecisionInputConfig(start_at=effective_start, end_at=args.end)
    try:
        frozen = load_frozen_decision_timeline(store, symbol, config=history_config)
    except DecisionTimelineCacheMiss as exc:
        raise SystemExit("FROZEN_DECISION_TIMELINE_CACHE_MISS") from exc

    snapshots = tuple(sorted(frozen.replay.snapshots, key=lambda x: pd.Timestamp(x.as_of)))
    bars_4h = store.load(symbol, "4h")
    if bars_4h.empty:
        raise SystemExit("4H bars are required")

    calibration = _load_calibration(args.cache_root, symbol)
    config = DecisionEngineConfig(opportunity_calibration=calibration)
    wanted_states = {str(x).strip().upper() for x in args.states}

    moves = tuple(
        move
        for move in detect_large_market_moves(
            bars_4h,
            min_move_pct=float(args.min_move_pct),
            reversal_pct=float(args.reversal_pct),
        )
        if move.direction == "UP"
    )

    rows: list[dict[str, object]] = []
    seen_keys: set[tuple[str, str]] = set()
    for move in moves:
        move_start = pd.Timestamp(move.start_time)
        move_end = pd.Timestamp(move.end_time)
        for snapshot in snapshots:
            ts = pd.Timestamp(snapshot.as_of)
            if ts < move_start:
                continue
            if ts > move_end:
                break
            st = assess_horizon_decision(snapshot, DecisionHorizon.SHORT_TERM, config=config, execution_event=None)
            if st.structural.direction is not StructuralDirection.LONG:
                continue
            state = _enum(st.opportunity.state).upper()
            if state not in wanted_states:
                continue
            if str(st.opportunity.target_semantics or "").upper() != "LIQUIDITY_MAGNET":
                continue
            cluster = _nearest_upside(snapshot)
            if cluster is None or cluster.identity != st.opportunity.target_identity:
                continue

            key = (str(move.start_time), str(cluster.identity))
            if key in seen_keys:
                continue
            seen_keys.add(key)

            target_price = _target_price(cluster)
            future = tuple(_future_snapshots(snapshots, ts, move_end))
            future_prices = [float(item.current_price) for item in future if getattr(item, "current_price", None) is not None]
            max_future = max(future_prices) if future_prices else float(snapshot.current_price)
            clear_level = float(cluster.envelope_high)
            cleared = max_future > clear_level
            first_clear = None
            bars_to_clear = None
            if cleared:
                for idx, item in enumerate(future, start=1):
                    price = getattr(item, "current_price", None)
                    if price is not None and float(price) > clear_level:
                        first_clear = pd.Timestamp(item.as_of)
                        bars_to_clear = idx
                        break

            extension_pct = ((max_future / target_price) - 1.0) * 100.0 if target_price > 0 else 0.0
            rows.append(
                {
                    "move_pct": float(move.move_pct),
                    "move_start": move_start,
                    "move_end": move_end,
                    "seen": ts,
                    "price": float(snapshot.current_price),
                    "state": state,
                    "room_atr": st.opportunity.room_atr,
                    "target": str(cluster.identity),
                    "target_price": target_price,
                    "target_high": float(cluster.envelope_high),
                    "quality": _enum(cluster.quality),
                    "origins": int(cluster.independent_origin_count),
                    "families": int(cluster.independent_family_count),
                    "cleared": cleared,
                    "first_clear": first_clear,
                    "bars_to_clear": bars_to_clear,
                    "max_future": max_future,
                    "extension_pct": extension_pct,
                }
            )

    total = len(rows)
    cleared_count = sum(1 for row in rows if row["cleared"])
    print("NEARBY UPSIDE TARGET OUTCOME AUDIT")
    print("==================================")
    print(f"SYMBOL\t{symbol}")
    print(f"UP_MOVES\t{len(moves)}")
    print(f"TARGET_CASES\t{total}")
    if total:
        print(f"CLEARED_DURING_SAME_MOVE\t{cleared_count}/{total} ({100.0 * cleared_count / total:.1f}%)")
        print(f"NOT_CLEARED\t{total-cleared_count}/{total} ({100.0 * (total-cleared_count) / total:.1f}%)")

    by_state = defaultdict(lambda: [0, 0])
    by_quality = defaultdict(lambda: [0, 0])
    for row in rows:
        by_state[row["state"]][0] += 1
        by_state[row["state"]][1] += int(bool(row["cleared"]))
        by_quality[row["quality"]][0] += 1
        by_quality[row["quality"]][1] += int(bool(row["cleared"]))

    print("\nBY OPPORTUNITY STATE")
    for name, (count, clears) in sorted(by_state.items()):
        print(f"  {name}: cleared={clears}/{count} ({(100.0*clears/count if count else 0):.1f}%)")

    print("\nBY TARGET QUALITY")
    for name, (count, clears) in sorted(by_quality.items()):
        print(f"  {name}: cleared={clears}/{count} ({(100.0*clears/count if count else 0):.1f}%)")

    print("\nCASES")
    ranked = sorted(rows, key=lambda row: (-float(row["move_pct"]), row["seen"]))
    for row in ranked[: max(0, int(args.show))]:
        outcome = "CLEARED" if row["cleared"] else "HELD"
        clear_text = "-" if row["first_clear"] is None else str(row["first_clear"])
        print(
            f"  {row['seen']} move=+{row['move_pct']:.2f}% price={row['price']:.2f} "
            f"{row['state']} room={float(row['room_atr']):.3f}ATR target={row['target_price']:.2f} "
            f"quality={row['quality']} origins={row['origins']} families={row['families']} "
            f"=> {outcome} first_clear={clear_text} max={row['max_future']:.2f} "
            f"beyond_target={row['extension_pct']:+.2f}%"
        )

    print("\nINTERPRETATION")
    if total == 0:
        print("  No matching nearby liquidity cases found.")
    else:
        ratio = cleared_count / total
        if ratio >= 0.65:
            print("  Nearby liquidity magnets are usually crossed during the same strong rise; treating all of them as absolute profit ceilings is likely too strict.")
        elif ratio <= 0.35:
            print("  Nearby liquidity magnets usually hold during these rises; the hard-room rule has substantial empirical support.")
        else:
            print("  Mixed result: target quality/origin strength should decide whether a nearby level is a hard barrier or only an intermediate waypoint.")


if __name__ == "__main__":
    main()
