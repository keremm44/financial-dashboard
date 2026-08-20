from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from financial_dashboard.data.daily_context import DailyContextPipeline
from financial_dashboard.data.eodhd_provider import EODHDProvider
from financial_dashboard.data.parquet_store import ParquetOHLCVStore


TZ = ZoneInfo("Europe/Istanbul")


def main() -> int:
    parser = argparse.ArgumentParser(description="EODHD daily/weekly BIST smoke")
    parser.add_argument("--symbol", default="THYAO")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--storage", default="storage/eodhd")
    args = parser.parse_args()

    if not os.getenv("EODHD_API_KEY"):
        raise SystemExit("EODHD_API_KEY is required")

    end = datetime.now(TZ)
    start = end - timedelta(days=args.days)
    provider = EODHDProvider()
    store = ParquetOHLCVStore(Path(args.storage))
    result = DailyContextPipeline(provider, store).refresh(
        symbol=args.symbol,
        requested_start=start,
        end=end,
    )

    print("=== EODHD DAILY CONTEXT SMOKE ===")
    print(f"symbol={args.symbol} daily_rows={len(result.daily)} weekly_rows={len(result.weekly)}")
    if not result.daily.empty:
        print(f"daily_first={result.daily.iloc[0]['timestamp']} daily_last={result.daily.iloc[-1]['timestamp']}")
    if not result.weekly.empty:
        last = result.weekly.iloc[-1]
        print(
            "weekly_last="
            f"{last['timestamp']} closed={bool(last['is_closed'])} "
            f"complete={bool(last['is_complete'])}"
        )
    print("EODHD_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
