import pandas as pd

from financial_dashboard.data.resampler import ResamplePolicy, resample_ohlcv
from financial_dashboard.data.schema import canonicalize_ohlcv


def _bars(count: int) -> pd.DataFrame:
    timestamps = pd.date_range("2026-08-19 10:00:00", periods=count, freq="1min", tz="Europe/Istanbul")
    raw = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [100 + i for i in range(count)],
            "high": [101 + i for i in range(count)],
            "low": [99 + i for i in range(count)],
            "close": [100.5 + i for i in range(count)],
            "volume": [10 * (i + 1) for i in range(count)],
        }
    )
    return canonicalize_ohlcv(raw, symbol="TEST", timeframe="1m", source="fixture")


def test_resample_1m_to_5m_preserves_ohlcv_math() -> None:
    result = resample_ohlcv(
        _bars(5),
        ResamplePolicy(target_timeframe="5m", rule="5min", expected_base_bars=5),
    )

    assert len(result) == 1
    bar = result.iloc[0]
    assert bar["open"] == 100
    assert bar["high"] == 105
    assert bar["low"] == 99
    assert bar["close"] == 104.5
    assert bar["volume"] == 150
    assert bar["source_count"] == 5
    assert bool(bar["is_complete"]) is True


def test_resample_marks_missing_base_bar_incomplete() -> None:
    result = resample_ohlcv(
        _bars(4),
        ResamplePolicy(target_timeframe="5m", rule="5min", expected_base_bars=5),
    )

    assert len(result) == 1
    assert result.iloc[0]["source_count"] == 4
    assert bool(result.iloc[0]["is_complete"]) is False
