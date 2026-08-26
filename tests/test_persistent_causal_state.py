from __future__ import annotations

from types import MappingProxyType

import pandas as pd

from financial_dashboard.data.analysis_inputs import AnalysisInputSnapshot, TimeframeInputSnapshot
from financial_dashboard.data.engine_input import prepare_engine_input
from financial_dashboard.data.schema import canonicalize_ohlcv
from financial_dashboard.decision.causal_reducer import CausalBarEvent, CausalTimelineReducer, ReducerCursor
from financial_dashboard.decision.native_domain_runtime import (
    IncrementalNativeDomainRuntime,
    causal_bar_events,
    causal_bar_events_after,
)
from financial_dashboard.decision.persistent_state import (
    PersistentCacheIdentity,
    PersistentCheckpointIdentity,
    PersistentCheckpointRecord,
    PersistentObjectStore,
    build_prefix_fingerprints,
    validate_append_only_prefix,
)
from financial_dashboard.decision.state_timeline import TimelineFingerprint
from financial_dashboard.decision.supporting_replay_runtime import IncrementalSupportingReplayRuntime
from financial_dashboard.structure_location_replay import CausalBarClock


def _frame(
    count: int,
    *,
    start: str = "2026-01-02 10:00:00+03:00",
    timeframe: str = "1h",
    freq: str = "1h",
) -> pd.DataFrame:
    timestamps = pd.date_range(start, periods=count, freq=freq)
    rows = []
    for index, timestamp in enumerate(timestamps):
        base = 100.0 + index * 0.25
        rows.append(
            {
                "timestamp": timestamp,
                "open": base,
                "high": base + 1.0,
                "low": base - 0.75,
                "close": base + (0.3 if index % 3 else -0.1),
                "volume": 1000.0 + index * 10.0,
            }
        )
    return canonicalize_ohlcv(
        pd.DataFrame(rows),
        symbol="ASELS",
        timeframe=timeframe,
        source="test",
    )


def _inputs(
    count: int,
    *,
    timeframe: str = "1h",
    freq: str = "1h",
) -> AnalysisInputSnapshot:
    raw = _frame(count, timeframe=timeframe, freq=freq)
    snapshot = TimeframeInputSnapshot(
        timeframe=timeframe,
        raw_frame=raw,
        input_batch=prepare_engine_input(raw),
    )
    return AnalysisInputSnapshot(
        symbol="ASELS",
        timeframes=(timeframe,),
        by_timeframe=MappingProxyType({timeframe: snapshot}),
        fingerprint=(),
    )


def test_persistent_object_store_is_exact_identity_and_atomic(tmp_path):
    store = PersistentObjectStore(tmp_path)
    identity = PersistentCacheIdentity(
        namespace="decision",
        symbol="ASELS",
        semantic_fingerprint="v1",
        config_fingerprint="cfg",
        source_fingerprint=(("1h", 10, 20),),
    )
    assert store.load(identity) is None
    store.save(identity, {"value": 7})
    assert store.load(identity) == {"value": 7}

    changed = PersistentCacheIdentity(
        namespace="decision",
        symbol="ASELS",
        semantic_fingerprint="v2",
        config_fingerprint="cfg",
        source_fingerprint=(("1h", 10, 20),),
    )
    assert store.load(changed) is None


def test_append_only_prefix_accepts_new_rows_but_rejects_history_edits():
    prefix = _inputs(20)
    expected = build_prefix_fingerprints(prefix, watermarks={"1h": 19})

    extended = _inputs(25)
    assert validate_append_only_prefix(extended, expected)

    edited_raw = _frame(25)
    edited_raw.loc[5, "close"] += 9.0
    edited = AnalysisInputSnapshot(
        symbol="ASELS",
        timeframes=("1h",),
        by_timeframe=MappingProxyType(
            {
                "1h": TimeframeInputSnapshot(
                    timeframe="1h",
                    raw_frame=edited_raw,
                    input_batch=prepare_engine_input(edited_raw),
                )
            }
        ),
        fingerprint=(),
    )
    assert not validate_append_only_prefix(edited, expected)


def test_checkpoint_record_roundtrip(tmp_path):
    store = PersistentObjectStore(tmp_path)
    inputs = _inputs(8)
    identity = PersistentCheckpointIdentity(
        namespace="native",
        symbol="ASELS",
        semantic_fingerprint="v1",
        config_fingerprint="cfg",
    )
    record = PersistentCheckpointRecord(
        identity=identity,
        prefixes=build_prefix_fingerprints(inputs, watermarks={"1h": 7}),
        cursor=ReducerCursor(watermarks={"1h": 7}, last_event_key=(7, "1h", 7)),
        payload={"ok": True},
    )
    store.save_checkpoint(record)
    assert store.load_checkpoint(identity) == record


class _FakeRuntime:
    def __init__(self, values=()):
        self.values = list(values)

    def ingest(self, event: CausalBarEvent) -> None:
        self.values.append(event.bar_index)

    def freeze(self, *, as_of, watermarks):
        return tuple(self.values)


def test_reducer_resume_cursor_matches_uninterrupted_sequence():
    fingerprint = TimelineFingerprint(
        symbol="ASELS",
        engine_config="test",
        clock_version="test",
    )
    events = tuple(
        CausalBarEvent(
            available_at=pd.Timestamp("2026-01-02 10:00:00+03:00") + pd.Timedelta(hours=index),
            timeframe="1h",
            bar_index=index,
            bar={"timestamp": index},
        )
        for index in range(5)
    )

    uninterrupted_runtime = _FakeRuntime()
    uninterrupted = CausalTimelineReducer(
        runtime=uninterrupted_runtime,
        compose_decision=lambda state, _as_of: state,
        fingerprint=fingerprint,
    )
    uninterrupted.run(events=events, cutoffs=(events[-1].available_at,))

    prefix_runtime = _FakeRuntime()
    prefix = CausalTimelineReducer(
        runtime=prefix_runtime,
        compose_decision=lambda state, _as_of: state,
        fingerprint=fingerprint,
    )
    prefix.run(events=events[:3], cutoffs=(events[2].available_at,))

    resumed_runtime = _FakeRuntime(prefix_runtime.values)
    resumed = CausalTimelineReducer(
        runtime=resumed_runtime,
        compose_decision=lambda state, _as_of: state,
        fingerprint=fingerprint,
        initial_cursor=prefix.cursor,
    )
    resumed.run(events=events[3:], cutoffs=(events[-1].available_at,))

    assert resumed_runtime.values == uninterrupted_runtime.values
    assert resumed.cursor.watermarks == uninterrupted.cursor.watermarks
    assert resumed.cursor.last_event_key == uninterrupted.cursor.last_event_key


def _native_full_state(inputs: AnalysisInputSnapshot):
    clock = CausalBarClock()
    runtime = IncrementalNativeDomainRuntime(inputs, symbol="ASELS", clock=clock)
    for event in causal_bar_events(inputs, clock=clock):
        runtime.ingest(event)
    watermarks = {
        timeframe: len(inputs.for_timeframe(timeframe).input_batch.frame) - 1
        for timeframe in inputs.timeframes
    }
    final_timeframe = inputs.timeframes[-1]
    final_timestamp = inputs.for_timeframe(final_timeframe).input_batch.frame.iloc[-1]["timestamp"]
    return runtime.freeze(as_of=final_timestamp, watermarks=watermarks)


def test_native_engine_checkpoint_restore_matches_full_replay():
    clock = CausalBarClock()
    full_inputs = _inputs(40)
    prefix_inputs = _inputs(25)

    full_runtime = IncrementalNativeDomainRuntime(
        full_inputs,
        symbol="ASELS",
        clock=clock,
    )
    for event in causal_bar_events(full_inputs, clock=clock):
        full_runtime.ingest(event)
    full_state = full_runtime.freeze(as_of=_frame(40).iloc[-1]["timestamp"], watermarks={"1h": 39})

    prefix_runtime = IncrementalNativeDomainRuntime(
        prefix_inputs,
        symbol="ASELS",
        clock=clock,
    )
    for event in causal_bar_events(prefix_inputs, clock=clock):
        prefix_runtime.ingest(event)
    checkpoint = prefix_runtime.export_checkpoint()

    resumed_runtime = IncrementalNativeDomainRuntime(
        full_inputs,
        symbol="ASELS",
        clock=clock,
    )
    resumed_runtime.restore_checkpoint(checkpoint)
    for event in causal_bar_events_after(full_inputs, watermarks={"1h": 24}, clock=clock):
        resumed_runtime.ingest(event)
    resumed_state = resumed_runtime.freeze(
        as_of=_frame(40).iloc[-1]["timestamp"],
        watermarks={"1h": 39},
    )

    full_structure = full_state.structure.replay_for("1h")
    resumed_structure = resumed_state.structure.replay_for("1h")
    assert resumed_structure.market_structure == full_structure.market_structure
    assert resumed_structure.support_resistance == full_structure.support_resistance
    assert resumed_state.pattern.pattern_snapshots == full_state.pattern.pattern_snapshots
    assert resumed_state.liquidity == full_state.liquidity
    assert resumed_state.order_block == full_state.order_block
    assert resumed_state.fvg == full_state.fvg


def test_supporting_checkpoint_restore_matches_full_replay():
    full_inputs = _inputs(45, timeframe="1d", freq="1D")
    prefix_inputs = _inputs(30, timeframe="1d", freq="1D")
    structure = _native_full_state(full_inputs).structure

    full_runtime = IncrementalSupportingReplayRuntime(full_inputs, symbol="ASELS")
    full_runtime.advance()
    full = full_runtime.freeze(structure_replay=structure)

    prefix_runtime = IncrementalSupportingReplayRuntime(prefix_inputs, symbol="ASELS")
    prefix_runtime.advance()
    checkpoint = prefix_runtime.export_checkpoint()

    resumed_runtime = IncrementalSupportingReplayRuntime(full_inputs, symbol="ASELS")
    resumed_runtime.restore_checkpoint(checkpoint)
    resumed_runtime.advance()
    resumed = resumed_runtime.freeze(structure_replay=structure)

    assert resumed_runtime.watermarks == full_runtime.watermarks
    assert resumed.ham.replay_for("1d").history == full.ham.replay_for("1d").history
    resumed_volume = resumed.volume.replay_for("1d")
    full_volume = full.volume.replay_for("1d")
    assert resumed_volume.history == full_volume.history
    assert resumed_volume.event_links == full_volume.event_links
    assert (
        resumed_volume.participation_without_structure
        == full_volume.participation_without_structure
    )
    assert resumed.volume.round2 == full.volume.round2
    assert resumed.volatility.for_timeframe("1d").snapshots == full.volatility.for_timeframe("1d").snapshots
