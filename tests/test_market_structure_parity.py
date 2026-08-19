from __future__ import annotations

import pandas as pd

from financial_dashboard.engines.market_structure import MarketStructureConfig
from financial_dashboard.engines.market_structure_engine import MarketStructureEngine
from financial_dashboard.engines.market_structure_parity import TV_COLUMNS, compare_parity, replay_export


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=10, freq="h", tz="UTC"),
            "open": [10, 10.5, 11, 12, 11, 10, 11, 12, 11, 10],
            "high": [11, 12, 14, 13, 12, 11, 13, 15, 12, 11],
            "low": [9, 10, 10.5, 11, 9, 8, 10, 11, 9, 8],
            "close": [10.5, 11.5, 13, 11.5, 10, 10.5, 12.5, 14, 10, 9],
            "volume": [100.0] * 10,
            "is_closed": [True] * 10,
        }
    )


def _tv_from_python(python_export: pd.DataFrame) -> pd.DataFrame:
    tv = pd.DataFrame({"timestamp": python_export["timestamp"]})
    for field, title in TV_COLUMNS.items():
        tv[title] = python_export[field]
    return tv


def test_parity_harness_passes_identical_contract_output() -> None:
    engine = MarketStructureEngine(
        MarketStructureConfig(external_pivot_len=2, internal_pivot_len=1, atr_length=3, external_min_atr_distance=0.1, internal_min_atr_distance=0.1)
    )
    python_export = replay_export(engine, _frame())
    tv = _tv_from_python(python_export)

    report = compare_parity(tv, python_export)

    assert report.passed
    assert report.compared_rows == len(python_export)
    assert report.compared_values == len(python_export) * len(TV_COLUMNS)


def test_parity_harness_reports_exact_state_mismatch() -> None:
    engine = MarketStructureEngine(
        MarketStructureConfig(external_pivot_len=2, internal_pivot_len=1, atr_length=3, external_min_atr_distance=0.1, internal_min_atr_distance=0.1)
    )
    python_export = replay_export(engine, _frame())
    tv = _tv_from_python(python_export)
    tv.loc[tv.index[-1], "ARGENT | MS | STATE"] = 99.0

    report = compare_parity(tv, python_export)

    assert not report.passed
    assert any(mismatch.field == "external_state" for mismatch in report.mismatches)
