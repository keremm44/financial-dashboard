from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from financial_dashboard.data.tvdatafeed_provider import TvDatafeedProvider


TZ = ZoneInfo("Europe/Istanbul")


def _resample_1m_to_5m(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    work = frame.copy().set_index("timestamp")
    out = work.resample("5min", label="left", closed="left", origin="start_day").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        source_count=("close", "count"),
    )
    return out.dropna(subset=["open", "high", "low", "close"]).reset_index()


def main() -> int:
    provider = TvDatafeedProvider(exchange="BIST", max_bars=5000)
    start = datetime(2026, 7, 31, 17, 0, tzinfo=TZ)
    end = datetime(2026, 7, 31, 18, 0, tzinfo=TZ)

    one = provider.get_ohlcv("THYAO", "1m", start, end)
    one_status = provider.last_volume_status
    five = provider.get_ohlcv("THYAO", "5m", start, end)
    five_status = provider.last_volume_status
    derived = _resample_1m_to_5m(one)

    print("=== PROVIDER GAP DIAGNOSTIC ===")
    print(f"1m rows={len(one)} volume_status={one_status}")
    print(f"5m rows={len(five)} volume_status={five_status}")

    if not one.empty:
        print("\n[1m timestamps]")
        print(", ".join(ts.strftime("%H:%M") for ts in one["timestamp"]))

    if not five.empty:
        print("\n[native 5m timestamps]")
        print(", ".join(ts.strftime("%H:%M") for ts in five["timestamp"]))

    if not derived.empty:
        print("\n[1m -> 5m buckets]")
        for _, row in derived.iterrows():
            print(
                f"{row['timestamp'].strftime('%H:%M')} count={int(row['source_count'])} "
                f"O={row['open']} H={row['high']} L={row['low']} C={row['close']} V={row['volume']}"
            )

    native_times = set(five["timestamp"].dt.strftime("%H:%M")) if not five.empty else set()
    derived_times = set(derived["timestamp"].dt.strftime("%H:%M")) if not derived.empty else set()
    recoverable = sorted(derived_times - native_times)
    print("\nrecoverable_native_5m_gaps_from_1m=", recoverable)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
