from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from financial_dashboard.data.parquet_store import ParquetOHLCVStore


_FREQUENCIES = {
    "1d": "1D",
    "4h": "4h",
    "2h": "2h",
    "1h": "1h",
    "30m": "30min",
}


def make_ui_store(root: str | Path) -> ParquetOHLCVStore:
    store = ParquetOHLCVStore(root)
    for timeframe, frequency in _FREQUENCIES.items():
        timestamps = pd.date_range(
            "2026-01-01T00:00:00Z",
            periods=160,
            freq=frequency,
        )
        closes = [100.0 + index * 0.035 + 2.4 * math.sin(index / 5) for index in range(160)]
        opens = [closes[0], *closes[:-1]]
        frame = pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": opens,
                "high": [max(open_, close) + 0.45 for open_, close in zip(opens, closes)],
                "low": [min(open_, close) - 0.45 for open_, close in zip(opens, closes)],
                "close": closes,
                "volume": [1000.0 + index * 3 for index in range(160)],
                "is_closed": True,
                "is_complete": True,
            }
        )
        store.merge_and_save(
            frame,
            symbol="THYAO",
            timeframe=timeframe,
            source="ui-test",
        )
    return store
