from __future__ import annotations

import argparse
from pathlib import Path

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.volatility_mtf_replay import (
    VOLATILITY_TIMEFRAMES,
    VolatilityMTFReplayRunner,
    direction_lag_records,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay Volatility/Bands/Fib early direction diagnostics")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cache-root", required=True, type=Path)
    parser.add_argument("--profile", default="Dengeli", choices=("Hassas", "Dengeli", "Seçici"))
    parser.add_argument(
        "--timeframes",
        nargs="+",
        default=list(VOLATILITY_TIMEFRAMES),
        choices=VOLATILITY_TIMEFRAMES,
        help="Replay only selected volatility timeframes, e.g. --timeframes 2h",
    )
    parser.add_argument(
        "--max-bars",
        type=int,
        default=None,
        help="Use only the latest N prepared bars per selected timeframe",
    )
    parser.add_argument("--max-lag-rows", type=int, default=30)
    args = parser.parse_args()

    replay = VolatilityMTFReplayRunner(ParquetOHLCVStore(args.cache_root)).replay(
        args.symbol,
        timeframes=tuple(args.timeframes),
        profile=args.profile,
        max_bars=args.max_bars,
    )
    bars_text = "all" if args.max_bars is None else str(args.max_bars)
    print(
        f"symbol={replay.symbol} timeframes={','.join(replay.timeframes)} "
        f"profile={args.profile} max_bars={bars_text}"
    )
    print("[latest]")
    for timeframe in replay.timeframes:
        latest = replay.for_timeframe(timeframe).latest
        if latest is None:
            print(f"{timeframe} EMPTY")
            continue
        core = "—" if latest.core_result is None else latest.core_result.state
        export = latest.confirmed_export
        print(
            f"{timeframe} as_of={latest.timestamp} early={latest.early.state.value} "
            f"evidence={latest.early.evidence_count} core={core} fib={export.fib_state} "
            f"coherence={export.coherence} quality={export.quality}"
        )

    records = direction_lag_records(replay)
    print("[direction-lag]")
    limit = max(0, args.max_lag_rows)
    displayed = records[-limit:] if limit else ()
    for row in displayed:
        print(
            f"{row.timeframe} {row.direction} early={row.early_index} "
            f"candidate={row.candidate_index} confirmed={row.confirmed_index} "
            f"candidate_lag={row.candidate_lag_bars} confirmed_lag={row.confirmed_lag_bars}"
        )
    print(f"lag_records={len(records)} displayed={len(displayed)}")
    print("VOLATILITY_REPLAY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
