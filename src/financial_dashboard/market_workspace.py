from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES, normalize_timeframes
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.engines.pattern_compression_core import PatternCompressionConfig
from financial_dashboard.ham_mtf_replay import HamMTFEvidenceReplay, HamMTFEvidenceReplayRunner
from financial_dashboard.three_domain_replay import (
    CachedThreeDomainObserverRunner,
    ThreeDomainReplayResult,
)
from financial_dashboard.volume_mtf_replay import (
    VolumeMTFEvidenceReplay,
    VolumeMTFEvidenceReplayRunner,
)


class WorkspaceDomainStatus(StrEnum):
    READY = "READY"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class WorkspaceDomainResult:
    status: WorkspaceDomainStatus
    result: Any | None = None
    error_type: str | None = None
    error_message: str | None = None

    @classmethod
    def ready(cls, result: Any) -> "WorkspaceDomainResult":
        return cls(status=WorkspaceDomainStatus.READY, result=result)

    @classmethod
    def failed(cls, error: Exception) -> "WorkspaceDomainResult":
        return cls(
            status=WorkspaceDomainStatus.ERROR,
            error_type=type(error).__name__,
            error_message=str(error),
        )

    @property
    def is_ready(self) -> bool:
        return self.status is WorkspaceDomainStatus.READY


@dataclass(frozen=True, slots=True)
class MarketAnalysisWorkspace:
    symbol: str
    timeframes: tuple[str, ...]
    fingerprint: tuple[tuple[str, int, int], ...]
    observer: ThreeDomainReplayResult
    ham: WorkspaceDomainResult
    volume: WorkspaceDomainResult

    @property
    def ham_result(self) -> HamMTFEvidenceReplay | None:
        result = self.ham.result
        return result if isinstance(result, HamMTFEvidenceReplay) else None

    @property
    def volume_result(self) -> VolumeMTFEvidenceReplay | None:
        result = self.volume.result
        return result if isinstance(result, VolumeMTFEvidenceReplay) else None


class CacheSnapshotChangedError(RuntimeError):
    """Raised when cache files change while one workspace replay is being built."""


class MarketAnalysisWorkspaceRunner:
    """Execution coordinator for independent analysis domains.

    This class has no trading authority. It normalizes identity, freezes one cache
    fingerprint, runs the existing deterministic engines, reuses authoritative
    Structure output for Volume linkage, isolates non-foundation domain failures,
    and rejects a run if cache files changed mid-replay.
    """

    def __init__(self, store: ParquetOHLCVStore) -> None:
        self.store = store

    def _fingerprint(
        self,
        *,
        symbol: str,
        timeframes: tuple[str, ...],
    ) -> tuple[tuple[str, int, int], ...]:
        rows: list[tuple[str, int, int]] = []
        for timeframe in timeframes:
            path = self.store.path_for(symbol, timeframe)
            if path.exists():
                stat = path.stat()
                rows.append((timeframe, stat.st_size, stat.st_mtime_ns))
            else:
                rows.append((timeframe, -1, -1))
        return tuple(rows)

    def run(
        self,
        *,
        symbol: str,
        timeframes: tuple[str, ...] = ANALYSIS_TIMEFRAMES,
        pattern_profile: str | None = None,
    ) -> MarketAnalysisWorkspace:
        normalized_symbol = normalize_symbol(symbol)
        normalized_timeframes = normalize_timeframes(
            timeframes,
            supported=ANALYSIS_TIMEFRAMES,
            label="workspace",
        )
        before = self._fingerprint(
            symbol=normalized_symbol,
            timeframes=normalized_timeframes,
        )

        pattern_config = (
            None
            if pattern_profile is None
            else PatternCompressionConfig(profile=pattern_profile)
        )
        observer = CachedThreeDomainObserverRunner(
            self.store,
            pattern_compression_config=pattern_config,
        ).run(
            symbol=normalized_symbol,
            timeframes=normalized_timeframes,
        )

        try:
            ham = WorkspaceDomainResult.ready(
                HamMTFEvidenceReplayRunner(self.store).replay(
                    normalized_symbol,
                    timeframes=normalized_timeframes,
                )
            )
        except Exception as error:
            ham = WorkspaceDomainResult.failed(error)

        try:
            volume = WorkspaceDomainResult.ready(
                VolumeMTFEvidenceReplayRunner(self.store).replay(
                    normalized_symbol,
                    timeframes=normalized_timeframes,
                    structure_replay=observer.structure_location,
                )
            )
        except Exception as error:
            volume = WorkspaceDomainResult.failed(error)

        after = self._fingerprint(
            symbol=normalized_symbol,
            timeframes=normalized_timeframes,
        )
        if after != before:
            raise CacheSnapshotChangedError(
                "cache files changed while the analysis workspace was replaying"
            )

        return MarketAnalysisWorkspace(
            symbol=normalized_symbol,
            timeframes=normalized_timeframes,
            fingerprint=before,
            observer=observer,
            ham=ham,
            volume=volume,
        )


def replay_market_workspace_from_cache(
    cache_root: str | Path,
    *,
    symbol: str,
    timeframes: tuple[str, ...] = ANALYSIS_TIMEFRAMES,
    pattern_profile: str | None = None,
) -> MarketAnalysisWorkspace:
    return MarketAnalysisWorkspaceRunner(ParquetOHLCVStore(cache_root)).run(
        symbol=symbol,
        timeframes=timeframes,
        pattern_profile=pattern_profile,
    )


__all__ = [
    "CacheSnapshotChangedError",
    "MarketAnalysisWorkspace",
    "MarketAnalysisWorkspaceRunner",
    "WorkspaceDomainResult",
    "WorkspaceDomainStatus",
    "replay_market_workspace_from_cache",
]
