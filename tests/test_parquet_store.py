from __future__ import annotations

import pandas as pd

from financial_dashboard.data.parquet_store import ParquetOHLCVStore


def _frame(timestamps, closes) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(timestamps),
            "open": [value - 0.5 for value in closes],
            "high": [value + 0.5 for value in closes],
            "low": [value - 1.0 for value in closes],
            "close": closes,
            "volume": [100.0] * len(closes),
            "symbol": ["THYAO"] * len(closes),
            "timeframe": ["5m"] * len(closes),
            "is_closed": [True] * len(closes),
            "is_complete": [True] * len(closes),
            "source": ["tvdatafeed"] * len(closes),
        }
    )


def test_store_merges_incrementally_and_newer_duplicate_wins(tmp_path) -> None:
    store = ParquetOHLCVStore(tmp_path)
    first = _frame(
        ["2026-08-19T10:00:00+03:00", "2026-08-19T10:05:00+03:00"],
        [100.0, 101.0],
    )
    second = _frame(
        ["2026-08-19T10:05:00+03:00", "2026-08-19T10:10:00+03:00"],
        [101.7, 102.0],
    )

    store.merge_and_save(first, symbol="THYAO", timeframe="5m", source="tvdatafeed")
    merged = store.merge_and_save(second, symbol="THYAO", timeframe="5m", source="tvdatafeed")

    assert len(merged) == 3
    assert merged["timestamp"].is_monotonic_increasing
    assert merged.loc[merged["timestamp"] == pd.Timestamp("2026-08-19T10:05:00+03:00"), "close"].item() == 101.7
    assert store.latest_timestamp("THYAO", "5m") == pd.Timestamp("2026-08-19T10:10:00+03:00")


def test_store_round_trip_preserves_canonical_columns(tmp_path) -> None:
    store = ParquetOHLCVStore(tmp_path)
    original = _frame(["2026-08-19T10:00:00+03:00"], [100.0])

    store.merge_and_save(original, symbol="THYAO", timeframe="5m", source="tvdatafeed")
    loaded = store.load("THYAO", "5m")

    assert loaded.iloc[0]["symbol"] == "THYAO"
    assert loaded.iloc[0]["timeframe"] == "5m"
    assert loaded.iloc[0]["source"] == "tvdatafeed"
    assert bool(loaded.iloc[0]["is_closed"])
    assert bool(loaded.iloc[0]["is_complete"])
