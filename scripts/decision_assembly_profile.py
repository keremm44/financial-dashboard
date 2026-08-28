from __future__ import annotations

import argparse
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter

from build_decision_timeline_cache import _build_timeline_once, _causal_warmup_start
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.persistent_history_runner import (
    PersistentHistoricalDecisionInputReplayRunner,
)


_STAGE_ORDER = (
    "views",
    "evidence",
    "dedup",
    "targeting",
    "semantic_targeting",
    "cross_domain",
    "decision_input",
)


@contextmanager
def _persistence_timing_scope():
    """Measure actual DecisionInput persistence time only inside this profiler."""

    original = PersistentHistoricalDecisionInputReplayRunner._save_decision_checkpoints
    measured = {"seconds": 0.0, "calls": 0}

    def wrapped(self, *args, **kwargs):
        started = perf_counter()
        try:
            return original(self, *args, **kwargs)
        finally:
            measured["seconds"] += perf_counter() - started
            measured["calls"] += 1

    PersistentHistoricalDecisionInputReplayRunner._save_decision_checkpoints = wrapped
    try:
        yield measured
    finally:
        PersistentHistoricalDecisionInputReplayRunner._save_decision_checkpoints = original


def _print_profile(symbol: str, runner, built, *, wall_seconds: float, persistence_seconds: float) -> None:
    breakdown = dict(runner.last_assembly_breakdown)
    stage_sum = sum(float(breakdown.get(stage, 0.0)) for stage in _STAGE_ORDER)
    assembly_seconds = float(built.timings.snapshot_assembly_seconds)
    assembly_overhead = max(0.0, assembly_seconds - stage_sum)
    stabil_seconds = float(built.timings.stabil_seconds)
    snapshots = len(built.snapshots)
    throughput = 0.0 if assembly_seconds <= 0.0 else snapshots / assembly_seconds

    print(f"\n=== DECISION ASSEMBLY PROFILE: {symbol} ===")
    print(f"{'STAGE':24s} {'SECONDS':>10s} {'% ASSEMBLY':>12s}")
    print("-" * 50)
    for stage in _STAGE_ORDER:
        seconds = float(breakdown.get(stage, 0.0))
        percentage = 0.0 if assembly_seconds <= 0.0 else seconds * 100.0 / assembly_seconds
        print(f"{stage:24s} {seconds:10.3f} {percentage:11.1f}%")
    overhead_pct = 0.0 if assembly_seconds <= 0.0 else assembly_overhead * 100.0 / assembly_seconds
    print(f"{'assembly_overhead':24s} {assembly_overhead:10.3f} {overhead_pct:11.1f}%")
    print("-" * 50)
    print(f"STABIL_SECONDS\t{stabil_seconds:.3f}")
    print(f"ASSEMBLY_SECONDS\t{assembly_seconds:.3f}")
    print(f"CACHE_WRITE_SECONDS\t{persistence_seconds:.3f}")
    print(f"TOTAL_PROFILE_WALL_SECONDS\t{wall_seconds:.3f}")
    print(f"SNAPSHOTS\t{snapshots}")
    print(f"SNAPSHOTS_PER_SECOND\t{throughput:.3f}")
    print(f"NATIVE_STATUS\t{runner.last_native_checkpoint_status}")
    print(f"SUPPORTING_STATUS\t{runner.last_supporting_checkpoint_status}")
    print(f"DECISION_CACHE_STATUS\t{runner.last_persistent_cache_status}")
    if assembly_seconds <= 0.0:
        print("PROFILE_NOTE\tEXACT_OR_NO_NEW_DECISION_SNAPSHOTS; no assembly work was measured")
    else:
        slowest = max(_STAGE_ORDER, key=lambda stage: float(breakdown.get(stage, 0.0)))
        print(f"SLOWEST_ASSEMBLY_STAGE\t{slowest}\t{float(breakdown.get(slowest, 0.0)):.3f}s")
    print("DECISION_ASSEMBLY_PROFILE_OK")


def _profile_symbol(cache_root: Path, symbol: str, args) -> None:
    store = ParquetOHLCVStore(cache_root)
    effective_start = _causal_warmup_start(
        store,
        symbol=symbol,
        requested_start=args.start,
    )
    config = HistoricalDecisionInputConfig(
        pattern_profile=args.pattern_profile,
        max_bars=args.max_bars,
        start_at=effective_start,
        end_at=args.end,
    )

    wall_started = perf_counter()
    with _persistence_timing_scope() as persistence:
        runner, built = _build_timeline_once(
            store,
            symbol=symbol,
            config=config,
        )
    wall_seconds = perf_counter() - wall_started
    _print_profile(
        symbol.strip().upper(),
        runner,
        built,
        wall_seconds=wall_seconds,
        persistence_seconds=float(persistence["seconds"]),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Profile post-domain DecisionInput assembly and persistence. "
            "Accepts one or more symbols so the same profiler can be reused for a future bulk universe."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbols", nargs="+")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
    args = parser.parse_args()

    failures = 0
    for symbol in args.symbols:
        try:
            _profile_symbol(args.cache_root, symbol, args)
        except Exception as exc:
            failures += 1
            print(f"DECISION_ASSEMBLY_PROFILE_ERROR\t{symbol.strip().upper()}\t{type(exc).__name__}: {exc}")

    print(f"\nPROFILE_SYMBOLS\t{len(args.symbols)}")
    print(f"PROFILE_FAILURES\t{failures}")
    print("DECISION_ASSEMBLY_PROFILE_BATCH_OK" if failures == 0 else "DECISION_ASSEMBLY_PROFILE_BATCH_WITH_FAILURES")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
