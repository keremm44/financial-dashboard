from __future__ import annotations

from datetime import datetime

import pandas as pd

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.data.pipeline import MarketDataPipeline
from financial_dashboard.data.provider import MarketDataProvider
from financial_dashboard.data.resampler import ResamplePolicy
from financial_dashboard.data.schema import canonicalize_ohlcv


class _Provider(MarketDataProvider):
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.calls = 0

    def get_ohlcv(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        self.calls += 1
        return canonicalize_ohlcv(
            self.frame,
            symbol=symbol,
            timeframe=timeframe,
            source="fixture",
        )


def _base_frame() -> pd.DataFrame:
    ts = pd.date_range("2026-08-19 10:00:00+03:00", periods=6, freq="5min")
    return pd.DataFrame(
        {
            "timestamp": ts,
            "open": [100, 101, 102, 103, 104, 105],
            "high": [101, 102, 103, 104, 105, 106],
            "low": [99, 100, 101, 102, 103, 104],
            "close": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5],
            "volume": [10, 20, 30, 40, 50, 60],
        }
    )


def test_pipeline_fetches_caches_and_resamples(tmp_path) -> None:
    provider = _Provider(_base_frame())
    store = ParquetOHLCVStore(tmp_path)
    pipeline = MarketDataPipeline(provider, store)
    policy = ResamplePolicy(
        target_timeframe="15m",
        rule="15min",
        expected_base_bars=3,
        origin="start_day",
        offset="10h",
    )

    result = pipeline.refresh(
        symbol="THYAO",
        base_timeframe="5m",
        start=datetime.fromisoformat("2026-08-19T10:00:00+03:00"),
        end=datetime.fromisoformat("2026-08-19T10:30:00+03:00"),
        policies=(policy,),
    )

    assert provider.calls == 1
    assert len(result.base) == 6
    assert len(result.derived["15m"]) == 2
    first = result.derived["15m"].iloc[0]
    assert first["open"] == 100
    assert first["high"] == 103
    assert first["low"] == 99
    assert first["close"] == 102.5
    assert first["volume"] == 60
    assert bool(first["is_complete"])
    assert store.latest_timestamp("THYAO", "5m") == pd.Timestamp("2026-08-19T10:25:00+03:00")
    assert store.latest_timestamp("THYAO", "15m") == pd.Timestamp("2026-08-19T10:15:00+03:00")
