from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .quality import DataQualityReport, DataQualityStatus, assess_ohlcv_quality


class EngineInputError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EngineInputBatch:
    frame: pd.DataFrame
    source_quality: DataQualityReport


def prepare_engine_input(frame: pd.DataFrame) -> EngineInputBatch:
    """Return only closed+complete candles after structural data-quality validation.

    DATA_INVALID blocks the engine. DATA_LIMITED may still be usable after filtering;
    for example zero volume is acceptable for a price-only engine such as Market
    Structure, while incomplete/open candles are excluded from confirmed replay.

    The caller-owned input is never mutated. Filtering, sorting and index reset all
    produce a new frame, so an eager full-frame ``copy()`` here only duplicated the
    same OHLCV payload before those transformations and added avoidable runtime/memory
    cost on every timeframe replay.
    """
    report = assess_ohlcv_quality(frame)
    if report.status is DataQualityStatus.INVALID:
        raise EngineInputError("; ".join(report.errors) or "Invalid market data")

    safe = frame
    if "is_closed" in safe.columns:
        safe = safe[safe["is_closed"].fillna(False).astype(bool)]
    if "is_complete" in safe.columns:
        safe = safe[safe["is_complete"].fillna(False).astype(bool)]
    safe = safe.sort_values("timestamp", kind="stable").reset_index(drop=True)

    if safe.empty:
        raise EngineInputError("No closed and complete candles available for engine input")

    structural = assess_ohlcv_quality(safe)
    if structural.status is DataQualityStatus.INVALID:
        raise EngineInputError("; ".join(structural.errors) or "Invalid filtered market data")

    return EngineInputBatch(frame=safe, source_quality=report)
