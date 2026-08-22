from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import pandas as pd

from .analysis_config import BAR_DURATIONS, CLOSE_LABELLED_TIMEFRAMES, LEFT_LABEL_DURATIONS
from .data.analysis_inputs import AnalysisInputSnapshot
from .data.engine_input import EngineInputBatch, prepare_engine_input
from .data.identity import normalize_symbol
from .data.parquet_store import ParquetOHLCVStore
from .engines.market_structure import MarketStructureConfig
from .engines.market_structure_engine import MarketStructureEngine
from .engines.market_structure_state import BreakConfig, EVENT_BOS, EVENT_CHOCH
from .engines.structure_location import (
    CausalZoneObservation,
    StructureLocationOutcome,
    StructureZoneLink,
    StructureZoneLinkConfig,
    ZoneConfluenceCluster,
    ZoneConfluenceConfig,
    build_zone_confluence,
    evaluate_structure_event_location,
)
from .engines.support_resistance_engine import (
    SupportResistanceConfig,
    SupportResistanceExport,
    SupportResistanceRangeEngine,
)
from .engines.support_resistance_zones import (
    SupportResistanceZone,
    ZoneLifecycle,
    ZoneLifecycleEvent,
)
from .engines.models import EngineResult
from .mtf_replay import (
    FOUNDATION_MARKET_STRUCTURE_TIMEFRAMES,
    MarketStructureTimeframeSnapshot,
    market_structure_timeframe_snapshot,
)


@dataclass(frozen=True, slots=True)
class CausalBarClock:
    """Convert canonical labels to availability while retaining physical bar duration."""

    durations: tuple[tuple[str, pd.Timedelta], ...] = tuple(LEFT_LABEL_DURATIONS.items())
    close_labelled_timeframes: tuple[str, ...] = tuple(sorted(CLOSE_LABELLED_TIMEFRAMES))
    bar_durations: tuple[tuple[str, pd.Timedelta], ...] = tuple(BAR_DURATIONS.items())

    def __post_init__(self) -> None:
        normalized = tuple(
            (timeframe.strip().lower(), pd.Timedelta(duration))
            for timeframe, duration in self.durations
        )
        keys = tuple(timeframe for timeframe, _ in normalized)
        if not all(keys) or len(set(keys)) != len(keys):
            raise ValueError("causal durations must use unique, non-empty timeframes")
        if any(duration <= pd.Timedelta(0) for _, duration in normalized):
            raise ValueError("causal durations must be positive")
        close_labelled = tuple(
            str(timeframe).strip().lower() for timeframe in self.close_labelled_timeframes
        )
        if not all(close_labelled) or len(set(close_labelled)) != len(close_labelled):
            raise ValueError(
                "close-labelled timeframes must use unique, non-empty names"
            )
        physical = tuple(
            (timeframe.strip().lower(), pd.Timedelta(duration))
            for timeframe, duration in self.bar_durations
        )
        physical_keys = tuple(timeframe for timeframe, _ in physical)
        if not all(physical_keys) or len(set(physical_keys)) != len(physical_keys):
            raise ValueError("bar durations must use unique, non-empty timeframes")
        if any(duration <= pd.Timedelta(0) for _, duration in physical):
            raise ValueError("bar durations must be positive")
        object.__setattr__(self, "durations", normalized)
        object.__setattr__(self, "close_labelled_timeframes", close_labelled)
        object.__setattr__(self, "bar_durations", physical)

    def available_at(self, timestamp: Any, timeframe: str) -> pd.Timestamp:
        if timestamp is None:
            raise ValueError("causal availability requires a bar timestamp")
        normalized = timeframe.strip().lower()
        timestamp_value = pd.Timestamp(timestamp)
        duration = dict(self.durations).get(normalized)
        if duration is not None:
            return timestamp_value + duration
        if normalized in self.close_labelled_timeframes:
            return timestamp_value
        raise ValueError(f"causal timestamp contract is not configured for timeframe: {timeframe}")

    def bar_duration(self, timeframe: str) -> pd.Timedelta:
        normalized = timeframe.strip().lower()
        # Explicit duration overrides retain legacy/custom-clock semantics.
        explicit = dict(self.durations).get(normalized)
        if explicit is not None:
            return explicit
        duration = dict(self.bar_durations).get(normalized)
        if duration is None:
            raise ValueError(f"bar duration is not configured for timeframe: {timeframe}")
        return duration


@dataclass(frozen=True, slots=True)
class SupportResistanceTimeframeSnapshot:
    symbol: str
    timeframe: str
    as_of: Any
    available_at: Any
    bar_count: int
    result: EngineResult | None
    export: SupportResistanceExport
    zones: tuple[SupportResistanceZone, ...]
    active_zones: tuple[SupportResistanceZone, ...]
    lifecycle_events: tuple[ZoneLifecycleEvent, ...]


@dataclass(frozen=True, slots=True)
class StructureLocationTimeframeReplay:
    timeframe: str
    input_batch: EngineInputBatch
    market_structure: MarketStructureTimeframeSnapshot
    support_resistance: SupportResistanceTimeframeSnapshot


@dataclass(frozen=True, slots=True)
class StructureLocationMTFResult:
    symbol: str
    timeframes: tuple[str, ...]
    replays: dict[str, StructureLocationTimeframeReplay]
    confluence: tuple[ZoneConfluenceCluster, ...]
    location_outcomes: tuple[StructureLocationOutcome, ...]
    event_zone_links: tuple[StructureZoneLink, ...]

    def replay_for(self, timeframe: str) -> StructureLocationTimeframeReplay:
        normalized = timeframe.strip().lower()
        try:
            return self.replays[normalized]
        except KeyError as error:
            raise KeyError(f"structure/location timeframe not replayed: {timeframe}") from error


def namespace_support_resistance_export(
    export: SupportResistanceExport,
    *,
    symbol: str,
    timeframe: str,
) -> SupportResistanceExport:
    zones = tuple(
        zone.with_namespace(symbol=symbol, timeframe=timeframe)
        for zone in export.zones
    )
    lifecycle_events = tuple(
        event.with_namespace(symbol=symbol, timeframe=timeframe)
        for event in export.zone_lifecycle_events
    )
    return replace(export, zones=zones, zone_lifecycle_events=lifecycle_events)


def _support_snapshot(
    *,
    symbol: str,
    timeframe: str,
    batch: EngineInputBatch,
    engine: SupportResistanceRangeEngine,
    clock: CausalBarClock,
) -> SupportResistanceTimeframeSnapshot:
    export = namespace_support_resistance_export(
        engine.export_contract,
        symbol=symbol,
        timeframe=timeframe,
    )
    result = engine.snapshot()
    as_of = None if result is None else result.timestamp
    available_at = None if as_of is None else clock.available_at(as_of, timeframe)
    return SupportResistanceTimeframeSnapshot(
        symbol=symbol,
        timeframe=timeframe.strip().lower(),
        as_of=as_of,
        available_at=available_at,
        bar_count=len(batch.frame),
        result=result,
        export=export,
        zones=export.zones,
        active_zones=tuple(zone for zone in export.zones if zone.is_active),
        lifecycle_events=export.zone_lifecycle_events,
    )


def _latest_causal_observation(
    timeline: tuple[CausalZoneObservation, ...],
    event_available_at: Any,
) -> CausalZoneObservation | None:
    for observation in reversed(timeline):
        if observation.available_at <= event_available_at:
            return observation
    return None


class CachedStructureLocationMTFRunner:
    """Replay Market Structure and S/R independently, then join only causal facts."""

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
    ) -> None:
        self.store = store
        self.clock = clock or CausalBarClock()
        self.confluence_config = confluence_config or ZoneConfluenceConfig()
        self.link_config = link_config or StructureZoneLinkConfig()
        self.market_structure_config = market_structure_config
        self.break_config = break_config
        self.support_resistance_config = support_resistance_config

    def run(
        self,
        *,
        symbol: str,
        timeframes: tuple[str, ...] = FOUNDATION_MARKET_STRUCTURE_TIMEFRAMES,
        input_snapshot: AnalysisInputSnapshot | None = None,
    ) -> StructureLocationMTFResult:
        normalized_symbol = normalize_symbol(symbol)
        normalized_timeframes = tuple(timeframe.strip().lower() for timeframe in timeframes)
        if not normalized_timeframes or not all(normalized_timeframes):
            raise ValueError("at least one non-empty timeframe is required")
        if len(set(normalized_timeframes)) != len(normalized_timeframes):
            raise ValueError("timeframes must be unique after normalization")
        if input_snapshot is not None:
            input_snapshot.validate_request(
                symbol=normalized_symbol,
                timeframes=normalized_timeframes,
            )
        configured_timeframes = set(dict(self.clock.durations)) | set(
            self.clock.close_labelled_timeframes
        )
        missing_contracts = tuple(
            timeframe
            for timeframe in normalized_timeframes
            if timeframe not in configured_timeframes
        )
        if missing_contracts:
            raise ValueError(
                "causal timestamp contract is not configured for timeframes: "
                + ", ".join(missing_contracts)
            )

        replays: dict[str, StructureLocationTimeframeReplay] = {}
        timelines: dict[str, tuple[CausalZoneObservation, ...]] = {}
        all_events = []
        final_active_zones: list[SupportResistanceZone] = []

        for timeframe in normalized_timeframes:
            if input_snapshot is None:
                batch = prepare_engine_input(self.store.load(normalized_symbol, timeframe))
            else:
                batch = input_snapshot.for_timeframe(timeframe).input_batch
            market_engine = MarketStructureEngine(
                config=self.market_structure_config,
                break_config=self.break_config,
            )
            support_engine = SupportResistanceRangeEngine(
                config=self.support_resistance_config,
            )
            observations: list[CausalZoneObservation] = []

            for bar_index, (_, bar) in enumerate(batch.frame.iterrows()):
                support_engine.update(bar)
                market_engine.update(bar)
                observed_at = bar.get("timestamp")
                available_at = self.clock.available_at(observed_at, timeframe)
                relevant_zones = tuple(
                    zone.with_namespace(symbol=normalized_symbol, timeframe=timeframe)
                    for zone in support_engine.zones
                    if zone.is_confluence_eligible
                    or (
                        zone.lifecycle in {ZoneLifecycle.BROKEN, ZoneLifecycle.INVALIDATED}
                        and zone.last_transition_bar == bar_index
                    )
                )
                observations.append(
                    CausalZoneObservation(
                        symbol=normalized_symbol,
                        timeframe=timeframe,
                        bar_index=bar_index,
                        observed_at=observed_at,
                        available_at=available_at,
                        zones=relevant_zones,
                    )
                )

            market_snapshot = market_structure_timeframe_snapshot(
                symbol=normalized_symbol,
                timeframe=timeframe,
                batch=batch,
                engine=market_engine,
            )
            support_snapshot = _support_snapshot(
                symbol=normalized_symbol,
                timeframe=timeframe,
                batch=batch,
                engine=support_engine,
                clock=self.clock,
            )
            replays[timeframe] = StructureLocationTimeframeReplay(
                timeframe=timeframe,
                input_batch=batch,
                market_structure=market_snapshot,
                support_resistance=support_snapshot,
            )
            timelines[timeframe] = tuple(observations)
            all_events.extend(market_snapshot.events)
            final_active_zones.extend(
                zone for zone in support_snapshot.zones if zone.is_confluence_eligible
            )

        confluence = build_zone_confluence(final_active_zones, config=self.confluence_config)
        outcomes: list[StructureLocationOutcome] = []
        for event in all_events:
            if (
                event.event_type not in {EVENT_BOS, EVENT_CHOCH}
                or event.confirmed_at is None
                or event.timeframe is None
            ):
                continue
            event_available_at = self.clock.available_at(event.confirmed_at, event.timeframe)
            observations = tuple(
                observation
                for timeframe in normalized_timeframes
                if (
                    observation := _latest_causal_observation(
                        timelines[timeframe], event_available_at
                    )
                ) is not None
            )
            outcomes.append(
                evaluate_structure_event_location(
                    event,
                    observations,
                    event_available_at=event_available_at,
                    config=self.link_config,
                )
            )

        ordered_outcomes = tuple(
            sorted(
                outcomes,
                key=lambda outcome: (
                    pd.Timestamp(outcome.event_available_at),
                    outcome.event_uid,
                ),
            )
        )
        links = tuple(
            sorted(
                (link for outcome in ordered_outcomes for link in outcome.links),
                key=lambda link: (
                    pd.Timestamp(link.event_available_at),
                    link.event_uid,
                    -link.score,
                    link.zone_uid,
                ),
            )
        )
        return StructureLocationMTFResult(
            symbol=normalized_symbol,
            timeframes=normalized_timeframes,
            replays=replays,
            confluence=confluence,
            location_outcomes=ordered_outcomes,
            event_zone_links=links,
        )

    def run_foundation(self, *, symbol: str) -> StructureLocationMTFResult:
        return self.run(
            symbol=symbol,
            timeframes=FOUNDATION_MARKET_STRUCTURE_TIMEFRAMES,
        )


def replay_foundation_structure_location(
    store: ParquetOHLCVStore,
    *,
    symbol: str,
    clock: CausalBarClock | None = None,
) -> StructureLocationMTFResult:
    return CachedStructureLocationMTFRunner(store, clock=clock).run_foundation(symbol=symbol)
