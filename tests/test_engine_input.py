from __future__ import annotations

import pandas as pd
import pytest

from financial_dashboard.data.engine_input import EngineInputError, prepare_engine_input
from financial_dashboard.data.quality import DataQualityStatus
from financial_dashboard.engines.market_structure import MarketStructureConfig
from financial_dashboard.engines.market_structure_engine import MarketStructureEngine


def _frame() -> pd.DataFrame:
    count = 24
    close = pd.Series([100.0 + i * 0.2 for i in range(count)])
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-08-03 10:00:00+03:00", periods=count, freq="h"),
            "open": close - 0.1,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": [0.0] * count,
            "is_closed": [True] * (count - 1) + [False],
            "is_complete": [True] * (count - 2) + [False, True],
        }
    )


def test_price_only_engine_accepts_limited_volume_but_filters_unsafe_candles() -> None:
    batch = prepare_engine_input(_frame())

    assert batch.source_quality.status == DataQualityStatus.LIMITED
    assert len(batch.frame) == 22
    assert batch.frame["is_closed"].all()
    assert batch.frame["is_complete"].all()

    engine = MarketStructureEngine(
        MarketStructureConfig(external_pivot_len=2, internal_pivot_len=1, atr_length=3)
    )
    results = engine.replay(batch.frame)
    assert len(results) == len(batch.frame)
    assert engine.export_contract is not None
    assert engine.export_contract.handshake == 314159.0


def test_invalid_ohlc_is_blocked_before_engine() -> None:
    frame = _frame()
    frame.loc[0, "high"] = frame.loc[0, "low"] - 1

    with pytest.raises(EngineInputError, match="High is below another OHLC value"):
        prepare_engine_input(frame)
