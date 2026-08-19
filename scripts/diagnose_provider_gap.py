from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from financial_dashboard.data.tvdatafeed_provider import TvDatafeedProvider


TZ = ZoneInfo("Europe/Istanbul")


def _fetch(provider: TvDatafeedProvider, timeframe: str, start: datetime, end: datetime):
    frame = provider.get_ohlcv("THYAO", timeframe, start, end)
    status = provider.last_volume_status
    return frame, status


def _print_frame(label: str, frame, status: str) -> None:
    print(f"{label} rows={len(frame)} volume_status={status}")
    if frame.empty:
        return
    print(f"[{label} timestamps]")
    print(", ".join(ts.strftime("%H:%M") for ts in frame["timestamp"]))
    print(f"[{label} OHLCV]")
    for _, row in frame.iterrows():
        print(
            f"{row['timestamp'].strftime('%H:%M')} "
            f"O={row['open']} H={row['high']} L={row['low']} C={row['close']} V={row['volume']}"
        )


def main() -> int:
    provider = TvDatafeedProvider(exchange="BIST", max_bars=5000)
    start = datetime(2026, 7, 31, 17, 0, tzinfo=TZ)
    end = datetime(2026, 7, 31, 18, 0, tzinfo=TZ)

    one, one_status = _fetch(provider, "1m", start, end)
    three, three_status = _fetch(provider, "3m", start, end)
    five, five_status = _fetch(provider, "5m", start, end)
    fifteen, fifteen_status = _fetch(provider, "15m", start, end)

    print("=== PROVIDER GAP DIAGNOSTIC ===")
    print("requested=2026-07-31 17:00..18:00 Europe/Istanbul")
    print("note: get_hist returns the latest n_bars first; requested dates older than that depth filter to zero rows")
    print()

    _print_frame("1m", one, one_status)
    print()
    _print_frame("3m", three, three_status)
    print()
    _print_frame("5m", five, five_status)
    print()
    _print_frame("15m", fifteen, fifteen_status)

    five_times = set(five["timestamp"].dt.strftime("%H:%M")) if not five.empty else set()
    expected_5m = {
        f"17:{minute:02d}" for minute in range(0, 60, 5)
    } | {"18:00"}
    print("\n[native 5m missing in requested hour]")
    print(sorted(expected_5m - five_times))

    print("\n[interpretation]")
    if one.empty:
        print("1m cannot validate this historical gap at max_bars=5000; its latest-5000 history does not reach the requested window.")
    if not three.empty:
        three_gap_window = three[(three["timestamp"].dt.time >= datetime.strptime("17:24", "%H:%M").time()) & (three["timestamp"].dt.time <= datetime.strptime("17:53", "%H:%M").time())]
        print(f"3m bars inside 17:24..17:53 = {len(three_gap_window)}")
    if not fifteen.empty:
        fifteen_gap_window = fifteen[(fifteen["timestamp"].dt.time >= datetime.strptime("17:15", "%H:%M").time()) & (fifteen["timestamp"].dt.time <= datetime.strptime("17:45", "%H:%M").time())]
        print(f"15m bars inside 17:15..17:45 = {len(fifteen_gap_window)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
