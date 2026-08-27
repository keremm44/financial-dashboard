from __future__ import annotations

from types import MappingProxyType

import pandas as pd

from financial_dashboard.data.analysis_inputs import AnalysisInputSnapshot, TimeframeInputSnapshot
from financial_dashboard.data.engine_input import prepare_engine_input
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.data.schema import canonicalize_ohlcv
from financial_dashboard.decision.native_domain_runtime import (
    IncrementalNativeDomainRuntime,
    causal_bar_events,
)
from financial_dashboard.decision.supporting_replay_runtime import IncrementalSupportingReplayRuntime
from financial_dashboard.ham_mtf_replay import HamMTFEvidenceReplayRunner
from financial_dashboard.structure_location_replay import CausalBarClock
from financial_dashboard.volume_mtf_replay import VolumeMTFEvidenceReplayRunner
from financial_dashboard.volatility_mtf_replay import VolatilityMTFReplayRunner


def _inputs(count: int = 60) -> AnalysisInputSnapshot:
    timestamps = pd.date_range("2026-01-02 18:10:00+03:00", periods=count, freq="1D")
    rows = []
    for index, timestamp in enumerate(timestamps):
        drift = index * 0.35
        pulse = 1.25 if index % 7 in {0, 1} else -0.45 if index % 5 == 0 else 0.20
        base = 100.0 + drift
        close = base + pulse
        rows.append(
            {
                "timestamp": timestamp,
                "open": base,
                "high": max(base, close) + 1.4 + (index % 3) * 0.1,
                "low": min(base, close) - 1.1 - (index % 4) * 0.1,
                "close": close,
                "volume": 1000.0 + index * 31.0 + (500.0 if index % 11 == 0 else 0.0),
            }
        )
    raw = canonicalize_ohlcv(
        pd.DataFrame(rows),
        symbol="ASELS",
        timeframe="1d",
        source="test",
    )
    snapshot = TimeframeInputSnapshot(
        timeframe="1d",
        raw_frame=raw,
        input_batch=prepare_engine_input(raw),
    )
    return AnalysisInputSnapshot(
        symbol="ASELS",
        timeframes=("1d",),
        by_timeframe=MappingProxyType({"1d": snapshot}),
        fingerprint=(),
    )


def _structure(inputs: AnalysisInputSnapshot):
    clock = CausalBarClock()
    runtime = IncrementalNativeDomainRuntime(inputs, symbol="ASELS", clock=clock)
    for event in causal_bar_events(inputs, clock=clock):
        runtime.ingest(event)
    index = len(inputs.for_timeframe("1d").input_batch.frame) - 1
    as_of = inputs.for_timeframe("1d").input_batch.frame.iloc[-1]["timestamp"]
    return runtime.freeze(as_of=as_of, watermarks={"1d": index}).structure


def test_supporting_incremental_runtime_matches_existing_canonical_runners(tmp_path):
    inputs = _inputs()
    structure = _structure(inputs)
    store = ParquetOHLCVStore(tmp_path)

    incremental_runtime = IncrementalSupportingReplayRuntime(
        inputs,
        symbol="ASELS",
        clock=CausalBarClock(),
    )
    incremental_runtime.advance()
    incremental = incremental_runtime.freeze(structure_replay=structure)

    canonical_ham = HamMTFEvidenceReplayRunner(store).replay(
        "ASELS",
        timeframes=("1d",),
        input_snapshot=inputs,
    )
    canonical_volume = VolumeMTFEvidenceReplayRunner(store).replay(
        "ASELS",
        timeframes=("1d",),
        structure_replay=structure,
        input_snapshot=inputs,
    )
    canonical_volatility = VolatilityMTFReplayRunner(store).replay(
        "ASELS",
        input_snapshot=inputs,
        timeframes=("1d",),
    )

    assert incremental.ham.replay_for("1d").history == canonical_ham.replay_for("1d").history

    incremental_volume = incremental.volume.replay_for("1d")
    canonical_volume_tf = canonical_volume.replay_for("1d")
    assert incremental_volume.history == canonical_volume_tf.history
    assert incremental_volume.latest == canonical_volume_tf.latest
    assert incremental_volume.event_links == canonical_volume_tf.event_links
    assert (
        incremental_volume.participation_without_structure
        == canonical_volume_tf.participation_without_structure
    )
    assert incremental.volume.round2 == canonical_volume.round2

    assert (
        incremental.volatility.for_timeframe("1d").snapshots
        == canonical_volatility.for_timeframe("1d").snapshots
    )
