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
    detail: str = ""


def _closed_records(store, symbol: str, timeframe: str):
    frame = store.load(symbol, timeframe)
    if "is_closed" in frame.columns:
        frame = frame.loc[frame["is_closed"].fillna(False).astype(bool)]
    if "is_complete" in frame.columns:
        frame = frame.loc[frame["is_complete"].fillna(False).astype(bool)]
    return frame, frame.to_dict("records")


def _worker(cache: str, symbol: str, domain: str, timeframe: str, queue) -> None:
    try:
        from financial_dashboard.data.parquet_store import ParquetOHLCVStore

        store = ParquetOHLCVStore(cache)
        started = time.perf_counter()

        if domain == "structure":
            from financial_dashboard.engines.market_structure_engine import MarketStructureEngine

            _, records = _closed_records(store, symbol, timeframe)
            engine = MarketStructureEngine()
            for row in records:
                engine.update(row)
            _ = engine.export_contract

        elif domain == "support_resistance":
            from financial_dashboard.engines.support_resistance_runtime_engine import (
                RuntimeSupportResistanceRangeEngine,
            )

            _, records = _closed_records(store, symbol, timeframe)
            engine = RuntimeSupportResistanceRangeEngine()
            for row in records:
                engine.update(row)
            _ = engine.snapshot()

        elif domain == "pattern":
            from financial_dashboard.engines.pattern_compression_runtime_engine import (
                RuntimePatternCompressionEngine,
            )

            _, records = _closed_records(store, symbol, timeframe)
            engine = RuntimePatternCompressionEngine()
            for row in records:
                engine.update(row)
            engine.snapshot()

        elif domain == "ham":
            from financial_dashboard.ham_mtf_replay import HamMTFEvidenceReplayRunner

            HamMTFEvidenceReplayRunner(store).replay(
                symbol,
                timeframes=(timeframe,),
            )

        elif domain == "volume":
            from financial_dashboard.volume_mtf_replay import VolumeMTFEvidenceReplayRunner

            VolumeMTFEvidenceReplayRunner(store).replay(
                symbol,
                timeframes=(timeframe,),
            )

        elif domain == "volatility":
            from financial_dashboard.volatility_mtf_replay import (
                VOLATILITY_TIMEFRAMES,
                VolatilityMTFReplayRunner,
            )

            if timeframe not in VOLATILITY_TIMEFRAMES:
                queue.put(("SKIP", 0.0, "unsupported timeframe"))
                return
            VolatilityMTFReplayRunner(store).replay(
                symbol,
                timeframes=(timeframe,),
            )

        elif domain == "liquidity":
            from financial_dashboard.target_evidence_replay import LiquidityMTFReplayRunner

            LiquidityMTFReplayRunner(store).replay(
                symbol,
                timeframes=(timeframe,),
            )

        elif domain == "order_block":
            from financial_dashboard.target_evidence_replay import OrderBlockMTFReplayRunner

            OrderBlockMTFReplayRunner(store).replay(
                symbol,
                timeframes=(timeframe,),
            )

        elif domain == "fvg_engulfing":
            from financial_dashboard.engines.fvg_engulfing_models import SUPPORTED_TIMEFRAMES
            from financial_dashboard.target_evidence_replay import FvgEngulfingMTFReplayRunner

            if timeframe not in SUPPORTED_TIMEFRAMES:
                queue.put(("SKIP", 0.0, "unsupported timeframe"))
                return
            FvgEngulfingMTFReplayRunner(store).replay(
                symbol,
                timeframes=(timeframe,),
            )

        elif domain == "stabil":
            if timeframe != "1d":
                queue.put(("SKIP", 0.0, "1d only"))
                return

            from financial_dashboard.data.analysis_inputs import load_analysis_inputs
            from financial_dashboard.decision.history_source import _stabil_points

            inputs = load_analysis_inputs(store, symbol=symbol, timeframes=("1d",))
            frame = inputs.for_timeframe("1d").input_batch.frame
            if frame.empty:
                queue.put(("SKIP", 0.0, "no 1d bars"))
                return
            _stabil_points(inputs, indices_1d=(len(frame) - 1,))

        else:
            queue.put(("ERROR", 0.0, f"unknown domain: {domain}"))
            return

        queue.put(("OK", time.perf_counter() - started, ""))
    except Exception as exc:
        queue.put(
            (
                "ERROR",
                0.0,
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
        status, seconds, detail = queue.get_nowait()
    except Exception:
        detail = f"process exited with code {process.exitcode} without result"
        print(f"ERROR   {domain:22s} {timeframe:4s} {detail}", flush=True)
        return RuntimeCase("ERROR", 0.0, domain, timeframe, detail)
    finally:
        queue.close()
        queue.join_thread()

    if status == "OK":
        print(f"DONE    {domain:22s} {timeframe:4s} {seconds:8.3f}s", flush=True)
    elif status == "SKIP":
        print(f"SKIP    {domain:22s} {timeframe:4s} {detail}", flush=True)
    else:
        print(f"ERROR   {domain:22s} {timeframe:4s} {detail}", flush=True)

    return RuntimeCase(status, seconds, domain, timeframe, detail)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run isolated real-runtime replays for every canonical decision domain/timeframe "
            "with a per-case timeout. Each case runs in a fresh Windows-safe process."
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
    print(f"{'DOMAIN':22s} {'TF':4s} {'SECONDS':>9s}  STATUS")
    print("-" * 52)
    for result in results:
        value = f"{result.seconds:.3f}" if result.status == "OK" else "-"
        print(f"{result.domain:22s} {result.timeframe:4s} {value:>9s}  {result.status}")

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
