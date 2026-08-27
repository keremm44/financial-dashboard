from __future__ import annotations

from dataclasses import dataclass
import pickle
from time import perf_counter
from typing import Any

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.analysis_inputs import load_analysis_inputs
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.structure_location_replay import CausalBarClock

from .causal_reducer import CausalTimelineReducer, ReducerCursor
from .history_source import HistoricalDecisionInputConfig, _capture_indices
from .native_domain_runtime import (
    IncrementalNativeDomainRuntime,
    NativeDomainState,
    NativeRuntimeCheckpoint,
    causal_bar_events,
    causal_bar_events_after,
)
from .persistent_state import (
    PersistentCheckpointIdentity,
    PersistentCheckpointRecord,
    PersistentObjectStore,
    build_prefix_fingerprints,
    validate_append_only_prefix,
)
from .state_timeline import CausalStateStore, TimelineFingerprint, build_state_store


_NATIVE_PERSISTENCE_SEMANTIC_VERSION = "native-causal-runtime-checkpoint-v2"


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
    full_state: NativeDomainState | None
    timings: HistoricalNativeTimelineTimings
    all_cutoffs: tuple[Any, ...] | None = None
    state_store_start_position: int = 0

    @property
    def cutoffs(self) -> tuple[Any, ...]:
        return self.state_store.cutoffs if self.all_cutoffs is None else self.all_cutoffs

    @property
    def domain_states(self) -> tuple[NativeDomainState, ...]:
        return tuple(point.state for point in self.state_store.domains)


@dataclass(frozen=True, slots=True)
class _NativeTimelineCheckpointPayload:
    """Small restart payload: continuation state, not the historical read-model timeline."""

    runtime: NativeRuntimeCheckpoint
    cutoffs: tuple[Any, ...]
    full_state: NativeDomainState | None
    fingerprint: TimelineFingerprint


def _config_fingerprint(config: HistoricalDecisionInputConfig) -> str:
    return repr(
        (
            config.decision_timeframe.strip().lower(),
            config.pattern_profile,
            config.max_bars,
            None if config.start_at is None else str(pd.Timestamp(config.start_at)),
            None if config.end_at is None else str(pd.Timestamp(config.end_at)),
        )
    )


def _cutoffs_are_prefix(previous: tuple[Any, ...], current: tuple[Any, ...]) -> bool:
    if len(previous) > len(current):
        return False
    return all(
        pd.Timestamp(left) == pd.Timestamp(right)
        for left, right in zip(previous, current[: len(previous)], strict=True)
    )


class HistoricalNativeTimelineReplayRunner:
    """Persistent append-only producer for the shared native-domain runtime.

    Cold replay computes the historical native-domain timeline once. Persistence then
    stores only the stateful engine continuation checkpoint plus causal watermarks and
    the latest frozen native state. The full historical state store is deliberately not
    duplicated inside the checkpoint: old BUY/SELL inputs live in their own frozen
    decision timeline, while newly appended candles produce only newly appended native
    domain points.

    A checkpoint is accepted only when every consumed OHLCV row remains identical and
    the requested decision cutoffs extend the checkpoint cutoffs. Any mismatch fails
    closed to the canonical cold replay path.
    """

    def __init__(self, store: ParquetOHLCVStore) -> None:
        self.store = store
        self.clock = CausalBarClock()
        self.last_checkpoint_status = "UNSET"

    def _checkpoint_identity(
        self,
        *,
        symbol: str,
        config: HistoricalDecisionInputConfig,
    ) -> PersistentCheckpointIdentity:
        return PersistentCheckpointIdentity(
            namespace="native_timeline",
            symbol=symbol,
            semantic_fingerprint=_NATIVE_PERSISTENCE_SEMANTIC_VERSION,
            config_fingerprint=_config_fingerprint(config),
        )

    def _save_checkpoint(
        self,
        *,
        cache: PersistentObjectStore,
        identity: PersistentCheckpointIdentity,
        inputs,
        reducer: CausalTimelineReducer[NativeDomainState, NativeDomainState],
        runtime: IncrementalNativeDomainRuntime,
        replay: HistoricalNativeTimelineReplay,
    ) -> None:
        try:
            cursor = reducer.cursor
            record = PersistentCheckpointRecord(
                identity=identity,
                prefixes=build_prefix_fingerprints(inputs, watermarks=cursor.watermarks),
                cursor=cursor,
                payload=_NativeTimelineCheckpointPayload(
                    runtime=runtime.export_checkpoint(),
                    cutoffs=tuple(replay.cutoffs),
                    full_state=replay.full_state,
                    fingerprint=replay.state_store.fingerprint,
                ),
            )
            cache.save_checkpoint(record)
            self.last_checkpoint_status = "SAVED"
        except Exception:
            self.last_checkpoint_status = "SAVE_FAILED"

    def replay(
        self,
        symbol: str,
        *,
        config: HistoricalDecisionInputConfig | None = None,
    ) -> HistoricalNativeTimelineReplay:
        cfg = config or HistoricalDecisionInputConfig()
        clean_symbol = normalize_symbol(symbol)
        self.last_checkpoint_status = "MISS"

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

        first_causal_cutoffs = []
        for timeframe in inputs.timeframes:
            frame = inputs.for_timeframe(timeframe).input_batch.frame
            if frame.empty:
                continue
            first_timestamp = pd.Timestamp(frame.iloc[0]["timestamp"])
            first_causal_cutoffs.append(pd.Timestamp(self.clock.available_at(first_timestamp, timeframe)))
        if first_causal_cutoffs:
            common_causal_cutoff = max(first_causal_cutoffs)
            decision_available_at = decision_frame["timestamp"].map(
                lambda value: pd.Timestamp(self.clock.available_at(value, decision_tf))
            )
            decision_frame = decision_frame.loc[decision_available_at >= common_causal_cutoff]

        if cfg.start_at is not None:
            decision_frame = decision_frame.loc[decision_frame["timestamp"] >= pd.Timestamp(cfg.start_at)]
        if cfg.end_at is not None:
            decision_frame = decision_frame.loc[decision_frame["timestamp"] <= pd.Timestamp(cfg.end_at)]
        if cfg.max_bars is not None:
            decision_frame = decision_frame.tail(cfg.max_bars)

        fingerprint = TimelineFingerprint(
            symbol=clean_symbol,
            engine_config="incremental-native-domain-v2",
            clock_version="CausalBarClock-v1",
            pattern_profile=cfg.pattern_profile,
        )
        if decision_frame.empty:
            return HistoricalNativeTimelineReplay(
                symbol=clean_symbol,
                decision_timeframe=decision_tf,
                state_store=CausalStateStore(fingerprint=fingerprint, domains=(), decisions=()),
                full_state=None,
                timings=HistoricalNativeTimelineTimings(load_seconds, 0.0, 0.0),
            )

        cutoffs = tuple(
            pd.Timestamp(self.clock.available_at(value, decision_tf))
            for value in decision_frame["timestamp"]
        )
        _capture_indices(inputs, cutoffs=cutoffs, clock=self.clock)

        cache = PersistentObjectStore(self.store.root)
        checkpoint_identity = self._checkpoint_identity(symbol=clean_symbol, config=cfg)
        checkpoint = cache.load_checkpoint(checkpoint_identity)
        if checkpoint is not None and isinstance(checkpoint.cursor, ReducerCursor):
            payload = checkpoint.payload
            if (
                isinstance(payload, _NativeTimelineCheckpointPayload)
                and payload.fingerprint == fingerprint
                and validate_append_only_prefix(inputs, checkpoint.prefixes)
                and _cutoffs_are_prefix(payload.cutoffs, cutoffs)
            ):
                previous_cutoffs = tuple(payload.cutoffs)
                started = perf_counter()
                new_events = causal_bar_events_after(
                    inputs,
                    watermarks=checkpoint.cursor.watermarks,
                    clock=self.clock,
                )
                event_build_seconds = perf_counter() - started
                new_cutoffs = cutoffs[len(previous_cutoffs) :]

                if not new_events and not new_cutoffs:
                    self.last_checkpoint_status = "HIT_EXACT"
                    return HistoricalNativeTimelineReplay(
                        symbol=clean_symbol,
                        decision_timeframe=decision_tf,
                        state_store=CausalStateStore(
                            fingerprint=fingerprint,
                            domains=(),
                            decisions=(),
                        ),
                        full_state=payload.full_state,
                        timings=HistoricalNativeTimelineTimings(
                            load_inputs_seconds=load_seconds,
                            event_build_seconds=event_build_seconds,
                            native_reduce_seconds=0.0,
                        ),
                        all_cutoffs=cutoffs,
                        state_store_start_position=len(cutoffs),
                    )

                runtime = IncrementalNativeDomainRuntime(
                    inputs,
                    symbol=clean_symbol,
                    clock=self.clock,
                    pattern_profile=cfg.pattern_profile,
                )
                try:
                    runtime.restore_checkpoint(payload.runtime)
                except (TypeError, ValueError, pickle.PickleError, AttributeError):
                    self.last_checkpoint_status = "RESTORE_FAILED"
                else:
                    reducer = CausalTimelineReducer(
                        runtime=runtime,
                        compose_decision=lambda state, _cutoff: state,
                        fingerprint=fingerprint,
                        initial_cursor=checkpoint.cursor,
                    )
                    reducer_cutoffs = tuple(new_cutoffs)
                    appended_full_cutoff = False
                    if new_events:
                        last_event_cutoff = pd.Timestamp(new_events[-1].available_at)
                        if not reducer_cutoffs or last_event_cutoff > reducer_cutoffs[-1]:
                            reducer_cutoffs = (*reducer_cutoffs, last_event_cutoff)
                            appended_full_cutoff = True

                    started = perf_counter()
                    raw_store = reducer.run(events=new_events, cutoffs=reducer_cutoffs)
                    native_reduce_seconds = perf_counter() - started
                    full_state = raw_store.domains[-1].state if raw_store.domains else payload.full_state
                    retained_count = len(new_cutoffs)
                    appended_store = (
                        build_state_store(
                            fingerprint=fingerprint,
                            domain_points=raw_store.domains[:retained_count],
                            decision_points=raw_store.decisions[:retained_count],
                        )
                        if appended_full_cutoff
                        else raw_store
                    )
                    resumed = HistoricalNativeTimelineReplay(
                        symbol=clean_symbol,
                        decision_timeframe=decision_tf,
                        state_store=appended_store,
                        full_state=full_state,
                        timings=HistoricalNativeTimelineTimings(
                            load_inputs_seconds=load_seconds,
                            event_build_seconds=event_build_seconds,
                            native_reduce_seconds=native_reduce_seconds,
                        ),
                        all_cutoffs=cutoffs,
                        state_store_start_position=len(previous_cutoffs),
                    )
                    self.last_checkpoint_status = "HIT_APPEND"
                    self._save_checkpoint(
                        cache=cache,
                        identity=checkpoint_identity,
                        inputs=inputs,
                        reducer=reducer,
                        runtime=runtime,
                        replay=resumed,
                    )
                    if self.last_checkpoint_status == "SAVED":
                        self.last_checkpoint_status = "HIT_APPEND_SAVED"
                    return resumed

        started = perf_counter()
        events = causal_bar_events(inputs, clock=self.clock)
        event_build_seconds = perf_counter() - started
        if not events:
            return HistoricalNativeTimelineReplay(
                symbol=clean_symbol,
                decision_timeframe=decision_tf,
                state_store=CausalStateStore(fingerprint=fingerprint, domains=(), decisions=()),
                full_state=None,
                timings=HistoricalNativeTimelineTimings(load_seconds, event_build_seconds, 0.0),
            )

        runtime = IncrementalNativeDomainRuntime(
            inputs,
            symbol=clean_symbol,
            clock=self.clock,
            pattern_profile=cfg.pattern_profile,
        )
        reducer = CausalTimelineReducer(
            runtime=runtime,
            compose_decision=lambda state, _cutoff: state,
            fingerprint=fingerprint,
        )

        last_event_cutoff = pd.Timestamp(events[-1].available_at)
        reducer_cutoffs = cutoffs
        appended_full_cutoff = last_event_cutoff > cutoffs[-1]
        if appended_full_cutoff:
            reducer_cutoffs = (*cutoffs, last_event_cutoff)

        started = perf_counter()
        raw_store = reducer.run(events=events, cutoffs=reducer_cutoffs)
        native_reduce_seconds = perf_counter() - started
        full_state = raw_store.domains[-1].state

        if appended_full_cutoff:
            state_store = build_state_store(
                fingerprint=fingerprint,
                domain_points=raw_store.domains[: len(cutoffs)],
                decision_points=raw_store.decisions[: len(cutoffs)],
            )
        else:
            state_store = raw_store

        replay = HistoricalNativeTimelineReplay(
            symbol=clean_symbol,
            decision_timeframe=decision_tf,
            state_store=state_store,
            full_state=full_state,
            timings=HistoricalNativeTimelineTimings(
                load_inputs_seconds=load_seconds,
                event_build_seconds=event_build_seconds,
                native_reduce_seconds=native_reduce_seconds,
            ),
            all_cutoffs=cutoffs,
            state_store_start_position=0,
        )
        self._save_checkpoint(
            cache=cache,
            identity=checkpoint_identity,
            inputs=inputs,
            reducer=reducer,
            runtime=runtime,
            replay=replay,
        )
        return replay


__all__ = [
    "HistoricalNativeTimelineReplay",
    "HistoricalNativeTimelineReplayRunner",
    "HistoricalNativeTimelineTimings",
]
