from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

from financial_dashboard.data.analysis_inputs import AnalysisInputSnapshot, load_analysis_inputs
from financial_dashboard.data.engine_input import EngineInputBatch
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.engines.stabil_support_behavior import (
    StabilSupportBehaviorConfig,
    StabilSupportBehaviorSnapshot,
    build_support_behavior,
)
from financial_dashboard.engines.stabil_support_lifecycle import (
    StabilSupportLifecycleSnapshot,
    build_daily_support_observations,
    build_support_lifecycle,
)
from financial_dashboard.engines.stabil_trend_engine import StabilTrendConfig


STABIL_SUPPORT_TIMEFRAME = "1d"


@dataclass(frozen=True, slots=True)
class StabilSupportReplayResult:
    symbol: str
    timeframe: str
    input_batch: EngineInputBatch
    snapshot: StabilSupportLifecycleSnapshot
    behavior: StabilSupportBehaviorSnapshot | None = None


@dataclass(frozen=True, slots=True)
class StabilSupportReplayPoint:
    as_of: object
    close: float
    snapshot: StabilSupportLifecycleSnapshot
    behavior: StabilSupportBehaviorSnapshot | None = None


@dataclass(frozen=True, slots=True)
class StabilSupportHistoricalReplay:
    symbol: str
    timeframe: str
    points: tuple[StabilSupportReplayPoint, ...]

    @property
    def latest(self) -> StabilSupportLifecycleSnapshot | None:
        return None if not self.points else self.points[-1].snapshot

    @property
    def latest_behavior(self) -> StabilSupportBehaviorSnapshot | None:
        return None if not self.points else self.points[-1].behavior


class StabilSupportReplayRunner:
    """Run the daily Stabil support lifecycle from the shared prepared input snapshot."""

    def __init__(
        self,
        store: ParquetOHLCVStore,
        *,
        config: StabilTrendConfig | None = None,
        behavior_config: StabilSupportBehaviorConfig | None = None,
    ) -> None:
        self.store = store
        self.config = config or StabilTrendConfig()
        self.behavior_config = behavior_config or StabilSupportBehaviorConfig()

    def replay(
        self,
        symbol: str,
        *,
        input_snapshot: AnalysisInputSnapshot | None = None,
    ) -> StabilSupportReplayResult:
        clean_symbol = normalize_symbol(symbol)
        inputs = input_snapshot
        if inputs is None:
            inputs = load_analysis_inputs(
                self.store,
                symbol=clean_symbol,
                timeframes=(STABIL_SUPPORT_TIMEFRAME,),
            )
        else:
            inputs.validate_request(
                symbol=clean_symbol,
                timeframes=(STABIL_SUPPORT_TIMEFRAME,),
            )

        batch = inputs.for_timeframe(STABIL_SUPPORT_TIMEFRAME).input_batch
        observations = build_daily_support_observations(
            batch.frame,
            config=self.config,
        )
        snapshot = build_support_lifecycle(
            observations,
            min_tick=self.config.min_tick,
        )
        behavior = build_support_behavior(
            observations,
            snapshot,
            config=self.behavior_config,
            min_tick=self.config.min_tick,
        )
        return StabilSupportReplayResult(
            symbol=clean_symbol,
            timeframe=STABIL_SUPPORT_TIMEFRAME,
            input_batch=batch,
            snapshot=snapshot,
            behavior=behavior,
        )


class StabilSupportHistoricalReplayRunner:
    """Prefix-safe daily replay for inspection and hypothesis measurement.

    The full observation stream is causal because each support is exposed only after
    its confirmation/availability boundary. Each replay point then rebuilds the
    lifecycle and descriptive support behaviour from the prefix available at that
    point; future tails cannot alter an earlier point.
    """

    def __init__(
        self,
        store: ParquetOHLCVStore,
        *,
        config: StabilTrendConfig | None = None,
        behavior_config: StabilSupportBehaviorConfig | None = None,
    ) -> None:
        self.store = store
        self.config = config or StabilTrendConfig()
        self.behavior_config = behavior_config or StabilSupportBehaviorConfig()

    def replay(
        self,
        symbol: str,
        *,
        input_snapshot: AnalysisInputSnapshot | None = None,
        minimum_bars: int = 1,
        step: int = 1,
        max_points: int | None = 100,
        progress: Callable[[int, int, object], None] | None = None,
    ) -> StabilSupportHistoricalReplay:
        if minimum_bars < 1:
            raise ValueError("minimum_bars must be >= 1")
        if step < 1:
            raise ValueError("step must be >= 1")
        if max_points is not None and max_points < 1:
            raise ValueError("max_points must be >= 1 when provided")

        latest = StabilSupportReplayRunner(
            self.store,
            config=self.config,
            behavior_config=self.behavior_config,
        ).replay(symbol, input_snapshot=input_snapshot)
        frame = latest.input_batch.frame
        observations = build_daily_support_observations(
            frame,
            config=self.config,
        )
        if not observations:
            return StabilSupportHistoricalReplay(
                symbol=latest.symbol,
                timeframe=STABIL_SUPPORT_TIMEFRAME,
                points=(),
            )

        candidate_indices = list(range(minimum_bars - 1, len(observations), step))
        if candidate_indices and candidate_indices[-1] != len(observations) - 1:
            candidate_indices.append(len(observations) - 1)
        if max_points is not None:
            candidate_indices = candidate_indices[-max_points:]

        points: list[StabilSupportReplayPoint] = []
        total = len(candidate_indices)
        for position, index in enumerate(candidate_indices, start=1):
            prefix = observations[: index + 1]
            snapshot = build_support_lifecycle(
                prefix,
                min_tick=self.config.min_tick,
            )
            behavior = build_support_behavior(
                prefix,
                snapshot,
                config=self.behavior_config,
                min_tick=self.config.min_tick,
            )
            point = StabilSupportReplayPoint(
                as_of=observations[index].timestamp,
                close=float(observations[index].close),
                snapshot=snapshot,
                behavior=behavior,
            )
            points.append(point)
            if progress is not None:
                progress(position, total, point.as_of)

        return StabilSupportHistoricalReplay(
            symbol=latest.symbol,
            timeframe=STABIL_SUPPORT_TIMEFRAME,
            points=tuple(points),
        )


def replay_stabil_support_from_cache(
    cache_root: str | Path,
    *,
    symbol: str,
    config: StabilTrendConfig | None = None,
    behavior_config: StabilSupportBehaviorConfig | None = None,
) -> StabilSupportReplayResult:
    return StabilSupportReplayRunner(
        ParquetOHLCVStore(Path(cache_root).expanduser()),
        config=config,
        behavior_config=behavior_config,
    ).replay(symbol)


def replay_stabil_support_history_from_cache(
    cache_root: str | Path,
    *,
    symbol: str,
    config: StabilTrendConfig | None = None,
    behavior_config: StabilSupportBehaviorConfig | None = None,
    minimum_bars: int = 1,
    step: int = 1,
    max_points: int | None = 100,
    progress: Callable[[int, int, object], None] | None = None,
) -> StabilSupportHistoricalReplay:
    return StabilSupportHistoricalReplayRunner(
        ParquetOHLCVStore(Path(cache_root).expanduser()),
        config=config,
        behavior_config=behavior_config,
    ).replay(
        symbol,
        minimum_bars=minimum_bars,
        step=step,
        max_points=max_points,
        progress=progress,
    )


__all__ = [
    "STABIL_SUPPORT_TIMEFRAME",
    "StabilSupportHistoricalReplay",
    "StabilSupportHistoricalReplayRunner",
    "StabilSupportReplayPoint",
    "StabilSupportReplayResult",
    "replay_stabil_support_from_cache",
    "replay_stabil_support_history_from_cache",
]
