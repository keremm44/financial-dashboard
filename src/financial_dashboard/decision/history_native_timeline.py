from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.analysis_inputs import load_analysis_inputs
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.structure_location_replay import CausalBarClock

from .causal_reducer import CausalTimelineReducer
from .history_source import HistoricalDecisionInputConfig, _capture_indices
from .native_domain_runtime import (
    IncrementalNativeDomainRuntime,
    NativeDomainState,
    causal_bar_events,
)
from .state_timeline import CausalStateStore, TimelineFingerprint


@dataclass(frozen=True, slots=True)
class HistoricalNativeTimelineTimings:
    load_inputs_seconds: float
    event_build_seconds: float
    native_reduce_seconds: float

    @property
    def total_seconds(self) -> float:
        return self.load_inputs_seconds + self.event_build_seconds + self.native_reduce_seconds


@dataclass(frozen=True, slots=True)
class HistoricalNativeTimelineReplay:
    symbol: str
    decision_timeframe: str
    state_store: CausalStateStore[NativeDomainState, NativeDomainState]
    timings: HistoricalNativeTimelineTimings

    @property
    def cutoffs(self) -> tuple[Any, ...]:
        return self.state_store.cutoffs

    @property
    def domain_states(self) -> tuple[NativeDomainState, ...]:
        return tuple(point.state for point in self.state_store.domains)


class HistoricalNativeTimelineReplayRunner:
    """Cold historical producer for the shared incremental native-domain runtime."""

    def __init__(self, store: ParquetOHLCVStore) -> None:
        self.store = store
        self.clock = CausalBarClock()

    def replay(
        self,
        symbol: str,
        *,
        config: HistoricalDecisionInputConfig | None = None,
    ) -> HistoricalNativeTimelineReplay:
        cfg = config or HistoricalDecisionInputConfig()
        clean_symbol = normalize_symbol(symbol)

        started = perf_counter()
        inputs = load_analysis_inputs(
            self.store,
            symbol=clean_symbol,
            timeframes=ANALYSIS_TIMEFRAMES,
        )
        load_seconds = perf_counter() - started

        decision_tf = cfg.decision_timeframe.strip().lower()
        decision_frame = inputs.for_timeframe(decision_tf).input_batch.frame.copy()
        decision_frame["timestamp"] = pd.to_datetime(decision_frame["timestamp"], errors="raise")
        if cfg.start_at is not None:
            decision_frame = decision_frame.loc[decision_frame["timestamp"] >= pd.Timestamp(cfg.start_at)]
        if cfg.end_at is not None:
            decision_frame = decision_frame.loc[decision_frame["timestamp"] <= pd.Timestamp(cfg.end_at)]
        if cfg.max_bars is not None:
            decision_frame = decision_frame.tail(cfg.max_bars)
        if decision_frame.empty:
            fingerprint = TimelineFingerprint(
                symbol=clean_symbol,
                engine_config="incremental-native-domain-v1",
                clock_version="CausalBarClock-v1",
                pattern_profile=cfg.pattern_profile,
            )
            return HistoricalNativeTimelineReplay(
                symbol=clean_symbol,
                decision_timeframe=decision_tf,
                state_store=CausalStateStore(fingerprint=fingerprint, domains=(), decisions=()),
                timings=HistoricalNativeTimelineTimings(load_seconds, 0.0, 0.0),
            )

        cutoffs = tuple(
            pd.Timestamp(self.clock.available_at(value, decision_tf))
            for value in decision_frame["timestamp"]
        )
        # Preserve the old source's warm-up/no-lookahead contract. This raises before
        # the reducer starts if any requested cutoff precedes a causal MTF prefix.
        _capture_indices(inputs, cutoffs=cutoffs, clock=self.clock)

        started = perf_counter()
        events = causal_bar_events(inputs, clock=self.clock)
        event_build_seconds = perf_counter() - started

        runtime = IncrementalNativeDomainRuntime(
            inputs,
            symbol=clean_symbol,
            clock=self.clock,
            pattern_profile=cfg.pattern_profile,
        )
        fingerprint = TimelineFingerprint(
            symbol=clean_symbol,
            engine_config="incremental-native-domain-v1",
            clock_version="CausalBarClock-v1",
            pattern_profile=cfg.pattern_profile,
        )
        reducer = CausalTimelineReducer(
            runtime=runtime,
            compose_decision=lambda state, _cutoff: state,
            fingerprint=fingerprint,
        )

        started = perf_counter()
        state_store = reducer.run(events=events, cutoffs=cutoffs)
        native_reduce_seconds = perf_counter() - started

        return HistoricalNativeTimelineReplay(
            symbol=clean_symbol,
            decision_timeframe=decision_tf,
            state_store=state_store,
            timings=HistoricalNativeTimelineTimings(
                load_inputs_seconds=load_seconds,
                event_build_seconds=event_build_seconds,
                native_reduce_seconds=native_reduce_seconds,
            ),
        )


__all__ = [
    "HistoricalNativeTimelineReplay",
    "HistoricalNativeTimelineReplayRunner",
    "HistoricalNativeTimelineTimings",
]
