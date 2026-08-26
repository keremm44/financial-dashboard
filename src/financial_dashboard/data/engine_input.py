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


def _is_canonical_range_index(index: pd.Index, row_count: int) -> bool:
    return (
        isinstance(index, pd.RangeIndex)
        and index.start == 0
        and index.stop == row_count
        and index.step == 1
    )


def prepare_engine_input(frame: pd.DataFrame) -> EngineInputBatch:
    """Return only closed+complete candles after structural data-quality validation.

    DATA_INVALID blocks the engine. DATA_LIMITED may still be usable after filtering;
    for example zero volume is acceptable for a price-only engine such as Market
    Structure, while incomplete/open candles are excluded from confirmed replay.

    The caller-owned input is never mutated. The common parquet-cache path is already
    timestamp-sorted, closed and complete, so this function avoids unconditional full
    DataFrame copies, boolean filtering, stable sorting and index rebuilding when those
    transformations would be no-ops. Any non-canonical input still follows the same
    filtering/sort/reset contract as before.
    """
    report = assess_ohlcv_quality(frame)
    if report.status is DataQualityStatus.INVALID:
        raise EngineInputError("; ".join(report.errors) or "Invalid market data")

    safe = frame
    if "is_closed" in safe.columns:
        closed = safe["is_closed"].fillna(False).astype(bool)
        if not bool(closed.all()):
            safe = safe[closed]
    if "is_complete" in safe.columns:
        complete = safe["is_complete"].fillna(False).astype(bool)
        if not bool(complete.all()):
            safe = safe[complete]

    if safe.empty:
        raise EngineInputError("No closed and complete candles available for engine input")

    if not safe["timestamp"].is_monotonic_increasing:
        safe = safe.sort_values("timestamp", kind="stable")
    if not _is_canonical_range_index(safe.index, len(safe)):
        safe = safe.reset_index(drop=True)

    structural = assess_ohlcv_quality(safe)
    if structural.status is DataQualityStatus.INVALID:
        raise EngineInputError("; ".join(structural.errors) or "Invalid filtered market data")

    return EngineInputBatch(frame=safe, source_quality=report)
