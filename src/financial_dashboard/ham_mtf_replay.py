from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES, normalize_timeframes
from financial_dashboard.data.analysis_inputs import AnalysisInputSnapshot
from financial_dashboard.data.engine_input import EngineInputBatch, prepare_engine_input
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.data.quality import DataQualityReport
from financial_dashboard.engines.ham_evidence import (
    HamEvidenceConfig,
    HamEvidenceEngine,
    HamEvidenceSnapshot,
)
from financial_dashboard.engines.raw_indicator_dashboard import (
    RawDataQuality,
    RawIndicatorConfig,
    TrendProfile,
)


# Backwards-compatible public name; canonical definition lives in analysis_config.
HAM_EVIDENCE_TIMEFRAMES: tuple[str, ...] = ANALYSIS_TIMEFRAMES

_PROFILE_BY_TIMEFRAME: Mapping[str, TrendProfile] = MappingProxyType(
    {
        "1d": TrendProfile.XAG_1D,
        "4h": TrendProfile.XAG_4H,
        "2h": TrendProfile.XAG_2H,
        "1h": TrendProfile.XAG_1H,
        "30m": TrendProfile.XAG_30M,
    }
)


def ham_profile_for_timeframe(timeframe: str) -> TrendProfile:
    normalized = timeframe.strip().lower()
    try:
        return _PROFILE_BY_TIMEFRAME[normalized]
    except KeyError as exc:
        supported = ", ".join(HAM_EVIDENCE_TIMEFRAMES)
        raise ValueError(
            f"unsupported Ham evidence timeframe {timeframe!r}; expected one of: {supported}"
        ) from exc


def _normalize_timeframes(timeframes: Iterable[str]) -> tuple[str, ...]:
    normalized = normalize_timeframes(
        timeframes,
        supported=HAM_EVIDENCE_TIMEFRAMES,
        label="Ham evidence",
    )
    for timeframe in normalized:
        ham_profile_for_timeframe(timeframe)
    return normalized


@dataclass(frozen=True, slots=True)
class HamTimeframeEvidenceReplay:
    symbol: str
    timeframe: str
    profile: TrendProfile
    input_batch: EngineInputBatch
    history: tuple[HamEvidenceSnapshot, ...]
    latest: HamEvidenceSnapshot

    @property
    def source_quality(self) -> DataQualityReport:
        return self.input_batch.source_quality

    @property
    def bar_count(self) -> int:
        return len(self.history)

    @property
    def warmup_bar_count(self) -> int:
        return sum(
            snapshot.data_quality == RawDataQuality.WARMUP
            for snapshot in self.history
        )

    @property
    def ready_bar_count(self) -> int:
        return sum(
            snapshot.data_quality == RawDataQuality.OK
            for snapshot in self.history
        )

    @property
    def earliest_timestamp(self) -> object | None:
        return self.history[0].timestamp if self.history else None

    @property
    def latest_timestamp(self) -> object | None:
        return self.latest.timestamp


@dataclass(frozen=True, slots=True)
class HamMTFEvidenceReplay:
    symbol: str
    timeframes: tuple[str, ...]
    timeframe_replays: tuple[HamTimeframeEvidenceReplay, ...]

    def replay_for(self, timeframe: str) -> HamTimeframeEvidenceReplay:
        normalized = timeframe.strip().lower()
        for replay in self.timeframe_replays:
            if replay.timeframe == normalized:
                return replay
        raise KeyError(f"timeframe not replayed: {timeframe}")

    @property
    def total_bar_count(self) -> int:
        return sum(replay.bar_count for replay in self.timeframe_replays)


class HamMTFEvidenceReplayRunner:
    """Independent full-cache Ham replay for each requested timeframe."""

    def __init__(
        self,
        store: ParquetOHLCVStore,
        *,
        evidence_config: HamEvidenceConfig | None = None,
        raw_configs: Mapping[str, RawIndicatorConfig] | None = None,
    ) -> None:
        self.store = store
        self.evidence_config = evidence_config or HamEvidenceConfig()
        self.raw_configs = {
            timeframe.strip().lower(): config
            for timeframe, config in (raw_configs or {}).items()
        }
        for timeframe, config in self.raw_configs.items():
            expected = ham_profile_for_timeframe(timeframe)
            if config.profile != expected:
                raise ValueError(
                    f"Ham raw config for {timeframe} must use profile {expected.value!r}, "
                    f"got {config.profile.value!r}"
                )

    def _raw_config_for(self, timeframe: str) -> RawIndicatorConfig:
        return self.raw_configs.get(
            timeframe,
            RawIndicatorConfig(profile=ham_profile_for_timeframe(timeframe)),
        )

    def replay(
        self,
        symbol: str,
        *,
        timeframes: Iterable[str] = HAM_EVIDENCE_TIMEFRAMES,
        input_snapshot: AnalysisInputSnapshot | None = None,
    ) -> HamMTFEvidenceReplay:
        normalized_symbol = normalize_symbol(symbol)
        normalized_timeframes = _normalize_timeframes(timeframes)
        if input_snapshot is not None:
            input_snapshot.validate_request(
                symbol=normalized_symbol,
                timeframes=normalized_timeframes,
            )

        timeframe_replays: list[HamTimeframeEvidenceReplay] = []
        for timeframe in normalized_timeframes:
            if input_snapshot is None:
                cached = self.store.load(normalized_symbol, timeframe)
                batch = prepare_engine_input(cached)
            else:
                batch = input_snapshot.for_timeframe(timeframe).input_batch
            raw_config = self._raw_config_for(timeframe)
            engine = HamEvidenceEngine(
                raw_config=raw_config,
                evidence_config=self.evidence_config,
            )
            engine.replay(batch.frame)
            history = engine.history
            latest = engine.snapshot
            if latest is None or not history:
                raise ValueError(
                    f"no closed and complete Ham evidence bars for "
                    f"{normalized_symbol} {timeframe}"
                )
            timeframe_replays.append(
                HamTimeframeEvidenceReplay(
                    symbol=normalized_symbol,
                    timeframe=timeframe,
                    profile=raw_config.profile,
                    input_batch=batch,
                    history=history,
                    latest=latest,
                )
            )

        return HamMTFEvidenceReplay(
            symbol=normalized_symbol,
            timeframes=normalized_timeframes,
            timeframe_replays=tuple(timeframe_replays),
        )


def replay_ham_evidence_from_cache(
    cache_root: str | Path,
    *,
    symbol: str,
    timeframes: Iterable[str] = HAM_EVIDENCE_TIMEFRAMES,
    evidence_config: HamEvidenceConfig | None = None,
) -> HamMTFEvidenceReplay:
    runner = HamMTFEvidenceReplayRunner(
        ParquetOHLCVStore(cache_root),
        evidence_config=evidence_config,
    )
    return runner.replay(symbol, timeframes=timeframes)


__all__ = [
    "HAM_EVIDENCE_TIMEFRAMES",
    "HamMTFEvidenceReplay",
    "HamMTFEvidenceReplayRunner",
    "HamTimeframeEvidenceReplay",
    "ham_profile_for_timeframe",
    "replay_ham_evidence_from_cache",
]
