from __future__ import annotations

import argparse
from pathlib import Path

from financial_dashboard.stabil_support_replay import replay_stabil_support_history_from_cache


def _fmt(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay the descriptive daily Stabil support lifecycle from local cache."
    )
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cache-root", required=True, type=Path)
    parser.add_argument("--minimum-bars", type=int, default=20)
    parser.add_argument("--step", type=int, default=1)
    parser.add_argument("--max-points", type=int, default=50)
    return parser


def main() -> int:
    args = _parser().parse_args()
    replay = replay_stabil_support_history_from_cache(
        args.cache_root,
        symbol=args.symbol,
        minimum_bars=args.minimum_bars,
        step=args.step,
        max_points=args.max_points,
    )

    print(
        f"symbol={replay.symbol} timeframe={replay.timeframe} points={len(replay.points)}"
    )
    for point in replay.points:
        snapshot = point.snapshot
        print(
            f"{point.as_of} close={point.close:.4f} "
            f"validity={snapshot.validity.value} dynamics={snapshot.dynamics.value} "
            f"support={_fmt(snapshot.support_level)} floor={_fmt(snapshot.support_floor)} "
            f"distance_pct={_fmt(snapshot.distance_pct)} "
            f"distance_atr={_fmt(snapshot.distance_atr)} "
            f"bars_above={snapshot.bars_above_support} "
            f"bars_below={snapshot.bars_below_support} "
            f"reclaims={snapshot.reclaim_count} progression={snapshot.progression.value}"
        )

    latest = replay.latest
    if latest is not None:
        print("\n[event-counts]")
        counts: dict[str, int] = {}
        for event in latest.events:
            key = event.event_type.value
            counts[key] = counts.get(key, 0) + 1
        for event_type, count in sorted(counts.items()):
            print(f"{event_type}={count}")

        print("\n[reclaim-diagnostics]")
        breach_events = [
            event
            for event in latest.events
            if event.event_type.value
            in {
                "SUPPORT_BREACHED",
                "SUPPORT_FLOOR_BROKEN",
                "SUPPORT_RECLAIMED",
                "SUPPORT_LOST",
            }
        ]
        for event in breach_events:
            print(
                f"{event.event_time} event={event.event_type.value} "
                f"support={_fmt(event.support_level)} bars_below={event.bars_below_support} "
                f"reclaims={event.reclaim_count} progression={event.progression.value}"
            )

    print("STABIL_SUPPORT_REPLAY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
