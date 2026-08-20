from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from financial_dashboard.data.engine_input import EngineInputError, prepare_engine_input
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.data.pipeline import MarketDataPipeline
from financial_dashboard.data.tvdatafeed_provider import TvDatafeedProvider
from financial_dashboard.engines import MarketStructureEngine, PatternCompressionEngine


TZ = ZoneInfo("Europe/Istanbul")


def _summary(frame) -> dict[str, object]:
    if frame.empty:
        return {"rows": 0}
    return {
        "rows": len(frame),
        "first": str(frame.iloc[0]["timestamp"]),
        "last": str(frame.iloc[-1]["timestamp"]),
        "closed": int(frame["is_closed"].fillna(False).astype(bool).sum()) if "is_closed" in frame.columns else None,
        "complete": int(frame["is_complete"].fillna(False).astype(bool).sum()) if "is_complete" in frame.columns else None,
        "volume_zero_or_missing": int(((frame["volume"].isna()) | (frame["volume"] <= 0)).sum()) if "volume" in frame.columns else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Real BIST tvDatafeed -> cache -> resample -> engine smoke test")
    parser.add_argument("--symbol", default="THYAO")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--cache-root", default=".cache/live-smoke")
    parser.add_argument("--max-bars", type=int, default=5000)
    args = parser.parse_args()

    end = datetime.now(TZ)
    start = end - timedelta(days=args.days)

    print("=== ARGENT LIVE SMOKE ===")
    print(f"symbol={args.symbol} exchange=BIST start={start.isoformat()} end={end.isoformat()}")

    provider = TvDatafeedProvider(exchange="BIST", max_bars=args.max_bars)
    store = ParquetOHLCVStore(Path(args.cache_root))
    pipeline = MarketDataPipeline(provider, store)

    result = pipeline.refresh_bist_5m_incremental(
        symbol=args.symbol,
        requested_start=start,
        end=end,
    )

    print("\n[provider]")
    print(f"volume_status={provider.last_volume_status} volume_type={provider.volume_type}")
    print(f"5m={_summary(result.base)}")

    print("\n[derived]")
    for timeframe, frame in result.derived.items():
        print(f"{timeframe}={_summary(frame)}")

    if result.base.empty:
        print("\nSMOKE_NO_DATA")
        print("reason=provider returned no rows for the requested window; retry before treating as a symbol/data failure")
        return 3

    if "1h" not in result.derived or result.derived["1h"].empty:
        print("\nSMOKE_NO_ENGINE_INPUT")
        print("reason=1h frame was not produced")
        return 4

    try:
        batch = prepare_engine_input(result.derived["1h"])
    except EngineInputError as exc:
        print("\nSMOKE_NO_ENGINE_INPUT")
        print(f"reason={exc}")
        return 4

    print("\n[engine-input 1h]")
    print(f"quality={batch.source_quality.status.value} rows={len(batch.frame)}")
    if batch.source_quality.warnings:
        print(f"warnings={list(batch.source_quality.warnings)}")

    market = MarketStructureEngine()
    market_results = market.replay(batch.frame)
    print("\n[market-structure 1h]")
    print(f"results={len(market_results)}")
    print(f"snapshot={market.snapshot()}")
    print(f"export={market.export_contract}")

    pattern = PatternCompressionEngine()
    pattern_results = pattern.replay(batch.frame)
    print("\n[pattern-compression 1h]")
    print(f"results={len(pattern_results)}")
    print(f"snapshot={pattern.snapshot()}")
    print(f"export={pattern.export_contract}")

    print("\nSMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
