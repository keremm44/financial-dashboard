from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time

import pandas as pd

from .schema import CANONICAL_COLUMNS, REQUIRED_OHLCV_COLUMNS, SchemaError


@dataclass(frozen=True, slots=True)
class BistEquitySession:
    """Session model for Borsa Istanbul Equity Market continuous trading.

    Normal continuous-trading bars are expected from 10:00 (inclusive) to 18:00
    (exclusive), Europe/Istanbul. Holidays and half-days are injected as data rather
    than hard-coded so exchange-calendar changes do not alter resampling math.
    """

    timezone: str = "Europe/Istanbul"
    open_time: time = time(10, 0)
    close_time: time = time(18, 0)
    closed_dates: frozenset[date] = field(default_factory=frozenset)
    close_overrides: dict[date, time] = field(default_factory=dict)

    def close_for(self, session_date: date) -> time:
        return self.close_overrides.get(session_date, self.close_time)

    def is_trading_date(self, session_date: date) -> bool:
        return session_date.weekday() < 5 and session_date not in self.closed_dates


_TARGET_MINUTES = {
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
}


def bist_target_timeframes() -> tuple[str, ...]:
    return ("15m", "30m", "1h", "2h", "4h", "1d")


def _localized_timestamp_series(frame: pd.DataFrame, timezone: str) -> pd.Series:
    ts = pd.to_datetime(frame["timestamp"], errors="raise")
    if ts.dt.tz is None:
        return ts.dt.tz_localize(timezone)
    return ts.dt.tz_convert(timezone)


def filter_bist_session(frame: pd.DataFrame, session: BistEquitySession | None = None) -> pd.DataFrame:
    session = session or BistEquitySession()
    if frame.empty:
        return frame.copy()
    missing = [column for column in REQUIRED_OHLCV_COLUMNS if column not in frame.columns]
    if missing:
        raise SchemaError(f"Missing required OHLCV columns: {', '.join(missing)}")

    work = frame.copy()
    work["timestamp"] = _localized_timestamp_series(work, session.timezone)
    dates = work["timestamp"].dt.date
    clock = work["timestamp"].dt.time

    keep = []
    for session_date, bar_time in zip(dates, clock, strict=True):
        valid_date = session.is_trading_date(session_date)
        close_time = session.close_for(session_date)
        keep.append(valid_date and session.open_time <= bar_time < close_time)
    return work.loc[keep].sort_values("timestamp", kind="stable").reset_index(drop=True)


def _empty_resample() -> pd.DataFrame:
    return pd.DataFrame(columns=(*CANONICAL_COLUMNS, "source_count", "expected_source_count"))


def _bucket_expected_count(
    bucket_start: pd.Timestamp,
    *,
    target_minutes: int | None,
    base_minutes: int,
    session_close: pd.Timestamp,
) -> int:
    if target_minutes is None:  # daily: all remaining session bars from open
        bucket_end = session_close
    else:
        bucket_end = min(bucket_start + pd.Timedelta(minutes=target_minutes), session_close)
    span_minutes = int((bucket_end - bucket_start).total_seconds() // 60)
    return max(0, span_minutes // base_minutes)


def resample_bist_5m(
    frame: pd.DataFrame,
    target_timeframe: str,
    *,
    session: BistEquitySession | None = None,
) -> pd.DataFrame:
    """Resample canonical 5m BIST bars without crossing session boundaries.

    Each trading day is anchored at 10:00. The final bucket of an explicitly
    overridden half-day may be shorter than the nominal target timeframe and is still
    considered complete when every expected 5m source bar for that shortened session
    bucket is present.
    """

    session = session or BistEquitySession()
    key = target_timeframe.strip().lower()
    if key not in bist_target_timeframes():
        raise ValueError(f"unsupported BIST target timeframe: {target_timeframe}")
    if frame.empty:
        return _empty_resample()

    work = filter_bist_session(frame, session)
    if work.empty:
        return _empty_resample()
    if work["timestamp"].duplicated().any():
        raise ValueError("duplicate timestamps must be resolved before BIST resampling")

    target_minutes = _TARGET_MINUTES.get(key)
    outputs: list[dict[str, object]] = []
    for session_date, day in work.groupby(work["timestamp"].dt.date, sort=True):
        open_ts = pd.Timestamp.combine(session_date, session.open_time).tz_localize(session.timezone)
        close_ts = pd.Timestamp.combine(session_date, session.close_for(session_date)).tz_localize(session.timezone)
        day = day.sort_values("timestamp", kind="stable").copy()

        if key == "1d":
            day["bucket"] = open_ts
        else:
            elapsed_minutes = ((day["timestamp"] - open_ts).dt.total_seconds() // 60).astype(int)
            bucket_index = elapsed_minutes // int(target_minutes)
            day["bucket"] = open_ts + pd.to_timedelta(bucket_index * int(target_minutes), unit="m")

        for bucket_start, bucket in day.groupby("bucket", sort=True):
            expected = _bucket_expected_count(
                bucket_start,
                target_minutes=target_minutes,
                base_minutes=5,
                session_close=close_ts,
            )
            source_count = len(bucket)
            upstream_complete = bool(bucket["is_complete"].all()) if "is_complete" in bucket.columns else True
            all_closed = bool(bucket["is_closed"].all()) if "is_closed" in bucket.columns else True
            outputs.append(
                {
                    "timestamp": bucket_start,
                    "open": float(bucket.iloc[0]["open"]),
                    "high": float(bucket["high"].max()),
                    "low": float(bucket["low"].min()),
                    "close": float(bucket.iloc[-1]["close"]),
                    "volume": float(bucket["volume"].sum()),
                    "symbol": str(bucket.iloc[0].get("symbol", "")),
                    "timeframe": key,
                    "is_closed": all_closed,
                    "is_complete": upstream_complete and expected > 0 and source_count == expected,
                    "source": str(bucket.iloc[0].get("source", "")),
                    "source_count": source_count,
                    "expected_source_count": expected,
                }
            )

    return pd.DataFrame(outputs).loc[:, (*CANONICAL_COLUMNS, "source_count", "expected_source_count")]
