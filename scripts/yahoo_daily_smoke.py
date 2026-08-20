from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from financial_dashboard.data.daily_context import DailyContextPipeline
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.data.yahoo_finance_provider import YahooFinanceDailyProvider, YahooFinanceError


TZ = ZoneInfo("Europe/Istanbul")


def run() -> int:
    parser = argparse.ArgumentParser(description="Yahoo Finance daily/weekly BIST smoke")
    parser.add_argument("--symbol", default="THYAO")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--storage", default="storage/yahoo_daily")
    args = parser.parse_args()

    end = datetime.now(TZ)
    start = end - timedelta(days=args.days)

    try:
        result = DailyContextPipeline(
            YahooFinanceDailyProvider(),
            ParquetOHLCVStore(Path(args.storage)),
        ).refresh(
            symbol=args.symbol,
            requested_start=start,
            end=end,
        )
    except YahooFinanceError as exc:
        print(f"YAHOO_SMOKE_ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        message = " ".join(str(exc).split()) or exc.__class__.__name__
        print(f"YAHOO_SMOKE_ERROR: unexpected {exc.__class__.__name__}: {message[:240]}", file=sys.stderr)
        return 3

    print("=== YAHOO DAILY CONTEXT SMOKE ===")
    print(f"symbol={args.symbol} daily_rows={len(result.daily)} weekly_rows={len(result.weekly)}")
    if not result.daily.empty:
        print(f"daily_first={result.daily.iloc[0]['timestamp']} daily_last={result.daily.iloc[-1]['timestamp']}")
    if not result.weekly.empty:
        last = result.weekly.iloc[-1]
        print(
            "weekly_last="
            f"{last['timestamp']} closed={bool(last['is_closed'])} "
            f"complete={bool(last['is_complete'])} source_count={int(last['source_count']) if 'source_count' in last else 'n/a'}"
        )
    print("YAHOO_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
