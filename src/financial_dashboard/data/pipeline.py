from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from .bist_session import (
    BistEquitySession,
    bist_target_timeframes,
    filter_bist_session,
    resample_bist,
)
from .parquet_store import ParquetOHLCVStore
from .provider import MarketDataProvider
from .resampler import ResamplePolicy, resample_ohlcv


_BIST_BASE_MINUTES = {
    "5m": 5,
    "15m": 15,
}


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

    def refresh_bist(
        self,
        *,
        symbol: str,
        base_timeframe: str,
        start: datetime,
        end: datetime,
        session: BistEquitySession | None = None,
        target_timeframes: tuple[str, ...] | None = None,
    ) -> PipelineResult:
        base_key = base_timeframe.strip().lower()
        if base_key not in _BIST_BASE_MINUTES:
            raise ValueError(f"unsupported BIST base timeframe: {base_timeframe}")

        session = session or BistEquitySession()
        targets = target_timeframes or bist_target_timeframes(base_key)
        fetched = self.provider.get_ohlcv(symbol, base_key, start, end)
        filtered = filter_bist_session(fetched, session)
        source = str(filtered["source"].iloc[-1]) if not filtered.empty and "source" in filtered.columns else self.provider.__class__.__name__
        base = self.store.merge_and_save(
            filtered,
            symbol=symbol,
            timeframe=base_key,
            source=source,
        )

        derived: dict[str, pd.DataFrame] = {}
        for target in targets:
            frame = resample_bist(
                base,
                target,
                base_timeframe=base_key,
                session=session,
            )
            cached = self.store.merge_and_save(
                frame,
                symbol=symbol,
                timeframe=target,
                source=source,
            )
            derived[target] = cached
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
        return self.refresh_bist(
            symbol=symbol,
            base_timeframe="5m",
            start=start,
            end=end,
            session=session,
            target_timeframes=target_timeframes,
        )

    def refresh_bist_15m(
        self,
        *,
        symbol: str,
        start: datetime,
        end: datetime,
        session: BistEquitySession | None = None,
        target_timeframes: tuple[str, ...] | None = None,
    ) -> PipelineResult:
        return self.refresh_bist(
            symbol=symbol,
            base_timeframe="15m",
            start=start,
            end=end,
            session=session,
            target_timeframes=target_timeframes,
        )

    def incremental_bist_start(
        self,
        *,
        symbol: str,
        requested_start: datetime,
        base_timeframe: str = "5m",
        overlap_bars: int = 1,
    ) -> datetime:
        """Return a safe incremental BIST fetch start with overlap for replacement."""
        if overlap_bars < 0:
            raise ValueError("overlap_bars must be non-negative")
        base_key = base_timeframe.strip().lower()
        try:
            base_minutes = _BIST_BASE_MINUTES[base_key]
        except KeyError as exc:
            raise ValueError(f"unsupported BIST base timeframe: {base_timeframe}") from exc

        latest = self.store.latest_timestamp(symbol, base_key)
        if latest is None:
            return requested_start

        requested = pd.Timestamp(requested_start)
        if requested.tzinfo is None and latest.tzinfo is not None:
            requested = requested.tz_localize(latest.tzinfo)
        elif requested.tzinfo is not None and latest.tzinfo is not None:
            requested = requested.tz_convert(latest.tzinfo)

        candidate = latest - pd.Timedelta(minutes=base_minutes * overlap_bars)
        start = max(candidate, requested)
        return start.to_pydatetime()

    def refresh_bist_incremental(
        self,
        *,
        symbol: str,
        base_timeframe: str,
        requested_start: datetime,
        end: datetime,
        session: BistEquitySession | None = None,
        target_timeframes: tuple[str, ...] | None = None,
        overlap_bars: int = 1,
    ) -> PipelineResult:
        start = self.incremental_bist_start(
            symbol=symbol,
            requested_start=requested_start,
            base_timeframe=base_timeframe,
            overlap_bars=overlap_bars,
        )
        return self.refresh_bist(
            symbol=symbol,
            base_timeframe=base_timeframe,
            start=start,
            end=end,
            session=session,
            target_timeframes=target_timeframes,
        )

    def refresh_bist_5m_incremental(
        self,
        *,
        symbol: str,
        requested_start: datetime,
        end: datetime,
        session: BistEquitySession | None = None,
        target_timeframes: tuple[str, ...] | None = None,
        overlap_bars: int = 1,
    ) -> PipelineResult:
        return self.refresh_bist_incremental(
            symbol=symbol,
            base_timeframe="5m",
            requested_start=requested_start,
            end=end,
            session=session,
            target_timeframes=target_timeframes,
            overlap_bars=overlap_bars,
        )

    def refresh_bist_15m_incremental(
        self,
        *,
        symbol: str,
        requested_start: datetime,
        end: datetime,
        session: BistEquitySession | None = None,
        target_timeframes: tuple[str, ...] | None = None,
        overlap_bars: int = 1,
    ) -> PipelineResult:
        return self.refresh_bist_incremental(
            symbol=symbol,
            base_timeframe="15m",
            requested_start=requested_start,
            end=end,
            session=session,
            target_timeframes=target_timeframes,
            overlap_bars=overlap_bars,
        )
