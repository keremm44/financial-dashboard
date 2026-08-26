from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.history_replay import HistoricalDecisionInputReplayRunner
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Profile canonical historical decision-input assembly by stage."
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--pattern-profile", default=None)
    args = parser.parse_args()

    runner = HistoricalDecisionInputReplayRunner(ParquetOHLCVStore(args.cache_root))
    started = perf_counter()
    replay = runner.replay(
        args.symbol,
        config=HistoricalDecisionInputConfig(
            max_bars=args.max_bars,
            start_at=args.start,
            end_at=args.end,
            pattern_profile=args.pattern_profile,
        ),
    )
    total = perf_counter() - started

    print(f"SNAPSHOTS\t{len(replay.snapshots)}")
    print(f"NATIVE_REPLAY_SECONDS\t{replay.timings.native_replay_seconds:.2f}")
    print(f"SNAPSHOT_ASSEMBLY_SECONDS\t{replay.timings.snapshot_assembly_seconds:.2f}")
    for name, seconds in runner.last_assembly_breakdown.items():
        print(f"ASSEMBLY_{name.upper()}_SECONDS\t{seconds:.2f}")
    print(f"TOTAL_SECONDS\t{total:.2f}")
    print("DECISION_ASSEMBLY_PROFILE_OK")


if __name__ == "__main__":
    main()
