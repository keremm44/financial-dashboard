import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.history_single_pass import SinglePassHistoricalDecisionInputReplayRunner
from financial_dashboard.decision.history_source import (
    HistoricalDecisionInputConfig,
    HistoricalDecisionInputReplayRunner,
)


def _bars(start: str, periods: int, freq: str) -> pd.DataFrame:
    timestamp = pd.date_range(start, periods=periods, freq=freq)
    values = [100.0 + i * 0.15 + ((i % 7) - 3) * 0.04 for i in range(periods)]
    return pd.DataFrame(
        {
            "timestamp": timestamp,
            "open": values,
            "high": [value + 1.2 + (i % 3) * 0.05 for i, value in enumerate(values)],
            "low": [value - 1.1 - (i % 2) * 0.04 for i, value in enumerate(values)],
            "close": [value + ((i % 5) - 2) * 0.08 for i, value in enumerate(values)],
            "volume": [100_000.0 + (i % 11) * 2_500.0 for i in range(periods)],
        }
    )


def _store(tmp_path) -> ParquetOHLCVStore:
    store = ParquetOHLCVStore(tmp_path)
    # Volatility/1d engines need >=120 closed bars to leave warmup, so the daily
    # and intraday caches stay above MINIMUM_HISTORY while decision-point history
    # (1h) is kept at the smallest size that still exercises multi-cutoff capture.
    specs = {
        "1d": ("2025-12-01 18:00", 140, "1D"),
        "4h": ("2026-01-01 10:00", 140, "4h"),
        "2h": ("2026-01-01 10:00", 140, "2h"),
        "1h": ("2026-01-01 10:00", 80, "1h"),
        "30m": ("2026-01-01 10:00", 160, "30min"),
    }
    for timeframe in ANALYSIS_TIMEFRAMES:
        start, periods, freq = specs[timeframe]
        store.merge_and_save(
            _bars(start, periods, freq),
            symbol="TEST",
            timeframe=timeframe,
            source="TEST",
        )
    return store


def test_single_pass_snapshots_are_prefix_equivalent_to_legacy_causal_replay(tmp_path):
    store = _store(tmp_path)
    config = HistoricalDecisionInputConfig(max_bars=2)

    legacy = HistoricalDecisionInputReplayRunner(store).replay("TEST", config=config)
    single = SinglePassHistoricalDecisionInputReplayRunner(store).replay("TEST", config=config)

    assert single.cutoffs == legacy.cutoffs
    assert single.decision_timeframe == legacy.decision_timeframe
    assert len(single.snapshots) == len(legacy.snapshots) == 2
    assert single.snapshots == legacy.snapshots
