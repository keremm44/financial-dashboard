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

_BASE_MINUTES = {
    "5m": 5,
    "15m": 15,
}


def bist_target_timeframes(base_timeframe: str = "5m") -> tuple[str, ...]:
    key = base_timeframe.strip().lower()
    if key == "5m":
        return ("15m", "30m", "1h", "2h", "4h", "1d")
    if key == "15m":
        return ("30m", "1h", "2h", "4h", "1d")
    raise ValueError(f"unsupported BIST base timeframe: {base_timeframe}")


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


def resample_bist(
    frame: pd.DataFrame,
    target_timeframe: str,
    *,
    base_timeframe: str,
    session: BistEquitySession | None = None,
) -> pd.DataFrame:
    """Resample canonical BIST intraday bars without crossing session boundaries.

    Supported base timeframes are 5m and 15m. Each trading day is anchored at 10:00.
    Completeness is evaluated against the selected base timeframe, so a normal daily
    bar expects 96 source bars from 5m input and 32 source bars from 15m input.
    """

    session = session or BistEquitySession()
    base_key = base_timeframe.strip().lower()
    if base_key not in _BASE_MINUTES:
        raise ValueError(f"unsupported BIST base timeframe: {base_timeframe}")
    key = target_timeframe.strip().lower()
    if key not in bist_target_timeframes(base_key):
        raise ValueError(
            f"unsupported BIST target timeframe {target_timeframe} for base {base_timeframe}"
        )
    if frame.empty:
        return _empty_resample()

    work = filter_bist_session(frame, session)
    if work.empty:
        return _empty_resample()
    if work["timestamp"].duplicated().any():
        raise ValueError("duplicate timestamps must be resolved before BIST resampling")

    base_minutes = _BASE_MINUTES[base_key]
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
                base_minutes=base_minutes,
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


def resample_bist_5m(
    frame: pd.DataFrame,
    target_timeframe: str,
    *,
    session: BistEquitySession | None = None,
) -> pd.DataFrame:
    """Backward-compatible 5m BIST resampling wrapper."""

    return resample_bist(
        frame,
        target_timeframe,
        base_timeframe="5m",
        session=session,
    )
