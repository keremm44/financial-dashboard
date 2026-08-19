from __future__ import annotations

import pandas as pd

from financial_dashboard.engines.market_structure import MarketStructureConfig
from financial_dashboard.engines.market_structure_engine import MarketStructureEngine
from financial_dashboard.engines.market_structure_state import STATE_NEUTRAL


def _frame() -> pd.DataFrame:
    highs = [10, 11, 15, 12, 11, 12, 13, 12, 16, 13, 12, 13]
    lows = [9, 10, 11, 10, 7, 9, 10, 9, 11, 8, 7, 9]
    closes = [9.8, 10.8, 14.2, 11.3, 8.0, 11.2, 12.4, 10.0, 15.2, 9.0, 7.8, 12.0]
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=len(highs), freq="h", tz="UTC"),
            "open": [9.5, 10.5, 12.0, 12.0, 10.5, 10.0, 11.5, 11.0, 13.0, 12.0, 8.5, 10.0],
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [100.0] * len(highs),
            "is_closed": [True] * len(highs),
        }
    )


def test_integrated_engine_exposes_state_score_and_export_contract() -> None:
    engine = MarketStructureEngine(
        MarketStructureConfig(
            external_pivot_len=2,
            internal_pivot_len=1,
            atr_length=3,
            external_min_atr_distance=0.10,
            internal_min_atr_distance=0.10,
            min_tick=0.01,
        )
    )

    results = engine.replay(_frame())

    assert results
    assert results[-1].state != "SWING_READY"
    assert results[-1].score is not None
    assert 0 <= results[-1].score <= 100
    assert engine.export_contract is not None
    assert engine.export_contract.handshake == 314159.0
    assert engine.external_context.state in {
        "STATE_NEUTRAL",
        "STATE_BULLISH",
        "STATE_BEARISH",
        "STATE_TRANSITION_UP",
        "STATE_TRANSITION_DOWN",
    }


def test_integrated_engine_preserves_open_bar_snapshot() -> None:
    engine = MarketStructureEngine(MarketStructureConfig(external_pivot_len=2, internal_pivot_len=1, atr_length=2))
    closed = {
        "timestamp": pd.Timestamp("2026-01-01T10:00:00Z"),
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
        "is_closed": True,
    }
    live = {**closed, "timestamp": pd.Timestamp("2026-01-01T11:00:00Z"), "high": 12.0, "is_closed": False}

    first = engine.update(closed)
    second = engine.update(live)

    assert second is first
    assert engine.external_context.state == STATE_NEUTRAL
