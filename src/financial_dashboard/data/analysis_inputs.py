from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES, normalize_timeframes

from .engine_input import EngineInputBatch, prepare_engine_input
from .identity import normalize_symbol
from .parquet_store import ParquetOHLCVStore


@dataclass(frozen=True, slots=True)
class TimeframeInputSnapshot:
    timeframe: str
    raw_frame: pd.DataFrame
    input_batch: EngineInputBatch


@dataclass(frozen=True, slots=True)
class AnalysisInputSnapshot:
    symbol: str
    timeframes: tuple[str, ...]
    by_timeframe: Mapping[str, TimeframeInputSnapshot]
    fingerprint: tuple[tuple[str, int, int], ...]

    def for_timeframe(self, timeframe: str) -> TimeframeInputSnapshot:
        normalized = timeframe.strip().lower()
        try:
            return self.by_timeframe[normalized]
        except KeyError as error:
            raise KeyError(f"analysis input timeframe not loaded: {timeframe}") from error

    def validate_request(self, *, symbol: str, timeframes: tuple[str, ...]) -> None:
        normalized_symbol = normalize_symbol(symbol)
        normalized_timeframes = tuple(tf.strip().lower() for tf in timeframes)
        if normalized_symbol != self.symbol:
            raise ValueError(
                f"analysis input symbol mismatch: {self.symbol!r} != {normalized_symbol!r}"
            )
        missing = tuple(tf for tf in normalized_timeframes if tf not in self.by_timeframe)
        if missing:
            raise ValueError(f"analysis input snapshot missing timeframe(s): {missing!r}")


def cache_fingerprint(
    store: ParquetOHLCVStore,
    *,
    symbol: str,
    timeframes: tuple[str, ...],
) -> tuple[tuple[str, int, int], ...]:
    normalized_symbol = normalize_symbol(symbol)
    rows: list[tuple[str, int, int]] = []
    for timeframe in timeframes:
        path = store.path_for(normalized_symbol, timeframe)
        if path.exists():
            stat = path.stat()
            rows.append((timeframe, stat.st_size, stat.st_mtime_ns))
        else:
            rows.append((timeframe, -1, -1))
    return tuple(rows)


def load_analysis_inputs(
    store: ParquetOHLCVStore,
    *,
    symbol: str,
    timeframes: tuple[str, ...] = ANALYSIS_TIMEFRAMES,
) -> AnalysisInputSnapshot:
    normalized_symbol = normalize_symbol(symbol)
    normalized_timeframes = normalize_timeframes(
        timeframes,
        supported=ANALYSIS_TIMEFRAMES,
        label="analysis input",
    )
    before = cache_fingerprint(
        store,
        symbol=normalized_symbol,
        timeframes=normalized_timeframes,
    )

    snapshots: dict[str, TimeframeInputSnapshot] = {}
    for timeframe in normalized_timeframes:
        raw = store.load(normalized_symbol, timeframe)
        batch = prepare_engine_input(raw)
        snapshots[timeframe] = TimeframeInputSnapshot(
            timeframe=timeframe,
            raw_frame=raw,
            input_batch=batch,
        )

    after = cache_fingerprint(
        store,
        symbol=normalized_symbol,
        timeframes=normalized_timeframes,
    )
    if after != before:
        raise RuntimeError("cache files changed while analysis inputs were loading")

    return AnalysisInputSnapshot(
        symbol=normalized_symbol,
        timeframes=normalized_timeframes,
        by_timeframe=MappingProxyType(snapshots),
        fingerprint=before,
    )


__all__ = [
    "AnalysisInputSnapshot",
    "TimeframeInputSnapshot",
    "cache_fingerprint",
    "load_analysis_inputs",
]
