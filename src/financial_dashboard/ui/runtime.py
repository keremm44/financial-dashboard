from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.data.quality import DataQualityStatus, assess_ohlcv_quality
from financial_dashboard.engines.pattern_compression_core import PatternCompressionConfig
from financial_dashboard.engines.three_domain_observer import (
    FOUNDATION_OBSERVER_TIMEFRAMES,
)
from financial_dashboard.three_domain_replay import (
    CachedThreeDomainObserverRunner,
    ThreeDomainReplayResult,
)


@dataclass(frozen=True, slots=True)
class CacheTimeframeStatus:
    symbol: str
    timeframe: str
    path: Path
    exists: bool
    row_count: int
    confirmed_row_count: int
    open_row_count: int
    incomplete_row_count: int
    latest_timestamp: Any
    quality_status: DataQualityStatus | None
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def runnable(self) -> bool:
        return (
            self.exists
            and self.quality_status is not DataQualityStatus.INVALID
            and self.confirmed_row_count > 0
        )

    @property
    def display_status(self) -> str:
        if not self.exists:
            return "MISSING"
        if self.quality_status is None:
            return "UNREADABLE"
        return self.quality_status.value


def _normalized_timeframes(timeframes: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(str(timeframe).strip().lower() for timeframe in timeframes)
    if not normalized or not all(normalized):
        raise ValueError("at least one non-empty timeframe is required")
    if len(set(normalized)) != len(normalized):
        raise ValueError("timeframes must be unique after normalization")
    unsupported = tuple(
        timeframe
        for timeframe in normalized
        if timeframe not in FOUNDATION_OBSERVER_TIMEFRAMES
    )
    if unsupported:
        raise ValueError("unsupported UI timeframes: " + ", ".join(unsupported))
    return normalized


def discover_cached_symbols(
    cache_root: str | Path,
    *,
    timeframes: tuple[str, ...] = FOUNDATION_OBSERVER_TIMEFRAMES,
) -> tuple[str, ...]:
    root = Path(cache_root).expanduser()
    if not root.exists() or not root.is_dir():
        return ()
    allowed = set(_normalized_timeframes(timeframes))
    symbols: set[str] = set()
    for path in root.glob("*.parquet"):
        stem = path.stem
        if "__" not in stem:
            continue
        symbol, timeframe = stem.rsplit("__", 1)
        if symbol and timeframe.strip().lower() in allowed:
            symbols.add(symbol)
    return tuple(sorted(symbols))


def inspect_symbol_cache(
    cache_root: str | Path,
    *,
    symbol: str,
    timeframes: tuple[str, ...] = FOUNDATION_OBSERVER_TIMEFRAMES,
) -> tuple[CacheTimeframeStatus, ...]:
    normalized = _normalized_timeframes(timeframes)
    clean_symbol = symbol.strip()
    if not clean_symbol:
        raise ValueError("symbol must not be empty")
    store = ParquetOHLCVStore(Path(cache_root).expanduser())
    statuses: list[CacheTimeframeStatus] = []

    for timeframe in normalized:
        path = store.path_for(clean_symbol, timeframe)
        if not path.exists():
            statuses.append(
                CacheTimeframeStatus(
                    symbol=clean_symbol,
                    timeframe=timeframe,
                    path=path,
                    exists=False,
                    row_count=0,
                    confirmed_row_count=0,
                    open_row_count=0,
                    incomplete_row_count=0,
                    latest_timestamp=None,
                    quality_status=None,
                    errors=("Cache file is missing",),
                )
            )
            continue

        try:
            frame = store.load(clean_symbol, timeframe)
            report = assess_ohlcv_quality(frame)
            closed = (
                frame["is_closed"].fillna(False).astype(bool)
                if "is_closed" in frame.columns
                else frame.index.to_series().map(lambda _: True)
            )
            complete = (
                frame["is_complete"].fillna(False).astype(bool)
                if "is_complete" in frame.columns
                else frame.index.to_series().map(lambda _: True)
            )
            statuses.append(
                CacheTimeframeStatus(
                    symbol=clean_symbol,
                    timeframe=timeframe,
                    path=path,
                    exists=True,
                    row_count=len(frame),
                    confirmed_row_count=int((closed & complete).sum()),
                    open_row_count=int((~closed).sum()),
                    incomplete_row_count=int((~complete).sum()),
                    latest_timestamp=(
                        None if frame.empty else frame.iloc[-1]["timestamp"]
                    ),
                    quality_status=report.status,
                    warnings=report.warnings,
                    errors=report.errors,
                )
            )
        except Exception as error:  # UI boundary: preserve the error as data.
            statuses.append(
                CacheTimeframeStatus(
                    symbol=clean_symbol,
                    timeframe=timeframe,
                    path=path,
                    exists=True,
                    row_count=0,
                    confirmed_row_count=0,
                    open_row_count=0,
                    incomplete_row_count=0,
                    latest_timestamp=None,
                    quality_status=None,
                    errors=(f"{type(error).__name__}: {error}",),
                )
            )
    return tuple(statuses)


def runnable_timeframes(
    statuses: Iterable[CacheTimeframeStatus],
) -> tuple[str, ...]:
    by_timeframe = {status.timeframe: status for status in statuses}
    return tuple(
        timeframe
        for timeframe in FOUNDATION_OBSERVER_TIMEFRAMES
        if (status := by_timeframe.get(timeframe)) is not None and status.runnable
    )


def replay_cached_observer(
    cache_root: str | Path,
    *,
    symbol: str,
    timeframes: tuple[str, ...],
    pattern_profile: str | None = None,
) -> ThreeDomainReplayResult:
    normalized = _normalized_timeframes(timeframes)
    clean_symbol = symbol.strip()
    if not clean_symbol:
        raise ValueError("symbol must not be empty")
    pattern_config = (
        None
        if pattern_profile is None
        else PatternCompressionConfig(profile=pattern_profile)
    )
    store = ParquetOHLCVStore(Path(cache_root).expanduser())
    runner = CachedThreeDomainObserverRunner(
        store,
        pattern_compression_config=pattern_config,
    )
    return runner.run(symbol=clean_symbol, timeframes=normalized)


def cache_fingerprint(
    cache_root: str | Path,
    *,
    symbol: str,
    timeframes: tuple[str, ...] = FOUNDATION_OBSERVER_TIMEFRAMES,
) -> tuple[tuple[str, int, int], ...]:
    normalized = _normalized_timeframes(timeframes)
    store = ParquetOHLCVStore(Path(cache_root).expanduser())
    fingerprint: list[tuple[str, int, int]] = []
    for timeframe in normalized:
        path = store.path_for(symbol.strip(), timeframe)
        if path.exists():
            stat = path.stat()
            fingerprint.append((timeframe, stat.st_size, stat.st_mtime_ns))
        else:
            fingerprint.append((timeframe, -1, -1))
    return tuple(fingerprint)
