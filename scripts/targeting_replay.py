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


def _objective_text(objective, current_price: float, atr: float) -> str:
    if objective is None:
        return "NONE"
    if objective.side.value == "ABOVE":
        distance = max(0.0, objective.low - current_price)
    elif objective.side.value == "BELOW":
        distance = max(0.0, current_price - objective.high)
    else:
        distance = 0.0
    distance_atr = distance / max(float(atr), 1e-12)
    scope = "-" if objective.liquidity_scope is None else objective.liquidity_scope.value
    return (
        f"{objective.kind.value}:{objective.side.value} "
        f"anchor={objective.anchor_price:.4f} dist_atr={distance_atr:.3f} "
        f"scope={scope} tf={objective.source.timeframe} state={objective.source.source_state}"
    )


def _arrival_text(context) -> str:
    if context is None:
        return "NONE"
    nearest_downstream = (
        None
        if not context.downstream_reactions
        else context.downstream_reactions[0].distance_from_objective_atr
    )
    downstream_text = "-" if nearest_downstream is None else f"{nearest_downstream:.3f}"
    return (
        f"{context.state.value} "
        f"current={len(context.current_reactions)} "
        f"ahead={len(context.reactions_ahead)} "
        f"at={len(context.reactions_at)} "
        f"downstream={len(context.downstream_reactions)} "
        f"nearest_downstream_atr={downstream_text} "
        f"independent_rx_origins={context.independent_reaction_origins}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay causal targeting through a cached symbol. Legacy TargetCluster and "
            "semantic Objective/Reaction/Confirmation outputs run in shadow mode."
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
        semantic = point.semantic_snapshot
        print(
            f"\n[{point.available_at}] price={snapshot.current_price:.4f} "
            f"atr={snapshot.reference_atr:.4f} clusters={len(snapshot.clusters)}"
        )
        if semantic is None:
            print("semantic=NONE")
        else:
            print(
                f"semantic_overall={semantic.overall_state.value} "
                f"upside_state={semantic.upside_state.value} "
                f"downside_state={semantic.downside_state.value} "
                f"objectives={len(semantic.objectives)} "
                f"reactions={len(semantic.reaction_zones)} "
                f"confirmations={len(semantic.confirmations)}"
            )
            print(
                "objective_up="
                + _objective_text(
                    semantic.nearest_upside_objective,
                    semantic.current_price,
                    semantic.reference_atr,
                )
            )
            print(
                "objective_down="
                + _objective_text(
                    semantic.nearest_downside_objective,
                    semantic.current_price,
                    semantic.reference_atr,
                )
            )
            print("arrival_up=" + _arrival_text(semantic.upside_arrival))
            print("arrival_down=" + _arrival_text(semantic.downside_arrival))

        print(f"legacy_nearest_up={_target_text(snapshot.nearest_upside_target)}")
        print(f"legacy_nearest_down={_target_text(snapshot.nearest_downside_target)}")
        print(f"legacy_highest_confluence_up={_target_text(snapshot.highest_confluence_upside)}")
        print(f"legacy_highest_confluence_down={_target_text(snapshot.highest_confluence_downside)}")

    print("\n[legacy-semantic-target-transitions]")
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
    semantic_causal_violations = []
    for point in replay.points:
        semantic = point.semantic_snapshot
        if semantic is None:
            continue
        sources = [
            *(objective.source for objective in semantic.objectives),
            *(zone.source for zone in semantic.reaction_zones),
            *(confirmation.source for confirmation in semantic.confirmations),
        ]
        semantic_causal_violations.extend(
            (point.available_at, item.uid)
            for item in sources
            if item.available_at > point.available_at
        )
    if causal_violations or semantic_causal_violations:
        print("\nTARGETING_REPLAY_FAILED")
        print(
            "reason=causal evidence violations: "
            f"legacy={causal_violations[:10]} semantic={semantic_causal_violations[:10]}"
        )
        return 4

    print("\nTARGETING_REPLAY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
