import pandas as pd
import pytest

from financial_dashboard.data.schema import CANONICAL_COLUMNS, SchemaError, canonicalize_ohlcv


def test_canonicalize_adds_metadata_and_sorts() -> None:
    raw = pd.DataFrame(
        {
            "timestamp": ["2026-08-19 10:01:00+03:00", "2026-08-19 10:00:00+03:00"],
            "open": [101, 100],
            "high": [102, 101],
            "low": [100, 99],
            "close": [101.5, 100.5],
            "volume": [20, 10],
        }
    )

    result = canonicalize_ohlcv(raw, symbol="TEST", timeframe="1m", source="fixture")

    assert tuple(result.columns) == CANONICAL_COLUMNS
    assert result.iloc[0]["open"] == 100
    assert result["symbol"].tolist() == ["TEST", "TEST"]
    assert result["is_closed"].all()
    assert result["is_complete"].all()


def test_canonicalize_rejects_missing_volume() -> None:
    raw = pd.DataFrame(
        {
            "timestamp": ["2026-08-19 10:00:00+03:00"],
            "open": [100],
            "high": [101],
            "low": [99],
            "close": [100.5],
        }
    )

    with pytest.raises(SchemaError):
        canonicalize_ohlcv(raw, symbol="TEST", timeframe="1m", source="fixture")
