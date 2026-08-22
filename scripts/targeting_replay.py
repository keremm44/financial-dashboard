from __future__ import annotations

import argparse
from pathlib import Path

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.targeting_historical_replay import TargetingHistoricalReplayRunner
from financial_dashboard.targeting_replay_diagnostics import semantic_transition_ledger


def _target_text(cluster) -> str:
    if cluster is None:
        return "NONE"
    anchor = cluster.liquidity_anchor
    anchor_text = "-" if anchor is None else f"{anchor:.4f}"
    return (
        f"{cluster.kind.value}:{cluster.side.value} "
        f"env=[{cluster.envelope_low:.4f},{cluster.envelope_high:.4f}] "
        f"anchor={anchor_text} dist_atr={cluster.distance_atr:.3f} "
        f"origins={cluster.independent_origin_count} families={cluster.independent_family_count}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay descriptive Liquidity/S-R/OB/FVG/Engulfing target clusters "
            "through a cached symbol using causal prefixes."
        )
    )
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cache-root", default=".cache/live-smoke")
    parser.add_argument("--reference-timeframe", default="1h")
    parser.add_argument("--timeframes", default="1d,4h,2h,1h,30m")
    parser.add_argument("--minimum-bars", type=int, default=20)
    parser.add_argument("--step", type=int, default=1)
    parser.add_argument("--max-points", type=int, default=50)
    parser.add_argument(
        "--quiet-progress",
        action="store_true",
        help="suppress per-point replay progress lines",
    )
    args = parser.parse_args()

    if args.minimum_bars < 1:
        parser.error("--minimum-bars must be >= 1")
    if args.step < 1:
        parser.error("--step must be >= 1")
    if args.max_points < 1:
        parser.error("--max-points must be >= 1")

    timeframes = tuple(
        item.strip().lower() for item in args.timeframes.split(",") if item.strip()
    )
    store = ParquetOHLCVStore(Path(args.cache_root))

    print("=== TARGETING CAUSAL REPLAY ===", flush=True)
    print(
        f"symbol={args.symbol} reference={args.reference_timeframe} "
        f"timeframes={','.join(timeframes)} max_points={args.max_points} step={args.step}",
        flush=True,
    )

    def progress(position, total, cutoff, state) -> None:
        if args.quiet_progress:
            return
        if state == "start":
            print(f"[{position}/{total}] {cutoff} replaying...", flush=True)
        elif state == "done":
            print(f"[{position}/{total}] {cutoff} done", flush=True)
        elif state == "skipped":
            print(f"[{position}/{total}] {cutoff} skipped: insufficient causal bars", flush=True)

    try:
        replay = TargetingHistoricalReplayRunner(store).replay(
            args.symbol,
            timeframes=timeframes,
            reference_timeframe=args.reference_timeframe,
            minimum_bars_per_timeframe=args.minimum_bars,
            step=args.step,
            max_points=args.max_points,
            progress=progress,
        )
    except KeyboardInterrupt:
        print("\nTARGETING_REPLAY_INTERRUPTED", flush=True)
        return 130
    except Exception as error:
        print("TARGETING_REPLAY_FAILED")
        print(f"reason={type(error).__name__}: {error}")
        return 2

    semantic_transitions = semantic_transition_ledger(replay)
    print(
        f"symbol={replay.symbol} reference={replay.reference_timeframe} "
        f"timeframes={','.join(replay.timeframes)} points={len(replay.points)} "
        f"raw_transitions={len(replay.transitions)} "
        f"semantic_transitions={len(semantic_transitions)}"
    )
    if not replay.points:
        print("TARGETING_REPLAY_NO_POINTS")
        print("reason=not enough causally available bars for the requested minimum")
        return 3

    for point in replay.points:
        snapshot = point.snapshot
        print(
            f"\n[{point.available_at}] price={snapshot.current_price:.4f} "
            f"atr={snapshot.reference_atr:.4f} clusters={len(snapshot.clusters)}"
        )
        print(f"nearest_up={_target_text(snapshot.nearest_upside_target)}")
        print(f"nearest_down={_target_text(snapshot.nearest_downside_target)}")
        print(f"highest_confluence_up={_target_text(snapshot.highest_confluence_upside)}")
        print(f"highest_confluence_down={_target_text(snapshot.highest_confluence_downside)}")

    print("\n[semantic-target-transitions]")
    if not semantic_transitions:
        print("NONE")
    for transition in semantic_transitions:
        print(
            f"{transition.available_at} {transition.field} {transition.kind.value}: "
            f"{transition.previous_identity or 'NONE'} -> {transition.new_identity or 'NONE'} "
            f"env={transition.previous_envelope}->{transition.new_envelope} "
            f"dist_atr={transition.previous_distance_atr}->{transition.new_distance_atr}"
        )

    causal_violations = [
        (point.available_at, evidence.uid)
        for point in replay.points
        for cluster in point.snapshot.clusters
        for evidence in cluster.evidence
        if evidence.available_at > point.available_at
    ]
    if causal_violations:
        print("\nTARGETING_REPLAY_FAILED")
        print(f"reason=causal evidence violations: {causal_violations[:10]}")
        return 4

    print("\nTARGETING_REPLAY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
