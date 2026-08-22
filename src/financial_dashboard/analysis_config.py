from __future__ import annotations

from types import MappingProxyType
from typing import Iterable

import pandas as pd


ANALYSIS_TIMEFRAMES: tuple[str, ...] = ("1d", "4h", "2h", "1h", "30m")

# Canonical significance order used only for deterministic presentation/progression
# ordering. It is not a vote weight and must not be used to promote lower-TF facts.
TIMEFRAME_RANK = MappingProxyType(
    {
        "30m": 1,
        "1h": 2,
        "2h": 3,
        "4h": 4,
        "1d": 5,
    }
)

# Intraday cache timestamps are left-labelled bar starts. Daily production cache
# timestamps are session-close labelled by YahooFinanceDailyProvider and therefore
# are already causally available at their stored timestamp.
LEFT_LABEL_DURATIONS = MappingProxyType(
    {
        "15m": pd.Timedelta(minutes=15),
        "30m": pd.Timedelta(minutes=30),
        "1h": pd.Timedelta(hours=1),
        "2h": pd.Timedelta(hours=2),
        "4h": pd.Timedelta(hours=4),
    }
)
CLOSE_LABELLED_TIMEFRAMES: frozenset[str] = frozenset({"1d", "1w"})


def normalize_timeframes(
    timeframes: Iterable[str],
    *,
    supported: Iterable[str] = ANALYSIS_TIMEFRAMES,
    label: str = "analysis",
) -> tuple[str, ...]:
    normalized = tuple(str(timeframe).strip().lower() for timeframe in timeframes)
    if not normalized or not all(normalized):
        raise ValueError(f"at least one non-empty {label} timeframe is required")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{label} timeframes must be unique after normalization")
    allowed = frozenset(str(timeframe).strip().lower() for timeframe in supported)
    unsupported = tuple(timeframe for timeframe in normalized if timeframe not in allowed)
    if unsupported:
        raise ValueError(
            f"unsupported {label} timeframe(s): {', '.join(unsupported)}"
        )
    return normalized


__all__ = [
    "ANALYSIS_TIMEFRAMES",
    "CLOSE_LABELLED_TIMEFRAMES",
    "LEFT_LABEL_DURATIONS",
    "TIMEFRAME_RANK",
    "normalize_timeframes",
]
