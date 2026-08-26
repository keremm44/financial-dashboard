from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.history_replay import (
    HistoricalDecisionInputReplayRunner,
    LegacyHistoricalDecisionInputReplayRunner,
)
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _plain(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return tuple(_plain(item) for item in value)
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return enum_value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare the legacy capture replay with the canonical append-only causal timeline."
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--max-bars", type=int, default=3)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--pattern-profile", default=None)
    args = parser.parse_args()

    config = HistoricalDecisionInputConfig(
        max_bars=args.max_bars,
        start_at=args.start,
        end_at=args.end,
        pattern_profile=args.pattern_profile,
    )
    store = ParquetOHLCVStore(args.cache_root)
    legacy = LegacyHistoricalDecisionInputReplayRunner(store).replay(args.symbol, config=config)
    canonical = HistoricalDecisionInputReplayRunner(store).replay(args.symbol, config=config)

    if legacy.cutoffs != canonical.cutoffs:
        raise SystemExit("CUTOFF_MISMATCH")
    if len(legacy.snapshots) != len(canonical.snapshots):
        raise SystemExit("SNAPSHOT_COUNT_MISMATCH")

    mismatches: list[int] = []
    for index, (left, right) in enumerate(zip(legacy.snapshots, canonical.snapshots, strict=True)):
        if _plain(left) != _plain(right):
            mismatches.append(index)

    print(f"SNAPSHOTS\t{len(legacy.snapshots)}")
    print(f"LEGACY_TOTAL_SECONDS\t{legacy.timings.total_seconds:.2f}")
    print(f"CANONICAL_TOTAL_SECONDS\t{canonical.timings.total_seconds:.2f}")
    print(f"LEGACY_ASSEMBLY_SECONDS\t{legacy.timings.snapshot_assembly_seconds:.2f}")
    print(f"CANONICAL_ASSEMBLY_SECONDS\t{canonical.timings.snapshot_assembly_seconds:.2f}")
    print(f"MISMATCHES\t{len(mismatches)}")
    if mismatches:
        print("MISMATCH_POSITIONS\t" + ",".join(str(index) for index in mismatches[:20]))
        raise SystemExit("INCREMENTAL_DECISION_REPLAY_MISMATCH")
    print("INCREMENTAL_DECISION_REPLAY_EQUIVALENT")


if __name__ == "__main__":
    main()
