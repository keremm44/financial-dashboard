from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES, normalize_timeframes
from financial_dashboard.data.analysis_inputs import AnalysisInputSnapshot, load_analysis_inputs
from financial_dashboard.data.engine_input import EngineInputBatch
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.engines.auction_engine import AuctionConfig
from financial_dashboard.engines.auction_estimated_profile import (
    EstimatedAuctionProfileEngine,
    EstimatedAuctionSnapshot,
)


@dataclass(frozen=True, slots=True)
class AuctionProfileReplayResult:
    symbol: str
    timeframe: str
    input_batch: EngineInputBatch
    snapshot: EstimatedAuctionSnapshot


@dataclass(frozen=True, slots=True)
class AuctionProfileReplayPoint:
    as_of: object
    close: float
    snapshot: EstimatedAuctionSnapshot


@dataclass(frozen=True, slots=True)
class AuctionProfileHistoricalReplay:
    symbol: str
    timeframe: str
    points: tuple[AuctionProfileReplayPoint, ...]

    @property
    def latest(self) -> EstimatedAuctionSnapshot | None:
        return None if not self.points else self.points[-1].snapshot


@dataclass(frozen=True, slots=True)
class AuctionProfileMTFReplay:
    symbol: str
    timeframes: tuple[str, ...]
    replays: tuple[AuctionProfileReplayResult, ...]

    def for_timeframe(self, timeframe: str) -> AuctionProfileReplayResult:
        for replay in self.replays:
            if replay.timeframe == timeframe:
                return replay
        raise KeyError(timeframe)


class AuctionProfileReplayRunner:
    def __init__(self, store: ParquetOHLCVStore) -> None:
        self.store = store

    def replay(
        self,
        symbol: str,
        *,
        timeframe: str,
        input_snapshot: AnalysisInputSnapshot | None = None,
    ) -> AuctionProfileReplayResult:
        clean_symbol = normalize_symbol(symbol)
        clean_tf = normalize_timeframes((timeframe,), supported=ANALYSIS_TIMEFRAMES, label="auction")[0]
        inputs = input_snapshot
        if inputs is None:
            inputs = load_analysis_inputs(self.store, symbol=clean_symbol, timeframes=(clean_tf,))
        else:
            inputs.validate_request(symbol=clean_symbol, timeframes=(clean_tf,))
        batch = inputs.for_timeframe(clean_tf).input_batch
        snapshot = EstimatedAuctionProfileEngine(AuctionConfig(timeframe=clean_tf)).analyze(batch.frame)
        return AuctionProfileReplayResult(clean_symbol, clean_tf, batch, snapshot)


class AuctionProfileMTFReplayRunner:
    def __init__(self, store: ParquetOHLCVStore) -> None:
        self.store = store

    def replay(
        self,
        symbol: str,
        *,
        timeframes: tuple[str, ...] = ANALYSIS_TIMEFRAMES,
        input_snapshot: AnalysisInputSnapshot | None = None,
    ) -> AuctionProfileMTFReplay:
        clean_symbol = normalize_symbol(symbol)
        clean_tfs = normalize_timeframes(timeframes, supported=ANALYSIS_TIMEFRAMES, label="auction")
        inputs = input_snapshot
        if inputs is None:
            inputs = load_analysis_inputs(self.store, symbol=clean_symbol, timeframes=clean_tfs)
        else:
            inputs.validate_request(symbol=clean_symbol, timeframes=clean_tfs)
        runner = AuctionProfileReplayRunner(self.store)
        replays = tuple(
            runner.replay(clean_symbol, timeframe=tf, input_snapshot=inputs)
            for tf in clean_tfs
        )
        return AuctionProfileMTFReplay(clean_symbol, clean_tfs, replays)


class AuctionProfileHistoricalReplayRunner:
    """Prefix-safe historical replay for estimated auction state inspection."""

    def __init__(self, store: ParquetOHLCVStore) -> None:
        self.store = store

    def replay(
        self,
        symbol: str,
        *,
        timeframe: str,
        input_snapshot: AnalysisInputSnapshot | None = None,
        minimum_bars: int = 1,
        step: int = 1,
        max_points: int | None = 100,
        progress: Callable[[int, int, object], None] | None = None,
    ) -> AuctionProfileHistoricalReplay:
        if minimum_bars < 1:
            raise ValueError("minimum_bars must be >= 1")
        if step < 1:
            raise ValueError("step must be >= 1")
        if max_points is not None and max_points < 1:
            raise ValueError("max_points must be >= 1 when provided")

        latest = AuctionProfileReplayRunner(self.store).replay(
            symbol,
            timeframe=timeframe,
            input_snapshot=input_snapshot,
        )
        frame = latest.input_batch.frame.reset_index(drop=True)
        indices = list(range(minimum_bars - 1, len(frame), step))
        if indices and indices[-1] != len(frame) - 1:
            indices.append(len(frame) - 1)
        if max_points is not None:
            indices = indices[-max_points:]

        points: list[AuctionProfileReplayPoint] = []
        total = len(indices)
        for position, index in enumerate(indices, start=1):
            prefix = frame.iloc[: index + 1].copy()
            snapshot = EstimatedAuctionProfileEngine(AuctionConfig(timeframe=latest.timeframe)).analyze(prefix)
            point = AuctionProfileReplayPoint(
                as_of=prefix.iloc[-1]["timestamp"],
                close=float(prefix.iloc[-1]["close"]),
                snapshot=snapshot,
            )
            points.append(point)
            if progress is not None:
                progress(position, total, point.as_of)
        return AuctionProfileHistoricalReplay(latest.symbol, latest.timeframe, tuple(points))


def replay_auction_profile_history_from_cache(
    cache_root: str | Path,
    *,
    symbol: str,
    timeframe: str,
    minimum_bars: int = 1,
    step: int = 1,
    max_points: int | None = 100,
) -> AuctionProfileHistoricalReplay:
    return AuctionProfileHistoricalReplayRunner(
        ParquetOHLCVStore(Path(cache_root).expanduser())
    ).replay(
        symbol,
        timeframe=timeframe,
        minimum_bars=minimum_bars,
        step=step,
        max_points=max_points,
    )


__all__ = [
    "AuctionProfileHistoricalReplay",
    "AuctionProfileHistoricalReplayRunner",
    "AuctionProfileMTFReplay",
    "AuctionProfileMTFReplayRunner",
    "AuctionProfileReplayPoint",
    "AuctionProfileReplayResult",
    "AuctionProfileReplayRunner",
    "replay_auction_profile_history_from_cache",
]
