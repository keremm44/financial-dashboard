from __future__ import annotations

import argparse
from pathlib import Path

from financial_dashboard.stabil_support_replay import replay_stabil_support_history_from_cache


def _token(value) -> str:
    return str(getattr(value, "value", value))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run only the causal 1D Stabil support lifecycle/behavior from cached OHLCV."
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--max-points", type=int, default=120)
    parser.add_argument("--step", type=int, default=1)
    args = parser.parse_args()

    replay = replay_stabil_support_history_from_cache(
        args.cache_root,
        symbol=args.symbol,
        minimum_bars=1,
        step=args.step,
        max_points=args.max_points,
    )

    print("STABIL_ONLY_REPLAY")
    print("==================")
    print(f"SYMBOL\t{replay.symbol}")
    print(f"TIMEFRAME\t{replay.timeframe}")
    print(f"POINTS\t{len(replay.points)}")
    print("OTHER_DOMAINS\tNOT_RUN")
    print()
    print("STATE TRANSITIONS")

    previous = None
    for point in replay.points:
        behavior = point.behavior
        if behavior is None:
            continue
        signature = (
            _token(behavior.primary_state),
            _token(behavior.motion),
            _token(behavior.relation),
            _token(behavior.interaction),
            bool(behavior.reclaim_rejected),
            int(behavior.reclaim_rejection_count),
        )
        if signature == previous:
            continue
        previous = signature
        print(
            f"{point.as_of} close={point.close:.2f} | "
            f"primary={signature[0]} motion={signature[1]} relation={signature[2]} "
            f"interaction={signature[3]} rejected={'YES' if signature[4] else 'NO'} "
            f"rejections={signature[5]}"
        )

    latest = replay.latest_behavior
    print()
    print("LATEST")
    if latest is None:
        print("- unavailable")
    else:
        print(
            f"primary={_token(latest.primary_state)} motion={_token(latest.motion)} "
            f"relation={_token(latest.relation)} interaction={_token(latest.interaction)} "
            f"bars_since_rebase={latest.bars_since_rebase} cross_count={latest.cross_count} "
            f"reclaim_rejected={latest.reclaim_rejected} "
            f"reclaim_rejection_count={latest.reclaim_rejection_count}"
        )
    print("STABIL_ONLY_REPLAY_OK")


if __name__ == "__main__":
    main()
