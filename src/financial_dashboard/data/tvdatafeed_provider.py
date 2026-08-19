from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from .provider import MarketDataProvider
from .schema import canonicalize_ohlcv


_TIMEFRAME_TO_INTERVAL_ATTR = {
    "1m": "in_1_minute",
    "3m": "in_3_minute",
    "5m": "in_5_minute",
    "15m": "in_15_minute",
    "30m": "in_30_minute",
    "45m": "in_45_minute",
    "1h": "in_1_hour",
    "2h": "in_2_hour",
    "3h": "in_3_hour",
    "4h": "in_4_hour",
    "1d": "in_daily",
    "1w": "in_weekly",
    "1mo": "in_monthly",
}

_TIMEFRAME_MINUTES = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "45m": 45,
    "1h": 60,
    "2h": 120,
    "3h": 180,
    "4h": 240,
}


class TvDatafeedProvider(MarketDataProvider):
    """Thin tvDatafeed adapter that keeps TradingView-specific behavior at the boundary.

    The adapter does not trust volume availability silently. `last_volume_status` is
    updated after every fetch as VALID, PARTIAL or UNAVAILABLE. The returned frame
    itself stays on the canonical OHLCV contract so analysis engines remain provider
    agnostic.
    """

    source = "tvdatafeed"

    def __init__(
        self,
        *,
        exchange: str = "BIST",
        timezone: str = "Europe/Istanbul",
        max_bars: int = 5000,
        client: Any | None = None,
        interval_enum: Any | None = None,
        volume_type: str = "TRADE_VOLUME",
    ) -> None:
        if max_bars < 1:
            raise ValueError("max_bars must be positive")
        self.exchange = exchange
        self.timezone = ZoneInfo(timezone)
        self.max_bars = max_bars
        self.volume_type = volume_type
        self.last_volume_status = "UNKNOWN"
        self._client = client
        self._interval_enum = interval_enum

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        try:
            from tvDatafeed import Interval, TvDatafeed
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "tvDatafeed is not installed. Install a compatible tvDatafeed package "
                "in the local runtime or inject a client for tests."
            ) from exc
        self._client = TvDatafeed()
        self._interval_enum = Interval

    def _resolve_interval(self, timeframe: str) -> Any:
        key = timeframe.strip().lower()
        attr = _TIMEFRAME_TO_INTERVAL_ATTR.get(key)
        if attr is None:
            raise ValueError(f"unsupported tvDatafeed timeframe: {timeframe}")
        if self._interval_enum is None:
            return key
        try:
            return getattr(self._interval_enum, attr)
        except AttributeError as exc:
            raise RuntimeError(f"tvDatafeed Interval is missing {attr}") from exc

    def _normalize_timestamp(self, values: pd.Series) -> pd.Series:
        parsed = pd.to_datetime(values, errors="raise")
        if parsed.dt.tz is None:
            return parsed.dt.tz_localize(self.timezone)
        return parsed.dt.tz_convert(self.timezone)

    def _mark_closed(self, frame: pd.DataFrame, timeframe: str, now: datetime) -> pd.Series:
        key = timeframe.strip().lower()
        if frame.empty:
            return pd.Series(dtype="bool")
        now_ts = pd.Timestamp(now)
        if now_ts.tzinfo is None:
            now_ts = now_ts.tz_localize(self.timezone)
        else:
            now_ts = now_ts.tz_convert(self.timezone)

        minutes = _TIMEFRAME_MINUTES.get(key)
        if minutes is not None:
            return frame["timestamp"] + pd.Timedelta(minutes=minutes) <= now_ts
        if key == "1d":
            return frame["timestamp"].dt.date < now_ts.date()
        return pd.Series(True, index=frame.index, dtype="bool")

    @staticmethod
    def _volume_status(volume: pd.Series) -> str:
        if volume.empty:
            return "UNAVAILABLE"
        numeric = pd.to_numeric(volume, errors="coerce")
        missing_or_zero = numeric.isna() | (numeric <= 0)
        if missing_or_zero.all():
            return "UNAVAILABLE"
        if missing_or_zero.any():
            return "PARTIAL"
        return "VALID"

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        self._ensure_client()
        interval = self._resolve_interval(timeframe)
        raw = self._client.get_hist(
            symbol=symbol,
            exchange=self.exchange,
            interval=interval,
            n_bars=self.max_bars,
            extended_session=False,
        )
        if raw is None or raw.empty:
            self.last_volume_status = "UNAVAILABLE"
            return canonicalize_ohlcv(
                pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"]),
                symbol=symbol,
                timeframe=timeframe,
                source=self.source,
            )

        work = raw.reset_index().copy()
        if "datetime" in work.columns:
            work = work.rename(columns={"datetime": "timestamp"})
        elif work.columns[0] != "timestamp":
            work = work.rename(columns={work.columns[0]: "timestamp"})

        work["timestamp"] = self._normalize_timestamp(work["timestamp"])
        if "volume" not in work.columns:
            work["volume"] = 0.0
        self.last_volume_status = self._volume_status(work["volume"])

        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        start_ts = start_ts.tz_localize(self.timezone) if start_ts.tzinfo is None else start_ts.tz_convert(self.timezone)
        end_ts = end_ts.tz_localize(self.timezone) if end_ts.tzinfo is None else end_ts.tz_convert(self.timezone)
        work = work[(work["timestamp"] >= start_ts) & (work["timestamp"] <= end_ts)].copy()
        work["symbol"] = symbol
        work["timeframe"] = timeframe
        work["source"] = self.source
        work["is_closed"] = self._mark_closed(work, timeframe, end)
        work["is_complete"] = True

        return canonicalize_ohlcv(
            work,
            symbol=symbol,
            timeframe=timeframe,
            source=self.source,
            default_is_closed=False,
        )
