from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.history_replay import HistoricalDecisionInputReplayRunner
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Profile canonical persistent historical decision-input assembly by stage."
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
    print(f"PERSISTENT_DECISION_CACHE\t{runner.last_persistent_cache_status}")
    print(f"DECISION_APPEND_CHECKPOINT\t{runner.last_decision_append_status}")
    print(f"NATIVE_CHECKPOINT\t{runner.last_native_checkpoint_status}")
    print(f"SUPPORTING_CHECKPOINT\t{runner.last_supporting_checkpoint_status}")
    print(f"NATIVE_CAPTURE_PASS_SECONDS\t{replay.timings.native_capture_pass_seconds:.2f}")
    print(f"HAM_REPLAY_SECONDS\t{replay.timings.ham_seconds:.2f}")
    print(f"VOLUME_REPLAY_SECONDS\t{replay.timings.volume_seconds:.2f}")
    print(f"VOLATILITY_REPLAY_SECONDS\t{replay.timings.volatility_seconds:.2f}")
    print(f"STABIL_REPLAY_SECONDS\t{replay.timings.stabil_seconds:.2f}")
    print(f"NATIVE_REPLAY_SECONDS\t{replay.timings.native_replay_seconds:.2f}")
    print(f"SNAPSHOT_ASSEMBLY_SECONDS\t{replay.timings.snapshot_assembly_seconds:.2f}")
    for name, seconds in runner.last_assembly_breakdown.items():
        print(f"ASSEMBLY_{name.upper()}_SECONDS\t{seconds:.2f}")
    print(f"TOTAL_SECONDS\t{total:.2f}")
    print("DECISION_ASSEMBLY_PROFILE_OK")


if __name__ == "__main__":
    main()
