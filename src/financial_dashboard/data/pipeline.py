from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from .bist_session import BistEquitySession, bist_target_timeframes, filter_bist_session, resample_bist_5m
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

    def refresh_bist_5m(
        self,
        *,
        symbol: str,
        start: datetime,
        end: datetime,
        session: BistEquitySession | None = None,
        target_timeframes: tuple[str, ...] | None = None,
    ) -> PipelineResult:
        """Refresh a BIST symbol from canonical 5m bars and derive session-safe TFs."""

        session = session or BistEquitySession()
        targets = target_timeframes or bist_target_timeframes()
        invalid = [tf for tf in targets if tf not in bist_target_timeframes()]
        if invalid:
            raise ValueError(f"unsupported BIST target timeframe(s): {', '.join(invalid)}")

        fetched = self.provider.get_ohlcv(symbol, "5m", start, end)
        source = str(fetched["source"].iloc[-1]) if not fetched.empty and "source" in fetched.columns else self.provider.__class__.__name__

        session_fetched = filter_bist_session(fetched, session)
        base = self.store.merge_and_save(
            session_fetched,
            symbol=symbol,
            timeframe="5m",
            source=source,
        )
        # Re-filter the accumulated cache so a stale/out-of-session row from an older
        # provider version can never participate in a derived candle.
        base = filter_bist_session(base, session)

        derived: dict[str, pd.DataFrame] = {}
        for timeframe in targets:
            frame = resample_bist_5m(base, timeframe, session=session)
            cached = self.store.merge_and_save(
                frame,
                symbol=symbol,
                timeframe=timeframe,
                source=source,
            )
            derived[timeframe] = cached

        return PipelineResult(base=base, derived=derived)
