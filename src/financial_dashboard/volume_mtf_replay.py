from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from financial_dashboard.data.engine_input import EngineInputBatch, prepare_engine_input
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.data.quality import DataQualityReport, DataQualityStatus
from financial_dashboard.engines.volume_evidence import (
    ParticipationWithoutStructure,
    StructureVolumeLink,
    VolumeEvidenceDataQuality,
    VolumeEvidenceEngine,
    VolumeEvidenceSnapshot,
    VolumeEvidenceStatus,
    find_participation_without_structure,
    link_structure_events_to_volume,
)
from financial_dashboard.engines.volume_participation_engine import (
    VolumeParticipationConfig,
)
from financial_dashboard.engines.volume_participation_lifecycle import (
    ParticipationLifecycleConfig,
)
from financial_dashboard.mtf_replay import (
    CachedMarketStructureMTFRunner,
    MTFReplayResult,
)


VOLUME_EVIDENCE_TIMEFRAMES: tuple[str, ...] = ("1d", "4h", "2h", "1h", "30m")


def _normalize_timeframes(timeframes: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(timeframe.strip().lower() for timeframe in timeframes)
    if not normalized:
        raise ValueError("at least one Volume evidence timeframe is required")
    if len(set(normalized)) != len(normalized):
        raise ValueError("Volume evidence timeframes must be unique")
    unsupported = tuple(
        timeframe
        for timeframe in normalized
        if timeframe not in VOLUME_EVIDENCE_TIMEFRAMES
    )
    if unsupported:
        supported = ", ".join(VOLUME_EVIDENCE_TIMEFRAMES)
        raise ValueError(
            f"unsupported Volume evidence timeframe(s) {unsupported!r}; "
            f"expected only: {supported}"
        )
    return normalized


def _closed_complete_mask(frame: pd.DataFrame) -> pd.Series:
    mask = pd.Series(True, index=frame.index, dtype=bool)
    if "is_closed" in frame.columns:
        mask &= frame["is_closed"].fillna(False).astype(bool)
    if "is_complete" in frame.columns:
        mask &= frame["is_complete"].fillna(False).astype(bool)
    return mask


def _trailing_excluded_count(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    safe = _closed_complete_mask(frame).tolist()
    count = 0
    for is_safe in reversed(safe):
        if is_safe:
            break
        count += 1
    return count


def _replay_data_quality(
    frame: pd.DataFrame,
    batch: EngineInputBatch,
) -> VolumeEvidenceDataQuality:
    if _trailing_excluded_count(frame):
        return VolumeEvidenceDataQuality.INCOMPLETE_TAIL
    if batch.source_quality.status is DataQualityStatus.LIMITED:
        return VolumeEvidenceDataQuality.DATA_LIMITED
    return VolumeEvidenceDataQuality.READY


def _timestamps(frame: pd.DataFrame) -> tuple[pd.Timestamp, ...]:
    return tuple(pd.Timestamp(value) for value in frame["timestamp"].tolist())


@dataclass(frozen=True, slots=True)
class VolumeTimeframeEvidenceReplay:
    symbol: str
    timeframe: str
    input_batch: EngineInputBatch
    history: tuple[VolumeEvidenceSnapshot, ...]
    latest: VolumeEvidenceSnapshot
    event_links: tuple[StructureVolumeLink, ...]
    participation_without_structure: tuple[ParticipationWithoutStructure, ...]
    replay_data_quality: VolumeEvidenceDataQuality
    excluded_tail_bar_count: int

    @property
    def source_quality(self) -> DataQualityReport:
        return self.input_batch.source_quality

    @property
    def bar_count(self) -> int:
        return len(self.history)

    @property
    def ready_bar_count(self) -> int:
        return sum(
            snapshot.status is VolumeEvidenceStatus.READY
            for snapshot in self.history
        )

    @property
    def warmup_bar_count(self) -> int:
        return sum(
            snapshot.status is VolumeEvidenceStatus.WARMUP
            for snapshot in self.history
        )

    @property
    def unavailable_bar_count(self) -> int:
        return sum(
            snapshot.status is VolumeEvidenceStatus.VOLUME_UNAVAILABLE
            for snapshot in self.history
        )


@dataclass(frozen=True, slots=True)
class VolumeMTFEvidenceReplay:
    symbol: str
    timeframes: tuple[str, ...]
    timeframe_replays: tuple[VolumeTimeframeEvidenceReplay, ...]

    def replay_for(self, timeframe: str) -> VolumeTimeframeEvidenceReplay:
        normalized = timeframe.strip().lower()
        for replay in self.timeframe_replays:
            if replay.timeframe == normalized:
                return replay
        raise KeyError(f"Volume timeframe not replayed: {timeframe}")

    @property
    def total_bar_count(self) -> int:
        return sum(replay.bar_count for replay in self.timeframe_replays)


class VolumeMTFEvidenceReplayRunner:
    """Independent full-cache replay plus causal same-timeframe Structure linkage."""

    def __init__(
        self,
        store: ParquetOHLCVStore,
        *,
        config: VolumeParticipationConfig | None = None,
        lifecycle_config: ParticipationLifecycleConfig | None = None,
        pre_event_bars: int = 2,
        follow_through_bars: int = 2,
    ) -> None:
        if pre_event_bars < 0 or follow_through_bars < 0:
            raise ValueError("Structure/Volume window lengths must be non-negative")
        self.store = store
        self.config = config or VolumeParticipationConfig()
        self.lifecycle_config = lifecycle_config or ParticipationLifecycleConfig()
        self.pre_event_bars = pre_event_bars
        self.follow_through_bars = follow_through_bars

    @staticmethod
    def _validate_structure_replay(
        structure_replay: MTFReplayResult,
        *,
        symbol: str,
        timeframes: tuple[str, ...],
    ) -> None:
        if structure_replay.symbol.strip().upper() != symbol:
            raise ValueError(
                f"Structure/Volume symbol mismatch: "
                f"{structure_replay.symbol!r} != {symbol!r}"
            )
        missing = tuple(
            timeframe
            for timeframe in timeframes
            if timeframe not in structure_replay.replays
        )
        if missing:
            raise ValueError(
                f"Market Structure replay is missing Volume timeframe(s): {missing!r}"
            )

    def replay(
        self,
        symbol: str,
        *,
        timeframes: Iterable[str] = VOLUME_EVIDENCE_TIMEFRAMES,
        structure_replay: MTFReplayResult | None = None,
    ) -> VolumeMTFEvidenceReplay:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            raise ValueError("symbol must not be empty")
        normalized_timeframes = _normalize_timeframes(timeframes)

        if structure_replay is None:
            structure_replay = CachedMarketStructureMTFRunner(self.store).run(
                symbol=normalized_symbol,
                timeframes=normalized_timeframes,
            )
        self._validate_structure_replay(
            structure_replay,
            symbol=normalized_symbol,
            timeframes=normalized_timeframes,
        )

        timeframe_replays: list[VolumeTimeframeEvidenceReplay] = []
        for timeframe in normalized_timeframes:
            cached = self.store.load(normalized_symbol, timeframe)
            batch = prepare_engine_input(cached)
            structure_timeframe = structure_replay.replays[timeframe]
            if _timestamps(batch.frame) != _timestamps(structure_timeframe.input_batch.frame):
                raise ValueError(
                    f"Structure/Volume closed-bar cache mismatch for "
                    f"{normalized_symbol} {timeframe}"
                )
            structure_snapshot = structure_replay.structure_for(timeframe)
            if structure_snapshot.symbol.strip().upper() != normalized_symbol:
                raise ValueError(
                    f"Market Structure namespace mismatch for {timeframe}"
                )

            engine = VolumeEvidenceEngine(
                symbol=normalized_symbol,
                timeframe=timeframe,
                config=self.config,
                lifecycle_config=self.lifecycle_config,
            )
            history = engine.replay(batch.frame)
            latest = engine.snapshot
            if latest is None or not history:
                raise ValueError(
                    f"no closed and complete Volume evidence bars for "
                    f"{normalized_symbol} {timeframe}"
                )
            links = link_structure_events_to_volume(
                structure_snapshot.events,
                history,
                pre_event_bars=self.pre_event_bars,
                follow_through_bars=self.follow_through_bars,
            )
            unlinked = find_participation_without_structure(
                history,
                structure_snapshot.events,
            )
            timeframe_replays.append(
                VolumeTimeframeEvidenceReplay(
                    symbol=normalized_symbol,
                    timeframe=timeframe,
                    input_batch=batch,
                    history=history,
                    latest=latest,
                    event_links=links,
                    participation_without_structure=unlinked,
                    replay_data_quality=_replay_data_quality(cached, batch),
                    excluded_tail_bar_count=_trailing_excluded_count(cached),
                )
            )

        return VolumeMTFEvidenceReplay(
            symbol=normalized_symbol,
            timeframes=normalized_timeframes,
            timeframe_replays=tuple(timeframe_replays),
        )


def replay_volume_evidence_from_cache(
    cache_root: str | Path,
    *,
    symbol: str,
    timeframes: Iterable[str] = VOLUME_EVIDENCE_TIMEFRAMES,
    config: VolumeParticipationConfig | None = None,
    lifecycle_config: ParticipationLifecycleConfig | None = None,
    structure_replay: MTFReplayResult | None = None,
) -> VolumeMTFEvidenceReplay:
    return VolumeMTFEvidenceReplayRunner(
        ParquetOHLCVStore(cache_root),
        config=config,
        lifecycle_config=lifecycle_config,
    ).replay(
        symbol,
        timeframes=timeframes,
        structure_replay=structure_replay,
    )


__all__ = [
    "VOLUME_EVIDENCE_TIMEFRAMES",
    "VolumeMTFEvidenceReplay",
    "VolumeMTFEvidenceReplayRunner",
    "VolumeTimeframeEvidenceReplay",
    "replay_volume_evidence_from_cache",
]
