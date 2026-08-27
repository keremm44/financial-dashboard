from __future__ import annotations

from dataclasses import fields, is_dataclass
import math
from typing import Any, Mapping

import pandas as pd
import pytest

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


def _assert_semantic_equal(actual: Any, expected: Any) -> None:
    """Require exact structure/state parity while tolerating machine-epsilon float drift."""
    if isinstance(actual, float) or isinstance(expected, float):
        if isinstance(actual, float) and isinstance(expected, float):
            if math.isnan(actual) and math.isnan(expected):
                return
            assert actual == pytest.approx(expected, abs=1e-12, rel=1e-12)
            return
    if is_dataclass(actual) and is_dataclass(expected):
        assert type(actual) is type(expected)
        for field in fields(actual):
            _assert_semantic_equal(getattr(actual, field.name), getattr(expected, field.name))
        return
    if isinstance(actual, Mapping) and isinstance(expected, Mapping):
        assert actual.keys() == expected.keys()
        for key in actual:
            _assert_semantic_equal(actual[key], expected[key])
        return
    if isinstance(actual, (tuple, list)) and isinstance(expected, (tuple, list)):
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected):
            _assert_semantic_equal(left, right)
        return
    assert actual == expected


def test_runtime_support_resistance_matches_canonical_bar_for_bar() -> None:
    canonical = SupportResistanceRangeEngine()
    runtime = RuntimeSupportResistanceRangeEngine()

    for bar in _bars():
        expected = canonical.update(bar)
        actual = runtime.update(bar)
        _assert_semantic_equal(actual, expected)
        _assert_semantic_equal(runtime.export_contract, canonical.export_contract)
        _assert_semantic_equal(runtime.zones, canonical.zones)
        _assert_semantic_equal(runtime.confirmed_pivots, canonical.confirmed_pivots)
