from __future__ import annotations

import pandas as pd

from financial_dashboard.data.parquet_store import ParquetOHLCVStore


def _frame(rows: int = 2) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=rows, freq="15min"),
            "open": [10.0 + i for i in range(rows)],
            "high": [11.0 + i for i in range(rows)],
            "low": [9.0 + i for i in range(rows)],
            "close": [10.5 + i for i in range(rows)],
            "volume": [100.0 + i for i in range(rows)],
            "symbol": ["ASELS"] * rows,
            "timeframe": ["15m"] * rows,
            "source": ["test"] * rows,
            "is_closed": [True] * rows,
            "is_complete": [True] * rows,
        }
    )


def test_repeated_loads_share_process_parquet_read(monkeypatch, tmp_path) -> None:
    path = tmp_path / "ASELS__15m.parquet"
    _frame().to_parquet(path, index=False)

    calls = 0
    original = pd.read_parquet

    def counted_read(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", counted_read)

    first_store = ParquetOHLCVStore(tmp_path)
    second_store = ParquetOHLCVStore(tmp_path)

    first = first_store.load("ASELS", "15m")
    second = second_store.load("ASELS", "15m")

    assert calls == 1
    pd.testing.assert_frame_equal(first, second)


def test_cached_load_returns_mutation_isolated_frame(tmp_path) -> None:
    path = tmp_path / "ASELS__15m.parquet"
    _frame().to_parquet(path, index=False)
    store = ParquetOHLCVStore(tmp_path)

    first = store.load("ASELS", "15m")
    first.loc[0, "close"] = -999.0

    second = store.load("ASELS", "15m")

    assert second.loc[0, "close"] == 10.5


def test_rewritten_parquet_uses_new_cache_key(tmp_path) -> None:
    path = tmp_path / "ASELS__15m.parquet"
    _frame(2).to_parquet(path, index=False)
    store = ParquetOHLCVStore(tmp_path)

    first = store.load("ASELS", "15m")
    _frame(3).to_parquet(path, index=False)
    second = store.load("ASELS", "15m")

    assert len(first) == 2
    assert len(second) == 3
