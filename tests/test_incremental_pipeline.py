from __future__ import annotations

from datetime import datetime

import pandas as pd

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.data.pipeline import MarketDataPipeline
from financial_dashboard.data.provider import MarketDataProvider
from financial_dashboard.data.schema import canonicalize_ohlcv


class _SequencedProvider(MarketDataProvider):
    def __init__(self, frames: list[pd.DataFrame]) -> None:
        self.frames = frames
        self.calls: list[tuple[datetime, datetime]] = []

    def get_ohlcv(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        self.calls.append((start, end))
        raw = self.frames[len(self.calls) - 1]
        return canonicalize_ohlcv(raw, symbol=symbol, timeframe=timeframe, source="fixture")


def _frame(times: list[str], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(times),
            "open": [value - 0.2 for value in closes],
            "high": [value + 0.5 for value in closes],
            "low": [value - 0.5 for value in closes],
            "close": closes,
            "volume": [100.0] * len(times),
            "is_closed": [True] * len(times),
            "is_complete": [True] * len(times),
        }
    )


def test_incremental_refresh_overlaps_and_replaces_last_cached_bar(tmp_path) -> None:
    first = _frame(
        [
            "2026-08-19T10:00:00+03:00",
            "2026-08-19T10:05:00+03:00",
            "2026-08-19T10:10:00+03:00",
        ],
        [100.0, 101.0, 102.0],
    )
    second = _frame(
        [
            "2026-08-19T10:05:00+03:00",
            "2026-08-19T10:10:00+03:00",
            "2026-08-19T10:15:00+03:00",
        ],
        [101.0, 202.0, 203.0],
    )
    provider = _SequencedProvider([first, second])
    pipeline = MarketDataPipeline(provider, ParquetOHLCVStore(tmp_path))
    requested = datetime.fromisoformat("2026-08-19T10:00:00+03:00")

    pipeline.refresh_bist_5m_incremental(
        symbol="THYAO",
        requested_start=requested,
        end=datetime.fromisoformat("2026-08-19T10:10:00+03:00"),
        target_timeframes=("15m",),
    )
    result = pipeline.refresh_bist_5m_incremental(
        symbol="THYAO",
        requested_start=requested,
        end=datetime.fromisoformat("2026-08-19T10:15:00+03:00"),
        target_timeframes=("15m",),
    )

    assert provider.calls[1][0] == datetime.fromisoformat("2026-08-19T10:05:00+03:00")
    assert result.base["timestamp"].tolist() == list(
        pd.to_datetime(
            [
                "2026-08-19T10:00:00+03:00",
                "2026-08-19T10:05:00+03:00",
                "2026-08-19T10:10:00+03:00",
                "2026-08-19T10:15:00+03:00",
            ]
        )
    )
    replaced = result.base.loc[result.base["timestamp"] == pd.Timestamp("2026-08-19T10:10:00+03:00")].iloc[0]
    assert replaced["close"] == 202.0
    assert not result.base["timestamp"].duplicated().any()
