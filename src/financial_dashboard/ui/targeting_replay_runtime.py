from __future__ import annotations

from pathlib import Path

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES, normalize_timeframes
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.targeting_historical_replay import (
    TargetingHistoricalReplay,
    TargetingHistoricalReplayRunner,
)


def replay_cached_targeting_history(
    cache_root: str | Path,
    *,
    symbol: str,
    timeframes: tuple[str, ...] = ANALYSIS_TIMEFRAMES,
    reference_timeframe: str = "1h",
    minimum_bars_per_timeframe: int = 20,
    step: int = 1,
    max_points: int = 10,
) -> TargetingHistoricalReplay:
    normalized = normalize_timeframes(
        timeframes,
        supported=ANALYSIS_TIMEFRAMES,
        label="targeting replay UI",
    )
    return TargetingHistoricalReplayRunner(
        ParquetOHLCVStore(Path(cache_root).expanduser())
    ).replay(
        normalize_symbol(symbol),
        timeframes=normalized,
        reference_timeframe=reference_timeframe,
        minimum_bars_per_timeframe=minimum_bars_per_timeframe,
        step=step,
        max_points=max_points,
    )


__all__ = ["replay_cached_targeting_history"]
