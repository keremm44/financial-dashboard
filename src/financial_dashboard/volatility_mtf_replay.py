from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from financial_dashboard.data.analysis_inputs import AnalysisInputSnapshot, load_analysis_inputs
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.engines.volatility_bands_fib_engine import (
    VolatilityBandsConfig,
    VolatilityState,
)
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
    profile: str = "Dengeli"

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
    outcome: str = "UNCONFIRMED"
    window_end_index: int | None = None
    candidate_horizon_bars: int = 0
    confirmation_horizon_bars: int = 0


def _volatility_state(snapshot: VolatilityDirectionSnapshot) -> VolatilityState | None:
    regime = snapshot.confirmed_export.regime
    if regime is None:
        return None
    try:
        return VolatilityState(int(regime))
    except (TypeError, ValueError):
        return None


def _lag_horizons(profile: str) -> tuple[int, int]:
    """Return candidate/confirmation measurement windows in native bars.

    These are diagnostic horizons, not engine thresholds. They are derived from the
    canonical profile's existing maturity/confirm/band-confirm windows so a stale
    EARLY event cannot be credited for a candidate 50+ bars later.
    """

    if profile == "Hassas":
        # maturity 3 + confirm 2 + 2-bar allowance; then band confirm 2 + 2.
        return 7, 11
    if profile == "Seçici":
        # maturity 5 + confirm 3 + 2; then band confirm 3 + 3.
        return 10, 16
    if profile != "Dengeli":
        raise ValueError("profile must be Hassas, Dengeli or Seçici")
    # maturity 4 + confirm 2 + 2; then band confirm 2 + 2.
    return 8, 12


def _episode_starts(snapshots: tuple[VolatilityDirectionSnapshot, ...]) -> tuple[int, ...]:
    return tuple(
        i
        for i, snapshot in enumerate(snapshots)
        if snapshot.early.episode_started
        or (
            snapshot.early.state is not EarlyDirectionTransition.NONE
            and snapshot.early.episode_id == 0
        )
    )


def direction_lag_records(replay: VolatilityMTFReplay) -> tuple[DirectionLagRecord, ...]:
    """Measure early-direction lead versus canonical volatility confirmation.

    Matching is episode-bounded and time-bounded. A transition that does not reach
    candidate/confirmed state inside its canonical diagnostic horizon remains an
    unconfirmed/expired early observation; it is never matched to a same-direction
    regime tens of bars later.
    """

    candidate_horizon, confirmation_horizon = _lag_horizons(replay.profile)
    records: list[DirectionLagRecord] = []

    for timeframe in replay.timeframes:
        snapshots = replay.for_timeframe(timeframe).snapshots
        starts = _episode_starts(snapshots)
        for start_position, i in enumerate(starts):
            snapshot = snapshots[i]
            early = snapshot.early.state
            if early is EarlyDirectionTransition.NONE:
                continue

            is_up = early is EarlyDirectionTransition.EARLY_UP
            direction = "UP" if is_up else "DOWN"
            candidate_state = VolatilityState.UP_CANDIDATE if is_up else VolatilityState.DOWN_CANDIDATE
            confirmed_state = VolatilityState.UP_CONFIRMED if is_up else VolatilityState.DOWN_CONFIRMED

            next_start = starts[start_position + 1] if start_position + 1 < len(starts) else None
            horizon_end = min(len(snapshots) - 1, i + confirmation_horizon)
            episode_end = horizon_end if next_start is None else min(horizon_end, next_start - 1)

            candidate_index = None
            confirmed_index = None
            candidate_deadline = min(episode_end, i + candidate_horizon)

            for j in range(i, episode_end + 1):
                state = _volatility_state(snapshots[j])
                if candidate_index is None and j <= candidate_deadline and state is candidate_state:
                    candidate_index = j
                if candidate_index is not None and state is confirmed_state:
                    confirmed_index = j
                    break

            if confirmed_index is not None:
                outcome = "CONFIRMED"
            elif candidate_index is not None:
                outcome = "CANDIDATE_ONLY"
            elif next_start is not None and next_start - 1 <= horizon_end:
                outcome = "SUPERSEDED"
            elif episode_end >= i + confirmation_horizon:
                outcome = "EXPIRED"
            else:
                outcome = "UNCONFIRMED"

            records.append(
                DirectionLagRecord(
                    timeframe=timeframe,
                    direction=direction,
                    early_index=i,
                    candidate_index=candidate_index,
                    confirmed_index=confirmed_index,
                    candidate_lag_bars=None if candidate_index is None else candidate_index - i,
                    confirmed_lag_bars=None if confirmed_index is None else confirmed_index - i,
                    outcome=outcome,
                    window_end_index=episode_end,
                    candidate_horizon_bars=candidate_horizon,
                    confirmation_horizon_bars=confirmation_horizon,
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
        max_bars: int | None = None,
    ) -> VolatilityMTFReplay:
        normalized_symbol = normalize_symbol(symbol)
        requested = tuple(tf.strip().lower() for tf in timeframes)
        unsupported = tuple(tf for tf in requested if tf not in VOLATILITY_TIMEFRAMES)
        if unsupported:
            raise ValueError(f"unsupported volatility timeframe(s): {unsupported!r}")
        if max_bars is not None and max_bars < 1:
            raise ValueError("max_bars must be >= 1 when provided")
        _lag_horizons(profile)  # fail closed on invalid profile before data work

        if input_snapshot is None:
            inputs = load_analysis_inputs(self.store, symbol=normalized_symbol, timeframes=requested)
        else:
            input_snapshot.validate_request(symbol=normalized_symbol, timeframes=requested)
            inputs = input_snapshot

        by_timeframe: dict[str, VolatilityTimeframeReplay] = {}
        for timeframe in requested:
            frame = inputs.for_timeframe(timeframe).input_batch.frame
            if max_bars is not None and len(frame) > max_bars:
                frame = frame.tail(max_bars).copy()
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
            profile=profile,
        )


__all__ = [
    "DirectionLagRecord",
    "VOLATILITY_TIMEFRAMES",
    "VolatilityMTFReplay",
    "VolatilityMTFReplayRunner",
    "VolatilityTimeframeReplay",
    "direction_lag_records",
]
