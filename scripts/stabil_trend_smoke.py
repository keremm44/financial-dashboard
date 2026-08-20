from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from financial_dashboard.data.daily_context import DailyContextPipeline
from financial_dashboard.data.engine_input import prepare_engine_input
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.data.pipeline import MarketDataPipeline
from financial_dashboard.data.tvdatafeed_provider import TvDatafeedProvider
from financial_dashboard.data.yahoo_finance_provider import YahooFinanceDailyProvider
from financial_dashboard.engines import StabilTrendEngine


TZ = ZoneInfo("Europe/Istanbul")


def _summary(frame) -> dict[str, object]:
    if frame.empty:
        return {"rows": 0}
    return {
        "rows": len(frame),
        "first": str(frame.iloc[0]["timestamp"]),
        "last": str(frame.iloc[-1]["timestamp"]),
        "closed": int(frame["is_closed"].fillna(False).astype(bool).sum()),
        "complete": int(frame["is_complete"].fillna(False).astype(bool).sum()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Stabil Trend production-architecture smoke")
    parser.add_argument("--symbol", default="THYAO")
    parser.add_argument("--intraday-days", type=int, default=180)
    parser.add_argument("--daily-days", type=int, default=900)
    parser.add_argument("--cache-root", default=".cache/stabil-smoke")
    parser.add_argument("--max-bars", type=int, default=5000)
    args = parser.parse_args()

    end = datetime.now(TZ)
    intraday_start = end - timedelta(days=args.intraday_days)
    daily_start = end - timedelta(days=args.daily_days)
    store = ParquetOHLCVStore(Path(args.cache_root))

    intraday = MarketDataPipeline(
        TvDatafeedProvider(exchange="BIST", max_bars=args.max_bars),
        store,
    ).refresh_bist_5m_incremental(
        symbol=args.symbol,
        requested_start=intraday_start,
        end=end,
    )
    if "4h" not in intraday.derived or intraday.derived["4h"].empty:
        raise RuntimeError("TradingView pipeline produced no 4h context")

    context = DailyContextPipeline(YahooFinanceDailyProvider(), store).refresh(
        symbol=args.symbol,
        requested_start=daily_start,
        end=end,
    )

    h4 = prepare_engine_input(intraday.derived["4h"]).frame
    daily = prepare_engine_input(context.daily).frame
    weekly = prepare_engine_input(context.weekly).frame

    export = StabilTrendEngine().analyze(weekly, daily, h4)

    print("=== STABIL TREND SMOKE ===")
    print(f"symbol={args.symbol}")
    print(f"weekly={_summary(weekly)}")
    print(f"daily={_summary(daily)}")
    print(f"h4={_summary(h4)}")
    print(f"state={export.state.value} state_code={export.state_code}")
    print(f"health={export.health} risk={export.risk}")
    print(f"reason={export.reason.value} coverage={export.evidence_coverage}/3")

    if not export.ready:
        raise RuntimeError("Stabil Trend export is not ready")
    if export.state_code not in range(1, 8):
        raise RuntimeError(f"invalid Stabil Trend state code: {export.state_code}")
    if export.health is None or export.risk is None:
        raise RuntimeError("Stabil Trend health/risk export missing")

    print("STABIL_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
