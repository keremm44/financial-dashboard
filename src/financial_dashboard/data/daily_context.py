from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from .parquet_store import ParquetOHLCVStore
from .provider import MarketDataProvider
from .schema import CANONICAL_COLUMNS, REQUIRED_OHLCV_COLUMNS, SchemaError


@dataclass(frozen=True, slots=True)
class DailyContextResult:
    daily: pd.DataFrame
    weekly: pd.DataFrame


def resample_daily_to_weekly(
    daily: pd.DataFrame,
    *,
    as_of: datetime | pd.Timestamp,
    timezone: str = "Europe/Istanbul",
    friday_close: str = "18:10",
) -> pd.DataFrame:
    """Aggregate closed daily bars into deterministic Monday-labelled weeks.

    A holiday week may legitimately contain fewer than five sessions, so completeness
    never depends on a fixed source-bar count. Historical weeks are complete when the
    week has passed and every available daily source bar is complete. The current week
    remains open until Friday session close.
    """
    if daily.empty:
        return pd.DataFrame(columns=(*CANONICAL_COLUMNS, "source_count"))
    missing = [column for column in REQUIRED_OHLCV_COLUMNS if column not in daily.columns]
    if missing:
        raise SchemaError(f"Missing required OHLCV columns: {', '.join(missing)}")

    work = daily.copy()
    work["timestamp"] = pd.to_datetime(work["timestamp"], errors="raise")
    work = work.sort_values("timestamp", kind="stable")
    if work["timestamp"].duplicated().any():
        raise ValueError("Duplicate daily timestamps must be resolved before weekly resampling")

    localized = work["timestamp"]
    if localized.dt.tz is None:
        localized = localized.dt.tz_localize(timezone)
    else:
        localized = localized.dt.tz_convert(timezone)
    work["timestamp"] = localized
    day = localized.dt.normalize()
    week_start = day - pd.to_timedelta(localized.dt.weekday, unit="D")
    work["week_start"] = week_start

    rows: list[dict[str, object]] = []
    as_of_ts = pd.Timestamp(as_of)
    if as_of_ts.tzinfo is None:
        as_of_ts = as_of_ts.tz_localize(timezone)
    else:
        as_of_ts = as_of_ts.tz_convert(timezone)
    close_hour, close_minute = (int(part) for part in friday_close.split(":", 1))

    for label, group in work.groupby("week_start", sort=True):
        group = group.sort_values("timestamp", kind="stable")
        friday = pd.Timestamp(label) + pd.Timedelta(days=4, hours=close_hour, minutes=close_minute)
        week_closed = as_of_ts >= friday
        upstream_closed = bool(group["is_closed"].all()) if "is_closed" in group else True
        upstream_complete = bool(group["is_complete"].all()) if "is_complete" in group else True
        rows.append(
            {
                "timestamp": friday,
                "open": group["open"].iloc[0],
                "high": group["high"].max(),
                "low": group["low"].min(),
                "close": group["close"].iloc[-1],
                "volume": group["volume"].sum(),
                "symbol": str(group["symbol"].iloc[0]) if "symbol" in group else "",
                "timeframe": "1w",
                "is_closed": week_closed and upstream_closed,
                "is_complete": week_closed and upstream_closed and upstream_complete,
                "source": str(group["source"].iloc[0]) if "source" in group else "",
                "source_count": int(len(group)),
            }
        )

    return pd.DataFrame(rows, columns=(*CANONICAL_COLUMNS, "source_count"))


class DailyContextPipeline:
    """EOD-style daily cache plus locally derived weekly context."""

    def __init__(
        self,
        provider: MarketDataProvider,
        store: ParquetOHLCVStore,
        *,
        timezone: str = "Europe/Istanbul",
    ) -> None:
        self.provider = provider
        self.store = store
        self.timezone = timezone

    def incremental_start(
        self,
        *,
        symbol: str,
        requested_start: datetime,
        overlap_days: int = 7,
    ) -> datetime:
        if overlap_days < 0:
            raise ValueError("overlap_days must be non-negative")
        latest = self.store.latest_timestamp(symbol, "1d")
        if latest is None:
            return requested_start
        requested = pd.Timestamp(requested_start)
        if requested.tzinfo is None and latest.tzinfo is not None:
            requested = requested.tz_localize(latest.tzinfo)
        elif requested.tzinfo is not None and latest.tzinfo is not None:
            requested = requested.tz_convert(latest.tzinfo)
        candidate = latest - pd.Timedelta(days=overlap_days)
        return max(candidate, requested).to_pydatetime()

    def refresh(
        self,
        *,
        symbol: str,
        requested_start: datetime,
        end: datetime,
        overlap_days: int = 7,
    ) -> DailyContextResult:
        start = self.incremental_start(
            symbol=symbol,
            requested_start=requested_start,
            overlap_days=overlap_days,
        )
        fetched = self.provider.get_ohlcv(symbol, "1d", start, end)
        source = (
            str(fetched["source"].iloc[-1])
            if not fetched.empty and "source" in fetched.columns
            else self.provider.__class__.__name__
        )
        daily = self.store.merge_and_save(fetched, symbol=symbol, timeframe="1d", source=source)
        weekly_raw = resample_daily_to_weekly(daily, as_of=end, timezone=self.timezone)
        weekly = self.store.merge_and_save(
            weekly_raw,
            symbol=symbol,
            timeframe="1w",
            source=source,
        )
        return DailyContextResult(daily=daily, weekly=weekly)
