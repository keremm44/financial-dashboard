from __future__ import annotations

import argparse
import hashlib
import json
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
from financial_dashboard.decision_audit.research import detect_large_market_moves

from buy_sell_backtest import _causal_warmup_start


AUDIT_CACHE_SCHEMA = "early-rise-pct-v3-calibrated"


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


def _decision_code_fingerprint() -> str:
    import financial_dashboard.decision as decision_package

    root = Path(decision_package.__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _load_calibration(cache_root: Path, symbol: str):
    path = cache_root / "calibration" / "opportunity" / f"{normalize_symbol(symbol)}.json"
    if not path.exists():
        raise SystemExit(
            "MISSING_OPPORTUNITY_CALIBRATION\n"
            f"Expected the same production/backtest calibration at: {path}\n"
            "Build opportunity calibration first; this audit must not silently fall back to UNKNOWN."
        )
    loaded = load_opportunity_calibration(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return loaded.calibration, path, digest


def _cache_identity(
    *,
    symbol: str,
    snapshots,
    args,
    code_fingerprint: str,
    calibration_path: Path,
    calibration_fingerprint: str,
) -> dict[str, object]:
    first = None if not snapshots else str(snapshots[0].as_of)
    last = None if not snapshots else str(snapshots[-1].as_of)
    return {
        "schema": AUDIT_CACHE_SCHEMA,
        "symbol": symbol,
        "snapshot_count": len(snapshots),
        "snapshot_first": first,
        "snapshot_last": last,
        "decision_code": code_fingerprint,
        "opportunity_calibration_path": str(calibration_path),
        "opportunity_calibration_sha256": calibration_fingerprint,
        "start": args.start,
        "end": args.end,
        "min_move_pct": float(args.min_move_pct),
        "reversal_pct": float(args.reversal_pct),
        "checkpoint_step_pct": float(args.checkpoint_step_pct),
    }


def _audit_cache_path(cache_root: Path, symbol: str, identity: dict[str, object]) -> Path:
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    key = hashlib.sha256(encoded).hexdigest()[:20]
    return cache_root / ".audit_cache" / "early_rise_pct" / f"{symbol}-{key}.json"


def _checkpoint_payload(snapshot, event, *, threshold_pct, threshold_price, config):
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

    return {
        "threshold_pct": float(threshold_pct),
        "first_seen": str(snapshot.as_of),
        "price": float(snapshot.current_price),
        "threshold_price": float(threshold_price),
        "action": action,
        "st_structure": f"{_enum(st.structural.direction)}/{_enum(st.structural.thesis_state)}",
        "st_scenario": f"{_enum(st_scenario.presence)}/{_enum(st_scenario.stage)}/{_enum(st_scenario.kind)}",
        "st_timing": _enum(st.timing.state),
        "st_opportunity": _enum(st.opportunity.state),
        "st_opportunity_room_atr": st.opportunity.room_atr,
        "st_opportunity_target": st.opportunity.target_identity,
        "st_opportunity_semantics": st.opportunity.target_semantics,
        "st_eligibility": _enum(st.eligibility.state),
        "st_conflict": _enum(st.conflict.state),
        "stabil_state": _enum(stabil.state),
        "stabil_quality": _enum(stabil.data_quality),
        "lt_structure": f"{_enum(lt.structural.direction)}/{_enum(lt.structural.thesis_state)}",
        "lt_scenario": f"{_enum(lt_scenario.presence)}/{_enum(lt_scenario.stage)}/{_enum(lt_scenario.kind)}",
        "waiting": list(waiting),
        "blockers": list(blockers),
        "reasons": list(reasons),
    }


def _build_cached_report(*, snapshots, decisions, bars_4h, decision_config, args, identity):
    decisions_by_time = {pd.Timestamp(event.timestamp): event for event in decisions}
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

    payload_moves: list[dict[str, object]] = []
    for rank, move in enumerate(ranked_moves, start=1):
        checkpoints: list[dict[str, object]] = []
        threshold = float(args.checkpoint_step_pct)
        while threshold <= float(move.move_pct) + 1e-9:
            threshold_price = float(move.start_price) * (1.0 + threshold / 100.0)
            snapshot = _snapshot_at_or_after(
                snapshots,
                move.start_time,
                move.end_time,
                threshold_price,
            )
            if snapshot is None:
                checkpoints.append(
                    {
                        "threshold_pct": float(threshold),
                        "missing": True,
                        "threshold_price": float(threshold_price),
                    }
                )
            else:
                event = _event_at(decisions_by_time, snapshot.as_of)
                checkpoints.append(
                    _checkpoint_payload(
                        snapshot,
                        event,
                        threshold_pct=threshold,
                        threshold_price=threshold_price,
                        config=decision_config,
                    )
                )
            threshold += float(args.checkpoint_step_pct)

        payload_moves.append(
            {
                "rank": rank,
                "start_time": str(move.start_time),
                "end_time": str(move.end_time),
                "start_price": float(move.start_price),
                "end_price": float(move.end_price),
                "move_pct": float(move.move_pct),
                "checkpoints": checkpoints,
            }
        )

    return {"identity": identity, "moves": payload_moves}


def _render_checkpoint(row: dict[str, object]) -> None:
    threshold = float(row["threshold_pct"])
    if row.get("missing"):
        print(
            f"  +{threshold:.0f}% NOT_SEEN_IN_CAUSAL_SNAPSHOTS "
            f"threshold={float(row['threshold_price']):.2f}"
        )
        return
    print(
        f"  +{threshold:.0f}% first_seen={row['first_seen']} "
        f"price={float(row['price']):.2f} threshold={float(row['threshold_price']):.2f} "
        f"action={row['action']}"
    )
    room = row.get("st_opportunity_room_atr")
    room_text = "-" if room is None else f"{float(room):.3f}ATR"
    print(
        "    ST "
        f"structure={row['st_structure']} scenario={row['st_scenario']} "
        f"timing={row['st_timing']} opportunity={row['st_opportunity']} "
        f"room={room_text} eligibility={row['st_eligibility']} conflict={row['st_conflict']}"
    )
    print(
        "    TARGET "
        f"id={row.get('st_opportunity_target') or '-'} "
        f"semantics={row.get('st_opportunity_semantics') or '-'}"
    )
    print(f"    STABIL state={row['stabil_state']} quality={row['stabil_quality']}")
    print(f"    LT structure={row['lt_structure']} scenario={row['lt_scenario']}")
    print(f"    waiting={_compact(row.get('waiting', ())) }")
    print(f"    blockers={_compact(row.get('blockers', ())) }")
    print(f"    reasons={_compact(row.get('reasons', ())) }")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Find price-only 4H rises, rank them from largest to smallest, cache the full "
            "causal audit once, then render selected move ranges without replaying decisions."
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
    parser.add_argument("--rebuild-audit-cache", action="store_true")
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

    calibration, calibration_path, calibration_fingerprint = _load_calibration(args.cache_root, symbol)
    decision_config = DecisionEngineConfig(opportunity_calibration=calibration)

    code_fingerprint = _decision_code_fingerprint()
    identity = _cache_identity(
        symbol=symbol,
        snapshots=snapshots,
        args=args,
        code_fingerprint=code_fingerprint,
        calibration_path=calibration_path,
        calibration_fingerprint=calibration_fingerprint,
    )
    cache_path = _audit_cache_path(args.cache_root, symbol, identity)

    report = None
    cache_status = "MISS"
    if cache_path.exists() and not args.rebuild_audit_cache:
        try:
            candidate = json.loads(cache_path.read_text(encoding="utf-8"))
            if candidate.get("identity") == identity:
                report = candidate
                cache_status = "HIT"
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            report = None

    if report is None:
        entry_events, exit_events = detect_1h_execution_events(snapshots)
        lifecycle = replay_canonical_trade_lifecycle(
            snapshots,
            config=decision_config,
            entry_execution_events=entry_events,
            exit_execution_events=exit_events,
        )
        decisions = canonical_decision_events_from_replay(lifecycle)
        bars_4h = store.load(symbol, "4h")
        if bars_4h.empty:
            raise SystemExit("4H bars are required")
        report = _build_cached_report(
            snapshots=snapshots,
            decisions=decisions,
            bars_4h=bars_4h,
            decision_config=decision_config,
            args=args,
            identity=identity,
        )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        cache_status = "REBUILT" if args.rebuild_audit_cache else "BUILT"

    moves = tuple(report.get("moves", ()))
    first_index = args.from_move - 1
    last_index = len(moves) if args.to_move is None else min(args.to_move, len(moves))
    selected = moves[first_index:last_index]

    print("EARLY RISE PERCENT AUDIT")
    print("========================")
    print(f"FROZEN_CACHE\t{frozen.cache_status}")
    print(f"AUDIT_CACHE\t{cache_status}")
    print("DOMAIN_REPLAY\tNOT_RUN")
    print(f"SYMBOL\t{symbol}")
    print(f"OPPORTUNITY_CALIBRATION\t{calibration_path}")
    print(f"OPPORTUNITY_CALIBRATION_SHA256\t{calibration_fingerprint[:16]}")
    print(
        "OPPORTUNITY_BOUNDS_ATR\t"
        f"none<={calibration.none_max_atr:.6g}; "
        f"compressed<={calibration.compressed_max_atr:.6g}; "
        f"moderate<={calibration.moderate_max_atr:.6g}; ample>moderate"
    )
    print(f"MOVE_RULE\tprice-only 4H, min=+{args.min_move_pct:g}%, reversal={args.reversal_pct:g}%")
    print("MOVE_ORDER\tlargest rise to smallest rise")
    print(f"CHECKPOINTS\tfirst causal snapshot at every +{args.checkpoint_step_pct:g}% from move start")
    print(f"UP_MOVES_TOTAL\t{len(moves)}")
    if selected:
        print(f"SHOWING\tMOVE #{selected[0]['rank']} .. MOVE #{selected[-1]['rank']}")
    else:
        print("SHOWING\tNONE")
    print()

    for move in selected:
        print(
            f"MOVE #{move['rank']}  {move['start_time']} -> {move['end_time']} | "
            f"{float(move['start_price']):.2f} -> {float(move['end_price']):.2f} | "
            f"{float(move['move_pct']):+.2f}%"
        )
        checkpoints = tuple(move.get("checkpoints", ()))
        if not checkpoints:
            print("  no percentage checkpoint rendered")
        for row in checkpoints:
            _render_checkpoint(row)
        print()

    print(f"AUDIT_CACHE_FILE\t{cache_path}")
    print("EARLY_RISE_PERCENT_AUDIT_OK")


if __name__ == "__main__":
    main()
