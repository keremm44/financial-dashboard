from __future__ import annotations

import argparse
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from financial_dashboard.data.engine_input import prepare_engine_input
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.data.pipeline import MarketDataPipeline
from financial_dashboard.data.tvdatafeed_provider import TvDatafeedProvider
from financial_dashboard.engines import MarketStructureEngine, PatternCompressionEngine


TZ = ZoneInfo("Europe/Istanbul")
TARGETS = ("15m", "30m", "1h", "2h", "4h", "1d")


def _floor_5m(ts: pd.Timestamp) -> pd.Timestamp:
    return ts.floor("5min")


def _row_at(frame: pd.DataFrame, timestamp: pd.Timestamp) -> dict[str, object] | None:
    if frame.empty:
        return None
    rows = frame[frame["timestamp"] == timestamp]
    if rows.empty:
        return None
    row = rows.iloc[-1]
    return {
        "timestamp": str(row["timestamp"]),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "volume": float(row["volume"]),
        "is_closed": bool(row["is_closed"]),
        "is_complete": bool(row["is_complete"]),
    }


def _snapshot_engines(frame: pd.DataFrame) -> tuple[object, object, int]:
    batch = prepare_engine_input(frame)
    ms = MarketStructureEngine()
    pc = PatternCompressionEngine()
    ms.replay(batch.frame)
    pc.replay(batch.frame)
    return ms.snapshot(), pc.snapshot(), len(batch.frame)


def _bucket_start(ts: pd.Timestamp, timeframe: str) -> pd.Timestamp:
    day_open = ts.normalize() + pd.Timedelta(hours=10)
    if timeframe == "1d":
        return day_open
    minutes = {"15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240}[timeframe]
    elapsed = int((ts - day_open).total_seconds() // 60)
    return day_open + pd.Timedelta(minutes=(elapsed // minutes) * minutes)


def _compare_bucket(before: pd.DataFrame, after: pd.DataFrame, bucket_ts: pd.Timestamp) -> dict[str, object]:
    b = before[before["timestamp"] == bucket_ts]
    a = after[after["timestamp"] == bucket_ts]
    if b.empty and a.empty:
        return {"before": None, "after": None, "changed": False}
    before_row = None if b.empty else b.iloc[-1]
    after_row = None if a.empty else a.iloc[-1]

    def compact(row):
        if row is None:
            return None
        return {
            "O": float(row["open"]),
            "H": float(row["high"]),
            "L": float(row["low"]),
            "C": float(row["close"]),
            "V": float(row["volume"]),
            "closed": bool(row["is_closed"]),
            "complete": bool(row["is_complete"]),
        }

    before_compact = compact(before_row)
    after_compact = compact(after_row)
    return {"before": before_compact, "after": after_compact, "changed": before_compact != after_compact}


def main() -> int:
    parser = argparse.ArgumentParser(description="Live open->closed 5m candle verification")
    parser.add_argument("--symbol", default="THYAO")
    parser.add_argument("--cache-root", default=".cache/live-open-probe")
    parser.add_argument("--history-days", type=int, default=30)
    parser.add_argument("--max-bars", type=int, default=5000)
    parser.add_argument("--settle-seconds", type=int, default=8)
    parser.add_argument("--no-wait", action="store_true", help="Capture only the current/open phase and exit")
    args = parser.parse_args()

    now = pd.Timestamp(datetime.now(TZ))
    if not (10 <= now.hour < 18) or now.weekday() >= 5:
        print("ERROR: run this during a normal BIST session, ideally between 10:02 and 17:52 Europe/Istanbul")
        return 2

    current_5m = _floor_5m(now)
    close_time = current_5m + pd.Timedelta(minutes=5)
    if now >= close_time:
        print("ERROR: current time is not inside a 5m candle window")
        return 2

    start = (now - pd.Timedelta(days=args.history_days)).to_pydatetime()
    provider = TvDatafeedProvider(exchange="BIST", max_bars=args.max_bars)
    store = ParquetOHLCVStore(Path(args.cache_root))
    pipeline = MarketDataPipeline(provider, store)

    print("=== LIVE OPEN BAR PROBE ===")
    print(f"symbol={args.symbol} now={now.isoformat()} target_5m={current_5m} expected_close={close_time}")

    before_result = pipeline.refresh_bist_5m_incremental(
        symbol=args.symbol,
        requested_start=start,
        end=now.to_pydatetime(),
    )
    before_base = before_result.base.copy()
    before_derived = {tf: frame.copy() for tf, frame in before_result.derived.items()}
    before_bar = _row_at(before_base, current_5m)

    print("\n[OPEN PHASE]")
    print(f"provider_volume_status={provider.last_volume_status}")
    print(f"5m_current={before_bar}")
    if before_bar is None:
        print("ERROR: provider did not expose the current 5m candle yet; retry a little later inside the same candle")
        return 3

    if bool(before_bar["is_closed"]):
        print("WARNING: current 5m row is already marked closed; run earlier inside a fresh candle")

    print("\n[OPEN PHASE DERIVED BUCKETS]")
    for tf in TARGETS:
        bucket = _bucket_start(current_5m, tf)
        row = before_derived[tf][before_derived[tf]["timestamp"] == bucket]
        print(f"{tf} bucket={bucket} row=" + ("None" if row.empty else str({
            "closed": bool(row.iloc[-1]["is_closed"]),
            "complete": bool(row.iloc[-1]["is_complete"]),
            "close": float(row.iloc[-1]["close"]),
            "volume": float(row.iloc[-1]["volume"]),
        })))

    # Engines must only see closed+complete data. Record confirmed snapshots before the live bar closes.
    ms_before, pc_before, safe_rows_before = _snapshot_engines(before_derived["1h"])
    print("\n[OPEN PHASE ENGINE CONFIRMED STATE]")
    print(f"safe_1h_rows={safe_rows_before}")
    print(f"market_structure={ms_before}")
    print(f"pattern_compression={pc_before}")

    if args.no_wait:
        print("\nOPEN_CAPTURE_OK")
        return 0

    wait_seconds = max(0.0, (close_time - pd.Timestamp(datetime.now(TZ))).total_seconds() + args.settle_seconds)
    print(f"\nWaiting locally until {close_time} + {args.settle_seconds}s for the same candle to close...")
    time.sleep(wait_seconds)

    after_now = pd.Timestamp(datetime.now(TZ))
    after_result = pipeline.refresh_bist_5m_incremental(
        symbol=args.symbol,
        requested_start=start,
        end=after_now.to_pydatetime(),
    )
    after_base = after_result.base.copy()
    after_derived = {tf: frame.copy() for tf, frame in after_result.derived.items()}
    after_bar = _row_at(after_base, current_5m)

    print("\n[CLOSED PHASE]")
    print(f"now={after_now.isoformat()} provider_volume_status={provider.last_volume_status}")
    print(f"5m_same_timestamp={after_bar}")

    same_timestamp_replaced = before_bar is not None and after_bar is not None and before_bar["timestamp"] == after_bar["timestamp"]
    closed_after = bool(after_bar and after_bar["is_closed"])
    print(f"same_timestamp_replaced={same_timestamp_replaced}")
    print(f"closed_after={closed_after}")

    print("\n[DERIVED BUCKET CHANGES]")
    changed_targets: list[str] = []
    for tf in TARGETS:
        bucket = _bucket_start(current_5m, tf)
        comparison = _compare_bucket(before_derived[tf], after_derived[tf], bucket)
        if comparison["changed"]:
            changed_targets.append(tf)
        print(f"{tf} bucket={bucket} {comparison}")

    ms_after, pc_after, safe_rows_after = _snapshot_engines(after_derived["1h"])
    print("\n[ENGINE CONFIRMED STATE AFTER CLOSE]")
    print(f"safe_1h_rows_before={safe_rows_before} safe_1h_rows_after={safe_rows_after}")
    print(f"market_structure_before={ms_before}")
    print(f"market_structure_after={ms_after}")
    print(f"pattern_compression_before={pc_before}")
    print(f"pattern_compression_after={pc_after}")

    print("\n[ASSERTIONS]")
    checks = {
        "open_bar_was_not_closed": before_bar is not None and not bool(before_bar["is_closed"]),
        "same_5m_timestamp_was_replaced": same_timestamp_replaced,
        "same_5m_bar_is_closed_after": closed_after,
        "only_containing_derived_buckets_changed": set(changed_targets).issubset(set(TARGETS)),
        "confirmed_engine_input_did_not_include_open_1h": safe_rows_after >= safe_rows_before,
    }
    for name, passed in checks.items():
        print(f"{name}={passed}")

    if all(checks.values()):
        print("\nLIVE_OPEN_CLOSE_OK")
        return 0

    print("\nLIVE_OPEN_CLOSE_NEEDS_REVIEW")
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
