from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Mapping

import pandas as pd

from financial_dashboard.data.analysis_inputs import AnalysisInputSnapshot, TimeframeInputSnapshot
from financial_dashboard.data.engine_input import prepare_engine_input
from financial_dashboard.data.schema import canonicalize_ohlcv
from financial_dashboard.decision.history_source import _stabil_points
from financial_dashboard.decision.native_domain_runtime import (
    IncrementalNativeDomainRuntime,
    causal_bar_events,
    causal_bar_events_after,
)
from financial_dashboard.decision.supporting_replay_runtime import IncrementalSupportingReplayRuntime
from financial_dashboard.structure_location_replay import CausalBarClock


def _semantic(value: Any) -> Any:
    """Normalize replay objects for deterministic causal parity assertions."""
    if isinstance(value, pd.DataFrame):
        return (
            "__DataFrame__",
            tuple(str(column) for column in value.columns),
            tuple(tuple(_semantic(item) for item in row) for row in value.itertuples(index=False, name=None)),
        )
    if isinstance(value, pd.Series):
        return ("__Series__", tuple((str(key), _semantic(item)) for key, item in value.items()))
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float) and math.isnan(value):
        return "__NaN__"
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return tuple((field.name, _semantic(getattr(value, field.name))) for field in fields(value))
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _semantic(item)) for key, item in value.items()))
    if isinstance(value, (tuple, list)):
        return tuple(_semantic(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_semantic(item) for item in value), key=repr))
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return tuple(sorted((str(key), _semantic(item)) for key, item in vars(value).items()))
    slots = getattr(type(value), "__slots__", ())
    if slots and not isinstance(value, type):
        if isinstance(slots, str):
            slots = (slots,)
        return tuple(
            (str(slot), _semantic(getattr(value, slot)))
            for slot in slots
            if hasattr(value, slot)
        )
    return value


def _raw(count: int, *, timeframe: str, freq: str) -> pd.DataFrame:
    timestamps = pd.date_range("2025-01-02 18:10:00+03:00", periods=count, freq=freq)
    rows = []
    for index, timestamp in enumerate(timestamps):
        cycle = ((index % 13) - 6) * 0.22
        trend = index * 0.14
        base = 100.0 + trend + cycle
        close = base + (0.75 if index % 9 in {0, 1} else -0.55 if index % 7 == 0 else 0.18)
        rows.append(
            {
                "timestamp": timestamp,
                "open": base,
                "high": max(base, close) + 1.1 + (index % 5) * 0.12,
                "low": min(base, close) - 0.9 - (index % 4) * 0.10,
                "close": close,
                "volume": 1000.0 + index * 23.0 + (650.0 if index % 17 == 0 else 0.0),
            }
        )
    return canonicalize_ohlcv(
        pd.DataFrame(rows),
        symbol="ASELS",
        timeframe=timeframe,
        source="test",
    )


def _inputs(count: int, *, timeframe: str = "1d", freq: str = "1D") -> AnalysisInputSnapshot:
    raw = _raw(count, timeframe=timeframe, freq=freq)
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


def _multi_inputs() -> AnalysisInputSnapshot:
    rows = {}
    for timeframe, count, freq in (("1h", 8, "1h"), ("30m", 12, "30min")):
        raw = _raw(count, timeframe=timeframe, freq=freq)
        rows[timeframe] = TimeframeInputSnapshot(
            timeframe=timeframe,
            raw_frame=raw,
            input_batch=prepare_engine_input(raw),
        )
    return AnalysisInputSnapshot(
        symbol="ASELS",
        timeframes=("1h", "30m"),
        by_timeframe=MappingProxyType(rows),
        fingerprint=(),
    )


def _native_full(inputs: AnalysisInputSnapshot):
    clock = CausalBarClock()
    runtime = IncrementalNativeDomainRuntime(inputs, symbol="ASELS", clock=clock)
    events = causal_bar_events(inputs, clock=clock)
    for event in events:
        runtime.ingest(event)
    watermarks = {
        tf: len(inputs.for_timeframe(tf).input_batch.frame) - 1
        for tf in inputs.timeframes
    }
    return runtime, runtime.freeze(as_of=events[-1].available_at, watermarks=watermarks)


def test_native_checkpoint_plus_new_bars_equals_causal_full_replay_on_supported_fvg_tf() -> None:
    clock = CausalBarClock()
    full_inputs = _inputs(90)
    prefix_inputs = _inputs(55)

    full_runtime, full = _native_full(full_inputs)

    prefix_runtime = IncrementalNativeDomainRuntime(prefix_inputs, symbol="ASELS", clock=clock)
    for event in causal_bar_events(prefix_inputs, clock=clock):
        prefix_runtime.ingest(event)
    checkpoint = prefix_runtime.export_checkpoint()

    resumed_runtime = IncrementalNativeDomainRuntime(full_inputs, symbol="ASELS", clock=clock)
    resumed_runtime.restore_checkpoint(checkpoint)
    tail = causal_bar_events_after(full_inputs, watermarks={"1d": 54}, clock=clock)
    for event in tail:
        resumed_runtime.ingest(event)
    resumed = resumed_runtime.freeze(
        as_of=causal_bar_events(full_inputs, clock=clock)[-1].available_at,
        watermarks={"1d": 89},
    )

    full_structure = full.structure.replay_for("1d")
    resumed_structure = resumed.structure.replay_for("1d")
    assert resumed_structure.market_structure == full_structure.market_structure
    assert resumed_structure.support_resistance == full_structure.support_resistance
    assert resumed.pattern.pattern_snapshots == full.pattern.pattern_snapshots
    assert resumed.liquidity == full.liquidity
    assert resumed.order_block == full.order_block
    assert full.fvg is not None
    assert resumed.fvg == full.fvg
    assert resumed_runtime.export_checkpoint().timeframes == full_runtime.export_checkpoint().timeframes


def test_native_runtime_advances_only_the_event_timeframe() -> None:
    inputs = _multi_inputs()
    clock = CausalBarClock()
    runtime = IncrementalNativeDomainRuntime(inputs, symbol="ASELS", clock=clock)
    event_30m = next(event for event in causal_bar_events(inputs, clock=clock) if event.timeframe == "30m")

    runtime.ingest(event_30m)

    assert len(runtime._runtimes["30m"].market._rows) == 1
    assert len(runtime._runtimes["1h"].market._rows) == 0
    assert len(runtime._runtimes["30m"].pattern._rows) == 1
    assert len(runtime._runtimes["1h"].pattern._rows) == 0


def test_same_native_watermarks_produce_no_new_events() -> None:
    inputs = _inputs(70)
    assert causal_bar_events_after(inputs, watermarks={"1d": 69}) == ()


def test_supporting_checkpoint_exact_warm_run_advances_zero_rows() -> None:
    inputs = _inputs(70)
    _, structure_state = _native_full(inputs)

    initial = IncrementalSupportingReplayRuntime(inputs, symbol="ASELS")
    initial.advance()
    frozen = initial.freeze(structure_replay=structure_state.structure)
    checkpoint = initial.export_checkpoint()

    restored = IncrementalSupportingReplayRuntime(inputs, symbol="ASELS")
    restored.restore_checkpoint(checkpoint)
    before = restored.watermarks
    restored.advance()
    after = restored.watermarks
    resumed = restored.freeze(structure_replay=structure_state.structure)

    assert before == after == {"1d": 69}
    assert restored.last_timings.ham_seconds >= 0.0
    assert restored.last_timings.volume_seconds >= 0.0
    assert restored.last_timings.volatility_seconds >= 0.0
    assert resumed.ham.replay_for("1d").history == frozen.ham.replay_for("1d").history
    assert resumed.volume.replay_for("1d").history == frozen.volume.replay_for("1d").history
    assert _semantic(resumed.volatility.for_timeframe("1d").snapshots) == _semantic(
        frozen.volatility.for_timeframe("1d").snapshots
    )


def test_stabil_frozen_point_is_causal_prefix_invariant() -> None:
    full_inputs = _inputs(120)
    prefix_inputs = _inputs(80)

    full_points = _stabil_points(full_inputs, indices_1d=(79, 119))
    prefix_point = _stabil_points(prefix_inputs, indices_1d=(79,))[79]

    assert _semantic(full_points[79]) == _semantic(prefix_point)
    assert 119 in full_points
