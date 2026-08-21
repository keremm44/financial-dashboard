from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .data.parquet_store import ParquetOHLCVStore
from .engines.market_structure import MarketStructureConfig
from .engines.market_structure_state import BreakConfig
from .engines.mtf_story_context import classify_context
from .engines.mtf_story_engine import classify_story
from .engines.mtf_story_models import RawTimeframeEvidence, role_for_timeframe
from .engines.mtf_story_normalizer import normalize_timeframe_evidence
from .engines.mtf_story_trigger import classify_trigger
from .engines.pattern_compression_core import PatternCompressionConfig
from .engines.pattern_compression_engine import PatternCompressionEngine, PatternExport
from .engines.models import EngineResult
from .engines.structure_location import StructureZoneLinkConfig, ZoneConfluenceConfig
from .engines.support_resistance_engine import SupportResistanceConfig
from .engines.three_domain_observer import (
    CausalStructureEventObservation,
    FOUNDATION_OBSERVER_TIMEFRAMES,
    LocationContextSnapshot,
    MTFPressureSnapshot,
    OpposingZoneConflictConfig,
    StructureProgressionSnapshot,
    ThreeDomainObservation,
    build_location_context,
    build_mtf_pressure,
    build_structure_progression,
    combine_three_domains,
)
from .structure_location_replay import (
    CachedStructureLocationMTFRunner,
    CausalBarClock,
    StructureLocationMTFResult,
)


@dataclass(frozen=True, slots=True)
class PatternTimeframeSnapshot:
    symbol: str
    timeframe: str
    as_of: Any
    bar_count: int
    result: EngineResult | None
    export: PatternExport | None


@dataclass(frozen=True, slots=True)
class ThreeDomainReplayResult:
    symbol: str
    timeframes: tuple[str, ...]
    structure_location: StructureLocationMTFResult
    pattern_snapshots: tuple[PatternTimeframeSnapshot, ...]
    pressure: MTFPressureSnapshot
    structure: StructureProgressionSnapshot
    location: LocationContextSnapshot
    observation: ThreeDomainObservation

    def pattern_for(self, timeframe: str) -> PatternTimeframeSnapshot:
        normalized = timeframe.strip().lower()
        for snapshot in self.pattern_snapshots:
            if snapshot.timeframe == normalized:
                return snapshot
        raise KeyError(f"Pattern/Compression timeframe not replayed: {timeframe}")


class CachedThreeDomainObserverRunner:
    """Replay all three domains continuously, then combine their immutable facts.

    Market Structure and S/R are replayed independently by the causal Round-2
    runner. Pattern/Compression receives every same closed+complete timeframe bar
    separately. Cross-domain combination happens only after all engines finish; no
    domain or higher timeframe can gate another engine's calculation or retention.
    """

    def __init__(
        self,
        store: ParquetOHLCVStore,
        *,
        clock: CausalBarClock | None = None,
        confluence_config: ZoneConfluenceConfig | None = None,
        link_config: StructureZoneLinkConfig | None = None,
        market_structure_config: MarketStructureConfig | None = None,
        break_config: BreakConfig | None = None,
        support_resistance_config: SupportResistanceConfig | None = None,
        pattern_compression_config: PatternCompressionConfig | None = None,
        opposing_zone_config: OpposingZoneConflictConfig | None = None,
    ) -> None:
        self.store = store
        self.clock = clock or CausalBarClock()
        self.pattern_compression_config = pattern_compression_config
        self.opposing_zone_config = opposing_zone_config
        self.structure_location_runner = CachedStructureLocationMTFRunner(
            store,
            clock=self.clock,
            confluence_config=confluence_config,
            link_config=link_config,
            market_structure_config=market_structure_config,
            break_config=break_config,
            support_resistance_config=support_resistance_config,
        )

    def run(
        self,
        *,
        symbol: str,
        timeframes: tuple[str, ...] = FOUNDATION_OBSERVER_TIMEFRAMES,
    ) -> ThreeDomainReplayResult:
        normalized = tuple(timeframe.strip().lower() for timeframe in timeframes)
        unsupported = tuple(
            timeframe
            for timeframe in normalized
            if timeframe not in FOUNDATION_OBSERVER_TIMEFRAMES
        )
        if unsupported:
            raise ValueError(
                "unsupported three-domain observer timeframes: "
                + ", ".join(unsupported)
            )
        structure_location = self.structure_location_runner.run(
            symbol=symbol,
            timeframes=normalized,
        )

        pattern_snapshots: list[PatternTimeframeSnapshot] = []
        timeframe_states = []
        final_availability = []
        observed_events: list[CausalStructureEventObservation] = []
        established_zones = []

        for timeframe in normalized:
            replay = structure_location.replay_for(timeframe)
            engine = PatternCompressionEngine(self.pattern_compression_config)
            for _, bar in replay.input_batch.frame.iterrows():
                engine.update(bar)
            pattern_result = engine.snapshot()
            pattern_export = engine.export_contract
            pattern_snapshot = PatternTimeframeSnapshot(
                symbol=symbol,
                timeframe=timeframe,
                as_of=None if pattern_result is None else pattern_result.timestamp,
                bar_count=len(replay.input_batch.frame),
                result=pattern_result,
                export=pattern_export,
            )
            pattern_snapshots.append(pattern_snapshot)

            market = replay.market_structure
            raw = RawTimeframeEvidence(
                timeframe=timeframe,
                role=role_for_timeframe(timeframe),
                data_quality=replay.input_batch.source_quality.status,
                market_structure=market.result,
                market_structure_export=market.export,
                pattern_compression=pattern_result,
                pattern_export=pattern_export,
            )
            timeframe_states.append(normalize_timeframe_evidence(raw))

            final_timestamp = replay.input_batch.frame.iloc[-1]["timestamp"]
            final_availability.append(self.clock.available_at(final_timestamp, timeframe))
            observed_events.extend(
                CausalStructureEventObservation(
                    event=event,
                    available_at=self.clock.available_at(event.confirmed_at, timeframe),
                )
                for event in market.events
                if event.confirmed_at is not None
            )
            established_zones.extend(replay.support_resistance.zones)

        states = tuple(timeframe_states)
        context = classify_context(
            [state for state in states if state.timeframe in {"1d", "4h", "2h"}]
        )
        trigger = classify_trigger(
            [state for state in states if state.timeframe in {"1h", "30m", "15m"}]
        )
        story = classify_story(context, trigger, states)
        pressure = build_mtf_pressure(context, trigger, story, states)

        as_of = max(final_availability)
        structure = build_structure_progression(
            observed_events,
            as_of=as_of,
            timeframes=normalized,
            symbol=symbol,
        )
        location = build_location_context(
            established_zones,
            structure_location.confluence,
            structure_location.location_outcomes,
            symbol=symbol,
            conflict_config=self.opposing_zone_config,
        )
        observation = combine_three_domains(
            symbol=symbol,
            as_of=as_of,
            pressure=pressure,
            structure=structure,
            location=location,
        )
        return ThreeDomainReplayResult(
            symbol=symbol,
            timeframes=normalized,
            structure_location=structure_location,
            pattern_snapshots=tuple(pattern_snapshots),
            pressure=pressure,
            structure=structure,
            location=location,
            observation=observation,
        )

    def run_foundation(self, *, symbol: str) -> ThreeDomainReplayResult:
        return self.run(symbol=symbol, timeframes=FOUNDATION_OBSERVER_TIMEFRAMES)


def replay_foundation_three_domains(
    store: ParquetOHLCVStore,
    *,
    symbol: str,
    clock: CausalBarClock | None = None,
) -> ThreeDomainReplayResult:
    return CachedThreeDomainObserverRunner(store, clock=clock).run_foundation(
        symbol=symbol
    )
