from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

PRICE_COLUMNS = ("open", "high", "low", "close")
REQUIRED_OHLCV_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")
CANONICAL_COLUMNS = (
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "symbol",
    "timeframe",
    "is_closed",
    "is_complete",
    "source",
)


class SchemaError(ValueError):
    """Raised when input data cannot satisfy the canonical OHLCV contract."""


def _missing(columns: Iterable[str], required: Iterable[str]) -> list[str]:
    present = set(columns)
    return [column for column in required if column not in present]


def canonicalize_ohlcv(
    frame: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    source: str,
    default_is_closed: bool = True,
) -> pd.DataFrame:
    """Return a sorted canonical OHLCV frame without inventing market data.

    Providers may have different response shapes, but all downstream engines receive
    this contract. Timestamps are parsed but their timezone is intentionally preserved;
    provider adapters are responsible for assigning the correct exchange timezone.
    """
    if frame.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    missing = _missing(frame.columns, REQUIRED_OHLCV_COLUMNS)
    if missing:
        raise SchemaError(f"Missing required OHLCV columns: {', '.join(missing)}")

    result = frame.copy()
    result["timestamp"] = pd.to_datetime(result["timestamp"], errors="raise")

    for column in (*PRICE_COLUMNS, "volume"):
        result[column] = pd.to_numeric(result[column], errors="raise")

    result["symbol"] = result.get("symbol", symbol)
    result["timeframe"] = result.get("timeframe", timeframe)
    result["source"] = result.get("source", source)
    result["is_closed"] = result.get("is_closed", default_is_closed).astype(bool) if "is_closed" in result else default_is_closed
    result["is_complete"] = result.get("is_complete", True).astype(bool) if "is_complete" in result else True

    result = result.sort_values("timestamp", kind="stable").reset_index(drop=True)
    return result.loc[:, CANONICAL_COLUMNS]
