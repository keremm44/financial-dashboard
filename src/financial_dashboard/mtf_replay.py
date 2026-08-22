from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .analysis_config import ANALYSIS_TIMEFRAMES
from .data.engine_input import EngineInputBatch, prepare_engine_input
from .data.identity import normalize_symbol
from .data.parquet_store import ParquetOHLCVStore
from .engines.market_structure_engine import MarketStructureEngine
from .engines.market_structure_evidence import MarketStructureExport
from .engines.market_structure_events import (
    MarketStructureEventRecord,
    MarketStructureScopeSnapshot,
)
from .engines.models import EngineResult


# Backwards-compatible public name; canonical definition lives in analysis_config.
FOUNDATION_MARKET_STRUCTURE_TIMEFRAMES = ANALYSIS_TIMEFRAMES


@dataclass(frozen=True, slots=True)
class MarketStructureTimeframeSnapshot:
    """Namespaced immutable state produced by exactly one timeframe engine."""

    symbol: str
    timeframe: str
    as_of: Any
    bar_count: int
    result: EngineResult | None
    export: MarketStructureExport | None
    events: tuple[MarketStructureEventRecord, ...]
    external_scope: MarketStructureScopeSnapshot | None
    internal_scope: MarketStructureScopeSnapshot | None


@dataclass(frozen=True, slots=True)
class TimeframeReplay:
    timeframe: str
    input_batch: EngineInputBatch
    results: tuple[EngineResult, ...]
    snapshot: EngineResult | None
    structure: MarketStructureTimeframeSnapshot | None = None


@dataclass(frozen=True, slots=True)
class MTFReplayResult:
    symbol: str
    timeframes: tuple[str, ...]
    replays: dict[str, TimeframeReplay]
    structure_snapshots: tuple[MarketStructureTimeframeSnapshot, ...] = ()

    def structure_for(self, timeframe: str) -> MarketStructureTimeframeSnapshot:
        normalized = timeframe.strip().lower()
        for snapshot in self.structure_snapshots:
            if snapshot.timeframe == normalized:
                return snapshot
        raise KeyError(f"Market Structure timeframe not replayed: {timeframe}")


def namespace_market_structure_export(
    export: MarketStructureExport,
    *,
    symbol: str,
    timeframe: str,
) -> MarketStructureExport:
    events = tuple(
        event.with_namespace(symbol=symbol, timeframe=timeframe)
        for event in export.events
    )
    by_uid = {
        original.event_uid: namespaced_event
        for original, namespaced_event in zip(export.events, events, strict=True)
    }

    def namespaced(record: MarketStructureEventRecord | None) -> MarketStructureEventRecord | None:
        if record is None:
            return None
        return by_uid.get(record.event_uid, record.with_namespace(symbol=symbol, timeframe=timeframe))

    latest_external = namespaced(export.latest_external_event)
    latest_internal = namespaced(export.latest_internal_event)
    external_scope = (
        replace(export.external_scope, latest_event=latest_external)
        if export.external_scope is not None
        else None
    )
    internal_scope = (
        replace(export.internal_scope, latest_event=latest_internal)
        if export.internal_scope is not None
        else None
    )
    return replace(
        export,
        events=events,
        latest_external_event=latest_external,
        latest_internal_event=latest_internal,
        external_scope=external_scope,
        internal_scope=internal_scope,
    )


def market_structure_timeframe_snapshot(
    *,
    symbol: str,
    timeframe: str,
    batch: EngineInputBatch,
    engine: MarketStructureEngine,
) -> MarketStructureTimeframeSnapshot:
    normalized_symbol = normalize_symbol(symbol)
    export = engine.export_contract
    namespaced_export = (
        namespace_market_structure_export(
            export,
            symbol=normalized_symbol,
            timeframe=timeframe,
        )
        if export is not None
        else None
    )
    result = engine.snapshot()
    return MarketStructureTimeframeSnapshot(
        symbol=normalized_symbol,
        timeframe=timeframe.strip().lower(),
        as_of=None if result is None else result.timestamp,
        bar_count=len(batch.frame),
        result=result,
        export=namespaced_export,
        events=() if namespaced_export is None else namespaced_export.events,
        external_scope=None if namespaced_export is None else namespaced_export.external_scope,
        internal_scope=None if namespaced_export is None else namespaced_export.internal_scope,
    )


class CachedMarketStructureMTFRunner:
    """Replay Market Structure independently across cached timeframes."""

    def __init__(self, store: ParquetOHLCVStore) -> None:
        self.store = store

    def run(
        self,
        *,
        symbol: str,
        timeframes: tuple[str, ...] = ("15m", "30m", "1h", "2h", "4h", "1d"),
    ) -> MTFReplayResult:
        normalized_symbol = normalize_symbol(symbol)
        normalized_timeframes = tuple(timeframe.strip().lower() for timeframe in timeframes)
        if not normalized_timeframes or not all(normalized_timeframes):
            raise ValueError("at least one non-empty Market Structure timeframe is required")
        if len(set(normalized_timeframes)) != len(normalized_timeframes):
            raise ValueError("Market Structure timeframes must be unique")

        replays: dict[str, TimeframeReplay] = {}
        structure_snapshots: list[MarketStructureTimeframeSnapshot] = []
        for timeframe in normalized_timeframes:
            cached = self.store.load(normalized_symbol, timeframe)
            batch = prepare_engine_input(cached)
            engine = MarketStructureEngine()
            results = tuple(engine.replay(batch.frame))
            structure = market_structure_timeframe_snapshot(
                symbol=normalized_symbol,
                timeframe=timeframe,
                batch=batch,
                engine=engine,
            )
            structure_snapshots.append(structure)
            replays[timeframe] = TimeframeReplay(
                timeframe=timeframe,
                input_batch=batch,
                results=results,
                snapshot=engine.snapshot(),
                structure=structure,
            )
        return MTFReplayResult(
            symbol=normalized_symbol,
            timeframes=normalized_timeframes,
            replays=replays,
            structure_snapshots=tuple(structure_snapshots),
        )

    def run_foundation(self, *, symbol: str) -> MTFReplayResult:
        """Replay the approved 1D/4H/2H/1H/30m foundation independently."""

        return self.run(
            symbol=symbol,
            timeframes=FOUNDATION_MARKET_STRUCTURE_TIMEFRAMES,
        )


def replay_foundation_market_structure(
    store: ParquetOHLCVStore,
    *,
    symbol: str,
) -> MTFReplayResult:
    """Public convenience entry point for the approved five-timeframe replay."""

    return CachedMarketStructureMTFRunner(store).run_foundation(symbol=symbol)
