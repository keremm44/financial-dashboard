from __future__ import annotations

import argparse
from pathlib import Path

from financial_dashboard.auction_profile_replay import replay_auction_profile_history_from_cache


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay OHLCV-estimated Auction/Volume Profile")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cache-root", required=True, type=Path)
    parser.add_argument("--timeframe", default="1h", choices=("30m", "1h", "2h", "4h", "1d"))
    parser.add_argument("--minimum-bars", type=int, default=20)
    parser.add_argument("--step", type=int, default=1)
    parser.add_argument("--max-points", type=int, default=50)
    args = parser.parse_args()

    replay = replay_auction_profile_history_from_cache(
        args.cache_root,
        symbol=args.symbol,
        timeframe=args.timeframe,
        minimum_bars=args.minimum_bars,
        step=args.step,
        max_points=args.max_points,
    )
    print(
        f"symbol={replay.symbol} timeframe={replay.timeframe} points={len(replay.points)} "
        "profile_source=OHLCV_ESTIMATED"
    )
    for point in replay.points:
        s = point.snapshot
        print(
            f"as_of={point.as_of} close={point.close:.4f} quality={s.data_quality.value} "
            f"poc={s.poc} vah={s.vah} val={s.val} reaction={s.export.reaction_state} "
            f"migration={s.export.migration_state} balance={s.export.balance_state} "
            f"bars_used={s.provenance.bars_used} history_fraction={s.provenance.history_fraction:.3f} "
            f"allocation_error_pct={s.provenance.allocation_error_pct}"
        )
    print("AUCTION_ESTIMATED_PROFILE_REPLAY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
