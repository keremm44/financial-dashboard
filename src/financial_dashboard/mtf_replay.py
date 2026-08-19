from __future__ import annotations

from dataclasses import dataclass

from .data.engine_input import EngineInputBatch, prepare_engine_input
from .data.parquet_store import ParquetOHLCVStore
from .engines.market_structure import MarketStructureEngine
from .engines.models import EngineResult


@dataclass(frozen=True, slots=True)
class TimeframeReplay:
    timeframe: str
    input_batch: EngineInputBatch
    results: tuple[EngineResult, ...]
    snapshot: EngineResult | None


@dataclass(frozen=True, slots=True)
class MTFReplayResult:
    symbol: str
    timeframes: tuple[str, ...]
    replays: dict[str, TimeframeReplay]


class CachedMarketStructureMTFRunner:
    """Replay Market Structure independently across cached timeframes.

    Parquet cache is the source of truth. Every timeframe receives a fresh engine
    instance so structure state never leaks across timeframe boundaries.
    """

    def __init__(self, store: ParquetOHLCVStore) -> None:
        self.store = store

    def run(
        self,
        *,
        symbol: str,
        timeframes: tuple[str, ...] = ("15m", "30m", "1h", "2h", "4h", "1d"),
    ) -> MTFReplayResult:
        replays: dict[str, TimeframeReplay] = {}
        for timeframe in timeframes:
            cached = self.store.load(symbol, timeframe)
            batch = prepare_engine_input(cached)
            engine = MarketStructureEngine()
            results = tuple(engine.replay(batch.frame))
            replays[timeframe] = TimeframeReplay(
                timeframe=timeframe,
                input_batch=batch,
                results=results,
                snapshot=engine.snapshot(),
            )
        return MTFReplayResult(symbol=symbol, timeframes=timeframes, replays=replays)
