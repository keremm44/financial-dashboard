from datetime import date, time

import pandas as pd

from financial_dashboard.data.bist_session import (
    BistEquitySession,
    filter_bist_session,
    resample_bist,
    resample_bist_5m,
)
from financial_dashboard.data.schema import canonicalize_ohlcv


def _canonical_intraday(start: str, periods: int, minutes: int) -> pd.DataFrame:
    ts = pd.date_range(start, periods=periods, freq=f"{minutes}min", tz="Europe/Istanbul")
    raw = pd.DataFrame(
        {
            "timestamp": ts,
            "open": [100.0 + i for i in range(periods)],
            "high": [101.0 + i for i in range(periods)],
            "low": [99.0 + i for i in range(periods)],
            "close": [100.5 + i for i in range(periods)],
            "volume": [10.0] * periods,
        }
    )
    return canonicalize_ohlcv(
        raw,
        symbol="THYAO",
        timeframe=f"{minutes}m",
        source="fixture",
    )


def _canonical_5m(start: str, periods: int) -> pd.DataFrame:
    return _canonical_intraday(start, periods, 5)


def _canonical_15m(start: str, periods: int) -> pd.DataFrame:
    return _canonical_intraday(start, periods, 15)


def test_filter_bist_session_keeps_only_regular_continuous_window() -> None:
    raw = _canonical_5m("2026-08-19 09:50", 100)
    result = filter_bist_session(raw)

    assert result.iloc[0]["timestamp"].strftime("%H:%M") == "10:00"
    assert result.iloc[-1]["timestamp"].strftime("%H:%M") == "17:55"
    assert len(result) == 96


def test_regular_day_resamples_expected_buckets_from_5m() -> None:
    base = _canonical_5m("2026-08-19 10:00", 96)

    expected = {
        "15m": (32, 3),
        "30m": (16, 6),
        "1h": (8, 12),
        "2h": (4, 24),
        "4h": (2, 48),
        "1d": (1, 96),
    }
    for timeframe, (rows, source_count) in expected.items():
        result = resample_bist_5m(base, timeframe)
        assert len(result) == rows
        assert set(result["source_count"]) == {source_count}
        assert set(result["expected_source_count"]) == {source_count}
        assert result["is_complete"].all()
        assert result["timestamp"].iloc[0].strftime("%H:%M") == "10:00"


def test_regular_day_resamples_expected_buckets_from_15m() -> None:
    base = _canonical_15m("2026-08-19 10:00", 32)

    expected = {
        "30m": (16, 2),
        "1h": (8, 4),
        "2h": (4, 8),
        "4h": (2, 16),
        "1d": (1, 32),
    }
    for timeframe, (rows, source_count) in expected.items():
        result = resample_bist(
            base,
            timeframe,
            base_timeframe="15m",
        )
        assert len(result) == rows
        assert set(result["source_count"]) == {source_count}
        assert set(result["expected_source_count"]) == {source_count}
        assert result["is_complete"].all()
        assert result["timestamp"].iloc[0].strftime("%H:%M") == "10:00"


def test_missing_source_bar_marks_only_affected_bucket_incomplete() -> None:
    base = _canonical_5m("2026-08-19 10:00", 96)
    missing_ts = pd.Timestamp("2026-08-19 10:25", tz="Europe/Istanbul")
    base = base[base["timestamp"] != missing_ts].reset_index(drop=True)

    result = resample_bist_5m(base, "30m")

    assert result.iloc[0]["source_count"] == 5
    assert result.iloc[0]["expected_source_count"] == 6
    assert not bool(result.iloc[0]["is_complete"])
    assert result.iloc[1:]["is_complete"].all()


def test_missing_15m_source_bar_marks_affected_one_hour_bucket_incomplete() -> None:
    base = _canonical_15m("2026-08-19 10:00", 32)
    missing_ts = pd.Timestamp("2026-08-19 10:30", tz="Europe/Istanbul")
    base = base[base["timestamp"] != missing_ts].reset_index(drop=True)

    result = resample_bist(base, "1h", base_timeframe="15m")

    assert result.iloc[0]["source_count"] == 3
    assert result.iloc[0]["expected_source_count"] == 4
    assert not bool(result.iloc[0]["is_complete"])
    assert result.iloc[1:]["is_complete"].all()


def test_half_day_override_allows_short_final_bucket_when_complete_for_session() -> None:
    session = BistEquitySession(close_overrides={date(2026, 8, 19): time(13, 0)})
    base = _canonical_5m("2026-08-19 10:00", 36)

    result = resample_bist_5m(base, "4h", session=session)

    assert len(result) == 1
    assert result.iloc[0]["source_count"] == 36
    assert result.iloc[0]["expected_source_count"] == 36
    assert bool(result.iloc[0]["is_complete"])


def test_closed_date_is_excluded_without_changing_resample_math() -> None:
    session = BistEquitySession(closed_dates=frozenset({date(2026, 8, 19)}))
    base = _canonical_5m("2026-08-19 10:00", 96)

    result = resample_bist_5m(base, "1h", session=session)

    assert result.empty
