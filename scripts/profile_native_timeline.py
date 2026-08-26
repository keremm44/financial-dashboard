from __future__ import annotations

import argparse
from pathlib import Path

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.history_native_timeline import HistoricalNativeTimelineReplayRunner
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Profile the shared incremental native-domain timeline producer."
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
    args = parser.parse_args()

    replay = HistoricalNativeTimelineReplayRunner(ParquetOHLCVStore(args.cache_root)).replay(
        args.symbol,
        config=HistoricalDecisionInputConfig(
            start_at=args.start,
            end_at=args.end,
            max_bars=args.max_bars,
            pattern_profile=args.pattern_profile,
        ),
    )
    timings = replay.timings
    print(f"CAUSAL_CUTOFFS\t{len(replay.cutoffs)}")
    print(f"LOAD_INPUTS_SECONDS\t{timings.load_inputs_seconds:.2f}")
    print(f"EVENT_BUILD_SECONDS\t{timings.event_build_seconds:.2f}")
    print(f"NATIVE_REDUCE_SECONDS\t{timings.native_reduce_seconds:.2f}")
    print(f"TOTAL_SECONDS\t{timings.total_seconds:.2f}")
    print("INCREMENTAL_NATIVE_TIMELINE_OK")


if __name__ == "__main__":
    main()
