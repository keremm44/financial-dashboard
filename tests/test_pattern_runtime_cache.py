from __future__ import annotations

import math

import pandas as pd

from financial_dashboard.engines.pattern_compression_engine import PatternCompressionEngine
from financial_dashboard.engines.pattern_compression_runtime_engine import RuntimePatternCompressionEngine


def _frame(rows: int = 180) -> pd.DataFrame:
    data = []
    price = 100.0
    for index in range(rows):
        wave = math.sin(index / 7.0) * 0.55 + math.sin(index / 19.0) * 0.22
        open_ = price
        close = 100.0 + wave + index * 0.005
        high = max(open_, close) + 0.32 + (index % 5) * 0.015
        low = min(open_, close) - 0.30 - (index % 4) * 0.012
        data.append(
            {
                "timestamp": pd.Timestamp("2026-01-01", tz="Europe/Istanbul")
                + pd.Timedelta(hours=index),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1000.0 + (index % 13) * 35.0,
                "is_closed": True,
                "is_complete": True,
            }
        )
        price = close
    return pd.DataFrame(data)


def test_runtime_pattern_cache_preserves_canonical_replay() -> None:
    frame = _frame()
    canonical = PatternCompressionEngine()
    runtime = RuntimePatternCompressionEngine()

    canonical_results = canonical.replay(frame)
    runtime_results = runtime.replay(frame)

    assert runtime_results == canonical_results
    assert runtime.export_contract == canonical.export_contract
    assert runtime.pattern_state == canonical.pattern_state
    assert len(runtime._runtime_highs) == len(runtime._rows)
    assert len(runtime._runtime_lows) == len(runtime._rows)
    assert len(runtime._runtime_closes) == len(runtime._rows)
