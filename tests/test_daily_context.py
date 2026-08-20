from __future__ import annotations

from datetime import datetime

import pandas as pd

from financial_dashboard.data.daily_context import DailyContextPipeline, resample_daily_to_weekly
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.data.provider import MarketDataProvider


TZ = "Europe/Istanbul"


def _daily(dates: list[str], *, symbol: str = "THYAO") -> pd.DataFrame:
    rows = []
    for i, date in enumerate(dates):
        close = 100.0 + i
        rows.append(
            {
                "timestamp": pd.Timestamp(f"{date} 18:10", tz=TZ),
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1000 + i,
                "symbol": symbol,
                "timeframe": "1d",
                "is_closed": True,
                "is_complete": True,
                "source": "YAHOO_FINANCE",
            }
        )
    return pd.DataFrame(rows)


def test_holiday_week_is_complete_without_requiring_five_sessions() -> None:
    frame = _daily(["2026-08-10", "2026-08-11", "2026-08-13", "2026-08-14"])
    weekly = resample_daily_to_weekly(frame, as_of=pd.Timestamp("2026-08-14 18:30", tz=TZ))
    row = weekly.iloc[0]
    assert row["source_count"] == 4
    assert bool(row["is_closed"])
    assert bool(row["is_complete"])
    assert row["timestamp"] == pd.Timestamp("2026-08-14 18:10", tz=TZ)


def test_current_week_remains_open_before_friday_close() -> None:
    frame = _daily(["2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20"])
    weekly = resample_daily_to_weekly(frame, as_of=pd.Timestamp("2026-08-20 17:00", tz=TZ))
    assert not bool(weekly.iloc[0]["is_closed"])
    assert not bool(weekly.iloc[0]["is_complete"])


def test_incomplete_daily_source_keeps_week_incomplete() -> None:
    frame = _daily(["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"])
    frame.loc[2, "is_complete"] = False
    weekly = resample_daily_to_weekly(frame, as_of=pd.Timestamp("2026-08-14 19:00", tz=TZ))
    assert bool(weekly.iloc[0]["is_closed"])
    assert not bool(weekly.iloc[0]["is_complete"])


def test_future_tail_does_not_rewrite_closed_week() -> None:
    first = _daily(["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"])
    later = pd.concat([first, _daily(["2026-08-17", "2026-08-18"])], ignore_index=True)
    a = resample_daily_to_weekly(first, as_of=pd.Timestamp("2026-08-14 19:00", tz=TZ)).iloc[0]
    b = resample_daily_to_weekly(later, as_of=pd.Timestamp("2026-08-18 19:00", tz=TZ)).iloc[0]
    for column in ("timestamp", "open", "high", "low", "close", "volume", "is_closed", "is_complete"):
        assert a[column] == b[column]


class FakeDailyProvider(MarketDataProvider):
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.calls: list[tuple[pd.Timestamp, pd.Timestamp]] = []

    def get_ohlcv(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        self.calls.append((pd.Timestamp(start), pd.Timestamp(end)))
        mask = (self.frame["timestamp"] >= pd.Timestamp(start)) & (self.frame["timestamp"] <= pd.Timestamp(end))
        return self.frame.loc[mask].copy()


def test_pipeline_caches_daily_derives_weekly_and_uses_incremental_overlap(tmp_path) -> None:
    frame = _daily(
        [
            "2026-08-10",
            "2026-08-11",
            "2026-08-12",
            "2026-08-13",
            "2026-08-14",
            "2026-08-17",
            "2026-08-18",
        ]
    )
    provider = FakeDailyProvider(frame)
    store = ParquetOHLCVStore(tmp_path)
    pipeline = DailyContextPipeline(provider, store)

    first = pipeline.refresh(
        symbol="THYAO",
        requested_start=pd.Timestamp("2026-08-10 00:00", tz=TZ).to_pydatetime(),
        end=pd.Timestamp("2026-08-14 19:00", tz=TZ).to_pydatetime(),
    )
    assert len(first.daily) == 5
    assert len(first.weekly) == 1
    assert bool(first.weekly.iloc[0]["is_complete"])

    second = pipeline.refresh(
        symbol="THYAO",
        requested_start=pd.Timestamp("2026-08-10 00:00", tz=TZ).to_pydatetime(),
        end=pd.Timestamp("2026-08-18 19:00", tz=TZ).to_pydatetime(),
        overlap_days=7,
    )
    assert len(second.daily) == 7
    assert len(second.weekly) == 2
    assert not second.daily["timestamp"].duplicated().any()
    assert provider.calls[1][0] == pd.Timestamp("2026-08-10 18:10", tz=TZ)
