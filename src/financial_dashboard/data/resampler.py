from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .schema import CANONICAL_COLUMNS, REQUIRED_OHLCV_COLUMNS, SchemaError


@dataclass(frozen=True, slots=True)
class ResamplePolicy:
    """Explicit resampling policy.

    `origin` and `offset` exist so exchange-session alignment is never hidden inside
    pandas defaults. A provider/session calendar will choose these values later.
    """

    target_timeframe: str
    rule: str
    expected_base_bars: int
    origin: str = "start_day"
    offset: str | None = None
    closed: str = "left"
    label: str = "left"


class ResampleError(ValueError):
    pass


def resample_ohlcv(frame: pd.DataFrame, policy: ResamplePolicy) -> pd.DataFrame:
    if policy.expected_base_bars <= 0:
        raise ResampleError("expected_base_bars must be positive")

    if frame.empty:
        return pd.DataFrame(columns=(*CANONICAL_COLUMNS, "source_count"))

    missing = [column for column in REQUIRED_OHLCV_COLUMNS if column not in frame.columns]
    if missing:
        raise SchemaError(f"Missing required OHLCV columns: {', '.join(missing)}")

    work = frame.copy()
    work["timestamp"] = pd.to_datetime(work["timestamp"], errors="raise")
    work = work.sort_values("timestamp", kind="stable").set_index("timestamp")

    if work.index.has_duplicates:
        raise ResampleError("Duplicate timestamps must be resolved before resampling")

    kwargs: dict[str, object] = {
        "rule": policy.rule,
        "origin": policy.origin,
        "closed": policy.closed,
        "label": policy.label,
    }
    if policy.offset is not None:
        kwargs["offset"] = policy.offset

    resampler = work.resample(**kwargs)
    aggregated = resampler.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        source_count=("close", "count"),
    )

    aggregated = aggregated[aggregated["source_count"] > 0].copy()

    def _first_or_empty(column: str, default: str) -> pd.Series:
        if column not in work.columns:
            return pd.Series(default, index=aggregated.index, dtype="object")
        return resampler[column].first().reindex(aggregated.index).fillna(default)

    aggregated["symbol"] = _first_or_empty("symbol", "")
    aggregated["timeframe"] = policy.target_timeframe
    aggregated["source"] = _first_or_empty("source", "")

    if "is_closed" in work.columns:
        aggregated["is_closed"] = resampler["is_closed"].all().reindex(aggregated.index).fillna(False).astype(bool)
    else:
        aggregated["is_closed"] = True

    upstream_complete = (
        resampler["is_complete"].all().reindex(aggregated.index).fillna(False).astype(bool)
        if "is_complete" in work.columns
        else pd.Series(True, index=aggregated.index, dtype="bool")
    )
    aggregated["is_complete"] = upstream_complete & (aggregated["source_count"] == policy.expected_base_bars)

    aggregated = aggregated.reset_index()
    return aggregated.loc[:, (*CANONICAL_COLUMNS, "source_count")]
