from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from .parquet_store import ParquetOHLCVStore
from .provider import MarketDataProvider
from .resampler import ResamplePolicy, resample_ohlcv


@dataclass(frozen=True, slots=True)
class PipelineResult:
    base: pd.DataFrame
    derived: dict[str, pd.DataFrame]


class MarketDataPipeline:
    """Provider -> canonical cache -> deterministic resample orchestration."""

    def __init__(self, provider: MarketDataProvider, store: ParquetOHLCVStore) -> None:
        self.provider = provider
        self.store = store

    def refresh(
        self,
        *,
        symbol: str,
        base_timeframe: str,
        start: datetime,
        end: datetime,
        policies: tuple[ResamplePolicy, ...] = (),
    ) -> PipelineResult:
        fetched = self.provider.get_ohlcv(symbol, base_timeframe, start, end)
        source = str(fetched["source"].iloc[-1]) if not fetched.empty and "source" in fetched.columns else self.provider.__class__.__name__
        base = self.store.merge_and_save(
            fetched,
            symbol=symbol,
            timeframe=base_timeframe,
            source=source,
        )

        derived: dict[str, pd.DataFrame] = {}
        for policy in policies:
            frame = resample_ohlcv(base, policy)
            cached = self.store.merge_and_save(
                frame,
                symbol=symbol,
                timeframe=policy.target_timeframe,
                source=source,
            )
            derived[policy.target_timeframe] = cached

        return PipelineResult(base=base, derived=derived)
