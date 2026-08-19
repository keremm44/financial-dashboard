from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from financial_dashboard.data.parquet_store import ParquetOHLCVStore


TZ = ZoneInfo("Europe/Istanbul")
SESSION_OPEN = time(10, 0)
SESSION_CLOSE = time(18, 0)


def expected_5m_index(session_date) -> pd.DatetimeIndex:
    start = pd.Timestamp(datetime.combine(session_date, SESSION_OPEN), tz=TZ)
    end = pd.Timestamp(datetime.combine(session_date, SESSION_CLOSE), tz=TZ) - pd.Timedelta(minutes=5)
    return pd.date_range(start=start, end=end, freq="5min")


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose missing BIST 5m cache bars and incomplete derived buckets")
    parser.add_argument("--symbol", default="THYAO")
    parser.add_argument("--cache-root", default=".cache/live-smoke")
    args = parser.parse_args()

    store = ParquetOHLCVStore(Path(args.cache_root))
    base = store.load(args.symbol, "5m")
    if base.empty:
        print("ERROR: 5m cache is empty")
        return 2

    base = base.copy()
    base["timestamp"] = pd.to_datetime(base["timestamp"], errors="raise")
    if base["timestamp"].dt.tz is None:
        base["timestamp"] = base["timestamp"].dt.tz_localize(TZ)
    else:
        base["timestamp"] = base["timestamp"].dt.tz_convert(TZ)

    print("=== 5M SESSION COUNTS ===")
    grouped = base.groupby(base["timestamp"].dt.date, sort=True)
    gap_days = 0
    for session_date, day in grouped:
        expected = expected_5m_index(session_date)
        actual = pd.DatetimeIndex(day["timestamp"])
        missing = expected.difference(actual)
        extra = actual.difference(expected)
        if len(missing) or len(extra) or len(day) != len(expected):
            gap_days += 1
            print(f"{session_date}: actual={len(day)} expected={len(expected)} missing={len(missing)} extra={len(extra)}")
            if len(missing):
                print("  missing:", ", ".join(ts.strftime("%H:%M") for ts in missing))
            if len(extra):
                print("  extra:", ", ".join(ts.strftime("%H:%M") for ts in extra))

    if gap_days == 0:
        print("No 5m session gaps found.")

    print("\n=== INCOMPLETE DERIVED BUCKETS ===")
    for timeframe in ("15m", "30m", "1h", "2h", "4h", "1d"):
        frame = store.load(args.symbol, timeframe)
        if frame.empty:
            print(f"{timeframe}: cache empty")
            continue
        incomplete = frame[~frame["is_complete"].fillna(False).astype(bool)].copy()
        print(f"{timeframe}: incomplete={len(incomplete)}")
        for _, row in incomplete.iterrows():
            print(
                f"  {row['timestamp']} source_count={row.get('source_count')} "
                f"expected={row.get('expected_source_count')}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
