from __future__ import annotations

import math

import pandas as pd

from financial_dashboard.engines.support_resistance_engine import SupportResistanceRangeEngine
from financial_dashboard.engines.support_resistance_runtime_engine import RuntimeSupportResistanceRangeEngine


def _bars(count: int = 360) -> list[dict[str, object]]:
    base = pd.Timestamp("2025-01-01", tz="UTC")
    rows: list[dict[str, object]] = []
    for i in range(count):
        center = 100.0 + math.sin(i / 31.0) * 0.22
        swing = math.sin(i * math.pi / 5.0) * 2.7
        close = center + swing
        open_ = center + math.sin((i - 1) * math.pi / 5.0) * 2.65
        rows.append(
            {
                "timestamp": base + pd.Timedelta(hours=i),
                "open": open_,
                "high": max(open_, close) + 0.55,
                "low": min(open_, close) - 0.55,
                "close": close,
                "volume": 1_000_000.0,
                "is_closed": True,
                "is_complete": True,
            }
        )
    return rows


def test_runtime_support_resistance_matches_canonical_bar_for_bar() -> None:
    canonical = SupportResistanceRangeEngine()
    runtime = RuntimeSupportResistanceRangeEngine()

    for bar in _bars():
        expected = canonical.update(bar)
        actual = runtime.update(bar)
        assert actual == expected
        assert runtime.export_contract == canonical.export_contract
        assert runtime.zones == canonical.zones
        assert runtime.confirmed_pivots == canonical.confirmed_pivots
