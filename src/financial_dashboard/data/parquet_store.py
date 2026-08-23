from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

from .schema import CANONICAL_COLUMNS, canonicalize_ohlcv


@lru_cache(maxsize=64)
def _read_parquet_cached(path_text: str, size: int, mtime_ns: int) -> pd.DataFrame:
    """Read and normalize one immutable parquet snapshot.

    ``size`` and ``mtime_ns`` are part of the cache key so a rewritten cache file
    automatically produces a new entry without requiring explicit invalidation.
    Callers receive a defensive copy from :meth:`ParquetOHLCVStore.load`.
    """

    del size, mtime_ns
    frame = pd.read_parquet(path_text)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="raise")
    return frame.sort_values("timestamp", kind="stable").reset_index(drop=True)


class ParquetOHLCVStore:
    """Incremental local OHLCV cache keyed by symbol and timeframe."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_part(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
        return cleaned or "unknown"

    def path_for(self, symbol: str, timeframe: str) -> Path:
        return self.root / f"{self._safe_part(symbol)}__{self._safe_part(timeframe)}.parquet"

    def load(self, symbol: str, timeframe: str) -> pd.DataFrame:
        path = self.path_for(symbol, timeframe)
        if not path.exists():
            return pd.DataFrame(columns=CANONICAL_COLUMNS)
        stat = path.stat()
        frame = _read_parquet_cached(str(path.resolve()), stat.st_size, stat.st_mtime_ns)
        return frame.copy(deep=True)

    def merge_and_save(self, frame: pd.DataFrame, *, symbol: str, timeframe: str, source: str) -> pd.DataFrame:
        incoming = canonicalize_ohlcv(frame, symbol=symbol, timeframe=timeframe, source=source)
        existing = self.load(symbol, timeframe)
        if existing.empty:
            merged = incoming
        elif incoming.empty:
            merged = existing
        else:
            merged = pd.concat([existing, incoming], ignore_index=True)
            merged = merged.sort_values("timestamp", kind="stable")
            merged = merged.drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)

        path = self.path_for(symbol, timeframe)
        merged.to_parquet(path, index=False)
        return merged

    def latest_timestamp(self, symbol: str, timeframe: str) -> pd.Timestamp | None:
        frame = self.load(symbol, timeframe)
        if frame.empty:
            return None
        return pd.Timestamp(frame.iloc[-1]["timestamp"])
