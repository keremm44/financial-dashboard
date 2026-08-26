from __future__ import annotations

import argparse
import multiprocessing as mp
import time
import traceback
from dataclasses import dataclass
from pathlib import Path


DEFAULT_TIMEFRAMES = ("1d", "4h", "2h", "1h", "30m")
DEFAULT_DOMAINS = (
    "structure",
    "support_resistance",
    "pattern",
    "ham",
    "volume",
    "volatility",
    "liquidity",
    "order_block",
    "fvg_engulfing",
    "stabil",
)


@dataclass(frozen=True, slots=True)
class RuntimeCase:
    status: str
    seconds: float
    domain: str
    timeframe: str
    bars: int = 0
    detail: str = ""


def _canonical_inputs(store, symbol: str, timeframe: str):
    from financial_dashboard.data.analysis_inputs import load_analysis_inputs

    inputs = load_analysis_inputs(store, symbol=symbol, timeframes=(timeframe,))
    frame = inputs.for_timeframe(timeframe).input_batch.frame
    return inputs, frame, frame.to_dict("records")


def _worker(cache: str, symbol: str, domain: str, timeframe: str, queue) -> None:
    try:
        from financial_dashboard.data.parquet_store import ParquetOHLCVStore

        store = ParquetOHLCVStore(cache)
        inputs, frame, records = _canonical_inputs(store, symbol, timeframe)
        bars = len(frame)

        if domain == "structure":
            from financial_dashboard.engines.market_structure_engine import MarketStructureEngine

            started = time.perf_counter()
            engine = MarketStructureEngine()
            for row in records:
                engine.update(row)
            _ = engine.export_contract
            seconds = time.perf_counter() - started

        elif domain == "support_resistance":
            from financial_dashboard.engines.support_resistance_runtime_engine import (
                RuntimeSupportResistanceRangeEngine,
            )

            started = time.perf_counter()
            engine = RuntimeSupportResistanceRangeEngine()
            for row in records:
                engine.update(row)
            _ = engine.snapshot()
            seconds = time.perf_counter() - started

        elif domain == "pattern":
            from financial_dashboard.engines.pattern_compression_runtime_engine import (
                RuntimePatternCompressionEngine,
            )

            started = time.perf_counter()
            engine = RuntimePatternCompressionEngine()
            for row in records:
                engine.update(row)
            engine.snapshot()
            seconds = time.perf_counter() - started

        elif domain == "ham":
            from financial_dashboard.ham_mtf_replay import HamMTFEvidenceReplayRunner

            started = time.perf_counter()
            HamMTFEvidenceReplayRunner(store).replay(
                symbol,
                timeframes=(timeframe,),
                input_snapshot=inputs,
            )
            seconds = time.perf_counter() - started

        elif domain == "volume":
            from financial_dashboard.structure_location_replay import CachedStructureLocationMTFRunner
            from financial_dashboard.volume_mtf_replay import VolumeMTFEvidenceReplayRunner

            # Canonical decision assembly already owns Structure. Build it outside
            # the Volume timer so the Volume row does not count a second Structure replay.
            structure_replay = CachedStructureLocationMTFRunner(store).run(
                symbol=symbol,
                timeframes=(timeframe,),
                input_snapshot=inputs,
            )
            started = time.perf_counter()
            VolumeMTFEvidenceReplayRunner(store).replay(
                symbol,
                timeframes=(timeframe,),
                structure_replay=structure_replay,
                input_snapshot=inputs,
            )
            seconds = time.perf_counter() - started

        elif domain == "volatility":
            from financial_dashboard.volatility_mtf_replay import (
                VOLATILITY_TIMEFRAMES,
                VolatilityMTFReplayRunner,
            )

            if timeframe not in VOLATILITY_TIMEFRAMES:
                queue.put(("SKIP", 0.0, bars, "unsupported timeframe"))
                return
            started = time.perf_counter()
            VolatilityMTFReplayRunner(store).replay(
                symbol,
                timeframes=(timeframe,),
                input_snapshot=inputs,
            )
            seconds = time.perf_counter() - started

        elif domain == "liquidity":
            from financial_dashboard.engines.liquidity_engine import LiquidityEngine

            started = time.perf_counter()
            engine = LiquidityEngine()
            for row in records:
                engine.update(row)
            _ = engine.behavior_snapshot
            seconds = time.perf_counter() - started

        elif domain == "order_block":
            from financial_dashboard.engines.order_block import OrderBlockEngine
            from financial_dashboard.engines.order_block_behavior import OrderBlockBehaviorTracker

            started = time.perf_counter()
            engine = OrderBlockEngine()
            behavior = OrderBlockBehaviorTracker()
            for index, row in enumerate(records):
                engine.update(row)
                behavior.update(
                    engine.records,
                    bar_index=index,
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                )
            seconds = time.perf_counter() - started

        elif domain == "fvg_engulfing":
            from financial_dashboard.engines.fvg_engulfing import FvgEngulfingEngine
            from financial_dashboard.engines.fvg_engulfing_models import (
                FvgEngulfingConfig,
                SUPPORTED_TIMEFRAMES,
            )

            if timeframe not in SUPPORTED_TIMEFRAMES:
                queue.put(("SKIP", 0.0, bars, "unsupported timeframe"))
                return
            started = time.perf_counter()
            engine = FvgEngulfingEngine(FvgEngulfingConfig(timeframe=timeframe))
            for row in records:
                engine.update(row)
            seconds = time.perf_counter() - started

        elif domain == "stabil":
            if timeframe != "1d":
                queue.put(("SKIP", 0.0, bars, "1d only"))
                return

            from financial_dashboard.decision.history_source import _stabil_points

            if frame.empty:
                queue.put(("SKIP", 0.0, bars, "no 1d bars"))
                return
            started = time.perf_counter()
            _stabil_points(inputs, indices_1d=(len(frame) - 1,))
            seconds = time.perf_counter() - started

        else:
            queue.put(("ERROR", 0.0, bars, f"unknown domain: {domain}"))
            return

        queue.put(("OK", seconds, bars, ""))
    except Exception as exc:
        queue.put(
            (
                "ERROR",
                0.0,
                0,
                f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
            )
        )


def _run_case(
    ctx,
    *,
    cache: str,
    symbol: str,
    domain: str,
    timeframe: str,
    timeout_seconds: float,
) -> RuntimeCase:
    queue = ctx.Queue()
    process = ctx.Process(
        target=_worker,
        args=(cache, symbol, domain, timeframe, queue),
    )

    print(f"START   {domain:22s} {timeframe:4s}", flush=True)
    wall_started = time.perf_counter()
    process.start()
    process.join(timeout_seconds)

    if process.is_alive():
        process.terminate()
        process.join()
        elapsed = time.perf_counter() - wall_started
        print(
            f"TIMEOUT {domain:22s} {timeframe:4s} > {timeout_seconds:g}s "
            f"(wall {elapsed:.3f}s)",
            flush=True,
        )
        return RuntimeCase("TIMEOUT", timeout_seconds, domain, timeframe)

    try:
        status, seconds, bars, detail = queue.get_nowait()
    except Exception:
        detail = f"process exited with code {process.exitcode} without result"
        print(f"ERROR   {domain:22s} {timeframe:4s} {detail}", flush=True)
        return RuntimeCase("ERROR", 0.0, domain, timeframe, 0, detail)
    finally:
        queue.close()
        queue.join_thread()

    if status == "OK":
        per_bar_ms = 0.0 if bars <= 0 else (seconds * 1000.0 / bars)
        print(
            f"DONE    {domain:22s} {timeframe:4s} {seconds:8.3f}s  "
            f"BARS={bars:5d}  MS/BAR={per_bar_ms:8.3f}",
            flush=True,
        )
    elif status == "SKIP":
        print(f"SKIP    {domain:22s} {timeframe:4s} BARS={bars:5d}  {detail}", flush=True)
    else:
        print(f"ERROR   {domain:22s} {timeframe:4s} {detail}", flush=True)

    return RuntimeCase(status, seconds, domain, timeframe, bars, detail)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run isolated canonical-runtime replays for every decision domain/timeframe. "
            "Volume timing excludes the Structure replay that canonical assembly already owns."
        )
    )
    parser.add_argument("cache", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES))
    parser.add_argument("--domains", nargs="+", default=list(DEFAULT_DOMAINS))
    args = parser.parse_args()

    cache = str(args.cache.expanduser().resolve(strict=False))
    symbol = args.symbol.strip().upper()
    timeframes = tuple(value.strip().lower() for value in args.timeframes)
    domains = tuple(value.strip().lower() for value in args.domains)

    mp.freeze_support()
    ctx = mp.get_context("spawn")

    print("\n=== RUNTIME MATRIX ===")
    print(f"cache   : {cache}")
    print(f"symbol  : {symbol}")
    print(f"timeout : {args.timeout:g}s / case")
    print(f"TF      : {', '.join(timeframes)}")
    print(f"domains : {', '.join(domains)}")

    results: list[RuntimeCase] = []
    wall_started = time.perf_counter()
    for timeframe in timeframes:
        print(f"\n--- {timeframe} ---")
        for domain in domains:
            results.append(
                _run_case(
                    ctx,
                    cache=cache,
                    symbol=symbol,
                    domain=domain,
                    timeframe=timeframe,
                    timeout_seconds=args.timeout,
                )
            )

    wall_seconds = time.perf_counter() - wall_started

    print("\n=== SUMMARY ===")
    print(f"{'DOMAIN':22s} {'TF':4s} {'BARS':>6s} {'SECONDS':>9s} {'MS/BAR':>9s}  STATUS")
    print("-" * 72)
    for result in results:
        value = f"{result.seconds:.3f}" if result.status == "OK" else "-"
        per_bar = (
            f"{result.seconds * 1000.0 / result.bars:.3f}"
            if result.status == "OK" and result.bars > 0
            else "-"
        )
        print(
            f"{result.domain:22s} {result.timeframe:4s} {result.bars:6d} "
            f"{value:>9s} {per_bar:>9s}  {result.status}"
        )

    successful = sorted(
        (item for item in results if item.status == "OK"),
        key=lambda item: item.seconds,
        reverse=True,
    )
    print("\n=== SLOWEST OK ===")
    for result in successful:
        print(f"{result.domain:22s} {result.timeframe:4s} {result.seconds:8.3f}s")

    domain_totals: dict[str, float] = {}
    for result in results:
        if result.status == "OK":
            domain_totals[result.domain] = domain_totals.get(result.domain, 0.0) + result.seconds

    print("\n=== DOMAIN TOTALS ===")
    for domain, seconds in sorted(domain_totals.items(), key=lambda item: item[1], reverse=True):
        print(f"{domain:22s} {seconds:8.3f}s")

    ok_sum = sum(item.seconds for item in results if item.status == "OK")
    failures = [item for item in results if item.status in {"ERROR", "TIMEOUT"}]
    print(f"\nTOTAL_OK_SECONDS\t{ok_sum:.3f}")
    print(f"TOTAL_WALL_SECONDS\t{wall_seconds:.3f}")
    print(f"RUNTIME_MATRIX_FAILURES\t{len(failures)}")
    print("RUNTIME_MATRIX_OK" if not failures else "RUNTIME_MATRIX_COMPLETED_WITH_FAILURES")


if __name__ == "__main__":
    main()
