from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from financial_dashboard.data.analysis_inputs import AnalysisInputSnapshot, load_analysis_inputs
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.engines.volatility_bands_fib_engine import VolatilityBandsConfig
from financial_dashboard.engines.volatility_direction_transition import (
    EarlyDirectionTransition,
    VolatilityDirectionSnapshot,
    VolatilityDirectionTransitionEngine,
)

VOLATILITY_TIMEFRAMES: tuple[str, ...] = ("1d", "4h", "2h")


@dataclass(frozen=True, slots=True)
class VolatilityTimeframeReplay:
    symbol: str
    timeframe: str
    snapshots: tuple[VolatilityDirectionSnapshot, ...]

    @property
    def latest(self) -> VolatilityDirectionSnapshot | None:
        return self.snapshots[-1] if self.snapshots else None


@dataclass(frozen=True, slots=True)
class VolatilityMTFReplay:
    symbol: str
    timeframes: tuple[str, ...]
    by_timeframe: Mapping[str, VolatilityTimeframeReplay]

    def for_timeframe(self, timeframe: str) -> VolatilityTimeframeReplay:
        return self.by_timeframe[timeframe.strip().lower()]


@dataclass(frozen=True, slots=True)
class DirectionLagRecord:
    timeframe: str
    direction: str
    early_index: int
    candidate_index: int | None
    confirmed_index: int | None
    candidate_lag_bars: int | None
    confirmed_lag_bars: int | None


def _state(snapshot: VolatilityDirectionSnapshot) -> str:
    return "" if snapshot.core_result is None else str(snapshot.core_result.state)


def direction_lag_records(replay: VolatilityMTFReplay) -> tuple[DirectionLagRecord, ...]:
    records: list[DirectionLagRecord] = []
    for timeframe in replay.timeframes:
        snapshots = replay.for_timeframe(timeframe).snapshots
        for i, snapshot in enumerate(snapshots):
            early = snapshot.early.state
            if early is EarlyDirectionTransition.NONE:
                continue
            if i > 0 and snapshots[i - 1].early.state is early:
                continue
            direction = "UP" if early is EarlyDirectionTransition.EARLY_UP else "DOWN"
            candidate_token = f"{direction}_CANDIDATE"
            confirmed_token = f"{direction}_CONFIRMED"
            candidate_index = None
            confirmed_index = None
            for j in range(i, len(snapshots)):
                state = _state(snapshots[j])
                if candidate_index is None and candidate_token in state:
                    candidate_index = j
                if confirmed_token in state:
                    confirmed_index = j
                    break
                opposite = "EARLY_DOWN" if direction == "UP" else "EARLY_UP"
                if j > i and snapshots[j].early.state.value == opposite:
                    break
            records.append(
                DirectionLagRecord(
                    timeframe=timeframe,
                    direction=direction,
                    early_index=i,
                    candidate_index=candidate_index,
                    confirmed_index=confirmed_index,
                    candidate_lag_bars=None if candidate_index is None else candidate_index - i,
                    confirmed_lag_bars=None if confirmed_index is None else confirmed_index - i,
                )
            )
    return tuple(records)


class VolatilityMTFReplayRunner:
    def __init__(self, store: ParquetOHLCVStore) -> None:
        self.store = store

    def replay(
        self,
        symbol: str,
        *,
        input_snapshot: AnalysisInputSnapshot | None = None,
        timeframes: tuple[str, ...] = VOLATILITY_TIMEFRAMES,
        profile: str = "Dengeli",
    ) -> VolatilityMTFReplay:
        normalized_symbol = normalize_symbol(symbol)
        requested = tuple(tf.strip().lower() for tf in timeframes)
        unsupported = tuple(tf for tf in requested if tf not in VOLATILITY_TIMEFRAMES)
        if unsupported:
            raise ValueError(f"unsupported volatility timeframe(s): {unsupported!r}")
        if input_snapshot is None:
            inputs = load_analysis_inputs(self.store, symbol=normalized_symbol, timeframes=requested)
        else:
            input_snapshot.validate_request(symbol=normalized_symbol, timeframes=requested)
            inputs = input_snapshot

        by_timeframe: dict[str, VolatilityTimeframeReplay] = {}
        for timeframe in requested:
            frame = inputs.for_timeframe(timeframe).input_batch.frame
            engine = VolatilityDirectionTransitionEngine(
                VolatilityBandsConfig(profile=profile, timeframe=timeframe)
            )
            snapshots = engine.replay(frame)
            by_timeframe[timeframe] = VolatilityTimeframeReplay(
                symbol=normalized_symbol,
                timeframe=timeframe,
                snapshots=snapshots,
            )
        return VolatilityMTFReplay(
            symbol=normalized_symbol,
            timeframes=requested,
            by_timeframe=by_timeframe,
        )


__all__ = [
    "DirectionLagRecord",
    "VOLATILITY_TIMEFRAMES",
    "VolatilityMTFReplay",
    "VolatilityMTFReplayRunner",
    "VolatilityTimeframeReplay",
    "direction_lag_records",
]
