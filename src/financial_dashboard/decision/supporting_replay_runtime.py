from __future__ import annotations

from dataclasses import dataclass
import pickle
from typing import Any, Mapping

from financial_dashboard.data.analysis_inputs import AnalysisInputSnapshot
from financial_dashboard.engines.ham_evidence import HamEvidenceConfig, HamEvidenceEngine
from financial_dashboard.engines.raw_indicator_dashboard import RawIndicatorConfig
from financial_dashboard.engines.volume_evidence import (
    VolumeEvidenceEngine,
    find_participation_without_structure,
)
from financial_dashboard.engines.volume_participation_engine import VolumeParticipationConfig
from financial_dashboard.engines.volume_participation_lifecycle import ParticipationLifecycleConfig
from financial_dashboard.engines.volume_round2 import build_volume_round2_assessment
from financial_dashboard.engines.volume_structure_link_runtime import link_structure_events_to_volume_indexed
from financial_dashboard.engines.volatility_bands_fib_engine import VolatilityBandsConfig
from financial_dashboard.engines.volatility_direction_runtime import RuntimeVolatilityDirectionTransitionEngine
from financial_dashboard.ham_mtf_replay import (
    HamMTFEvidenceReplay,
    HamTimeframeEvidenceReplay,
    ham_profile_for_timeframe,
)
from financial_dashboard.structure_location_replay import CausalBarClock, StructureLocationMTFResult
from financial_dashboard.volume_mtf_replay import (
    VolumeMTFEvidenceReplay,
    VolumeTimeframeEvidenceReplay,
    _replay_data_quality,
    _trailing_excluded_count,
)
from financial_dashboard.volatility_mtf_replay import (
    VOLATILITY_TIMEFRAMES,
    VolatilityMTFReplay,
    VolatilityTimeframeReplay,
)


@dataclass(slots=True)
class _HamRuntime:
    engine: HamEvidenceEngine


@dataclass(slots=True)
class _VolumeRuntime:
    engine: VolumeEvidenceEngine


@dataclass(slots=True)
class _VolatilityRuntime:
    engine: RuntimeVolatilityDirectionTransitionEngine
    snapshots: list[Any]


@dataclass(frozen=True, slots=True)
class SupportingRuntimeCheckpoint:
    """Detached continuation state for HAM, Volume and Volatility engines."""

    symbol: str
    timeframes: tuple[str, ...]
    watermarks: tuple[tuple[str, int], ...]
    ham: tuple[tuple[str, _HamRuntime], ...]
    volume: tuple[tuple[str, _VolumeRuntime], ...]
    volatility: tuple[tuple[str, _VolatilityRuntime], ...]

    @property
    def watermark_map(self) -> dict[str, int]:
        return {timeframe: int(index) for timeframe, index in self.watermarks}


@dataclass(frozen=True, slots=True)
class SupportingReplayState:
    ham: HamMTFEvidenceReplay
    volume: VolumeMTFEvidenceReplay
    volatility: VolatilityMTFReplay


class IncrementalSupportingReplayRuntime:
    """Append-only HAM/Volume/Volatility runtime shared by restart and live catch-up.

    Expensive indicator/participation/volatility engines advance only for rows after
    their checkpoint watermark. Structure/Volume links and Round-2 summaries are
    reconstructed from already-frozen histories because they are derived read-models,
    not stateful market engines. This keeps semantic parity while avoiding the costly
    full Volume engine replay on every process start.
    """

    def __init__(
        self,
        inputs: AnalysisInputSnapshot,
        *,
        symbol: str,
        clock: CausalBarClock | None = None,
        volatility_profile: str = "Dengeli",
    ) -> None:
        self.inputs = inputs
        self.symbol = symbol.strip().upper()
        self.clock = clock or CausalBarClock()
        self.volatility_profile = volatility_profile
        self._ham = {
            timeframe: _HamRuntime(
                engine=HamEvidenceEngine(
                    raw_config=RawIndicatorConfig(profile=ham_profile_for_timeframe(timeframe)),
                    evidence_config=HamEvidenceConfig(),
                )
            )
            for timeframe in inputs.timeframes
        }
        volume_config = VolumeParticipationConfig()
        lifecycle_config = ParticipationLifecycleConfig()
        self._volume = {
            timeframe: _VolumeRuntime(
                engine=VolumeEvidenceEngine(
                    symbol=self.symbol,
                    timeframe=timeframe,
                    config=volume_config,
                    lifecycle_config=lifecycle_config,
                )
            )
            for timeframe in inputs.timeframes
        }
        self._volatility = {
            timeframe: _VolatilityRuntime(
                engine=RuntimeVolatilityDirectionTransitionEngine(
                    VolatilityBandsConfig(profile=volatility_profile, timeframe=timeframe)
                ),
                snapshots=[],
            )
            for timeframe in inputs.timeframes
            if timeframe in VOLATILITY_TIMEFRAMES
        }
        self._watermarks = {timeframe: -1 for timeframe in inputs.timeframes}

    @property
    def watermarks(self) -> Mapping[str, int]:
        return dict(self._watermarks)

    def export_checkpoint(self) -> SupportingRuntimeCheckpoint:
        detached = pickle.loads(
            pickle.dumps(
                (
                    tuple(self._ham.items()),
                    tuple(self._volume.items()),
                    tuple(self._volatility.items()),
                ),
                protocol=pickle.HIGHEST_PROTOCOL,
            )
        )
        ham, volume, volatility = detached
        return SupportingRuntimeCheckpoint(
            symbol=self.symbol,
            timeframes=tuple(self.inputs.timeframes),
            watermarks=tuple((tf, int(self._watermarks[tf])) for tf in self.inputs.timeframes),
            ham=ham,
            volume=volume,
            volatility=volatility,
        )

    def restore_checkpoint(self, checkpoint: SupportingRuntimeCheckpoint) -> None:
        if checkpoint.symbol != self.symbol:
            raise ValueError(
                f"supporting checkpoint symbol mismatch: {checkpoint.symbol!r} != {self.symbol!r}"
            )
        expected = tuple(self.inputs.timeframes)
        if checkpoint.timeframes != expected:
            raise ValueError(
                f"supporting checkpoint timeframe mismatch: {checkpoint.timeframes!r} != {expected!r}"
            )
        watermarks = checkpoint.watermark_map
        if set(watermarks) != set(expected):
            raise ValueError("supporting checkpoint watermark set is incomplete")
        for timeframe in expected:
            frame = self.inputs.for_timeframe(timeframe).input_batch.frame
            index = int(watermarks[timeframe])
            if index < -1 or index >= len(frame):
                raise ValueError(
                    f"supporting checkpoint watermark outside current {timeframe} frame: {index}"
                )

        ham, volume, volatility = pickle.loads(
            pickle.dumps(
                (dict(checkpoint.ham), dict(checkpoint.volume), dict(checkpoint.volatility)),
                protocol=pickle.HIGHEST_PROTOCOL,
            )
        )
        if set(ham) != set(expected) or set(volume) != set(expected):
            raise ValueError("supporting checkpoint engine set does not match requested timeframes")
        if set(volatility) != set(self._volatility):
            raise ValueError("supporting checkpoint volatility engine set does not match requested timeframes")
        self._ham = ham
        self._volume = volume
        self._volatility = volatility
        self._watermarks = watermarks

    def advance(self) -> None:
        """Advance every supporting engine through only its unseen closed rows."""

        for timeframe in self.inputs.timeframes:
            frame = self.inputs.for_timeframe(timeframe).input_batch.frame
            start = int(self._watermarks[timeframe]) + 1
            if start < 0 or start > len(frame):
                raise ValueError(f"invalid supporting start index for {timeframe}: {start}")
            if start == len(frame):
                continue
            for index, row in enumerate(frame.iloc[start:].to_dict("records"), start=start):
                self._ham[timeframe].engine.update(row)
                self._volume[timeframe].engine.update(row)
                if timeframe in self._volatility:
                    snapshot = self._volatility[timeframe].engine.update(row)
                    self._volatility[timeframe].snapshots.append(snapshot)
                self._watermarks[timeframe] = index

    @staticmethod
    def _structure_snapshots(
        structure_replay: StructureLocationMTFResult,
        timeframes: tuple[str, ...],
    ) -> tuple[Any, ...]:
        return tuple(
            structure_replay.replay_for(timeframe).market_structure
            for timeframe in timeframes
        )

    def freeze(self, *, structure_replay: StructureLocationMTFResult) -> SupportingReplayState:
        """Build immutable public replays from already-advanced engine histories."""

        ham_rows: list[HamTimeframeEvidenceReplay] = []
        volume_rows: list[VolumeTimeframeEvidenceReplay] = []

        for timeframe in self.inputs.timeframes:
            input_row = self.inputs.for_timeframe(timeframe)
            batch = input_row.input_batch

            ham_engine = self._ham[timeframe].engine
            ham_history = ham_engine.history
            ham_latest = ham_engine.snapshot
            if ham_latest is None or len(ham_history) != len(batch.frame):
                raise ValueError(
                    f"HAM runtime/history mismatch for {self.symbol} {timeframe}: "
                    f"{len(ham_history)} != {len(batch.frame)}"
                )
            ham_rows.append(
                HamTimeframeEvidenceReplay(
                    symbol=self.symbol,
                    timeframe=timeframe,
                    profile=ham_profile_for_timeframe(timeframe),
                    input_batch=batch,
                    history=ham_history,
                    latest=ham_latest,
                )
            )

            volume_engine = self._volume[timeframe].engine
            history = volume_engine.history
            latest = volume_engine.snapshot
            if latest is None or len(history) != len(batch.frame):
                raise ValueError(
                    f"Volume runtime/history mismatch for {self.symbol} {timeframe}: "
                    f"{len(history)} != {len(batch.frame)}"
                )
            structure_snapshot = structure_replay.replay_for(timeframe).market_structure
            links = link_structure_events_to_volume_indexed(
                structure_snapshot.events,
                history,
                pre_event_bars=2,
                follow_through_bars=2,
            )
            unlinked = find_participation_without_structure(
                history,
                structure_snapshot.events,
            )
            volume_rows.append(
                VolumeTimeframeEvidenceReplay(
                    symbol=self.symbol,
                    timeframe=timeframe,
                    input_batch=batch,
                    history=history,
                    latest=latest,
                    event_links=links,
                    participation_without_structure=unlinked,
                    replay_data_quality=_replay_data_quality(input_row.raw_frame, batch),
                    excluded_tail_bar_count=_trailing_excluded_count(input_row.raw_frame),
                )
            )

        ham = HamMTFEvidenceReplay(
            symbol=self.symbol,
            timeframes=tuple(self.inputs.timeframes),
            timeframe_replays=tuple(ham_rows),
        )
        volume_tuple = tuple(volume_rows)
        volume = VolumeMTFEvidenceReplay(
            symbol=self.symbol,
            timeframes=tuple(self.inputs.timeframes),
            timeframe_replays=volume_tuple,
            round2=build_volume_round2_assessment(
                symbol=self.symbol,
                timeframe_replays=volume_tuple,
                structure_snapshots=self._structure_snapshots(
                    structure_replay,
                    tuple(self.inputs.timeframes),
                ),
                clock=self.clock,
            ),
        )

        volatility_by_timeframe: dict[str, VolatilityTimeframeReplay] = {}
        volatility_timeframes = tuple(
            timeframe for timeframe in VOLATILITY_TIMEFRAMES if timeframe in self.inputs.timeframes
        )
        for timeframe in volatility_timeframes:
            runtime = self._volatility[timeframe]
            expected_count = len(self.inputs.for_timeframe(timeframe).input_batch.frame)
            if len(runtime.snapshots) != expected_count:
                raise ValueError(
                    f"Volatility runtime/history mismatch for {self.symbol} {timeframe}: "
                    f"{len(runtime.snapshots)} != {expected_count}"
                )
            volatility_by_timeframe[timeframe] = VolatilityTimeframeReplay(
                symbol=self.symbol,
                timeframe=timeframe,
                snapshots=tuple(runtime.snapshots),
            )
        volatility = VolatilityMTFReplay(
            symbol=self.symbol,
            timeframes=volatility_timeframes,
            by_timeframe=volatility_by_timeframe,
            profile=self.volatility_profile,
        )
        return SupportingReplayState(
            ham=ham,
            volume=volume,
            volatility=volatility,
        )


__all__ = [
    "IncrementalSupportingReplayRuntime",
    "SupportingReplayState",
    "SupportingRuntimeCheckpoint",
]
