from __future__ import annotations

from types import MappingProxyType
from typing import Any

import pandas as pd

from financial_dashboard.data.analysis_inputs import (
    AnalysisInputSnapshot,
    TimeframeInputSnapshot,
)
from financial_dashboard.data.engine_input import prepare_engine_input
from financial_dashboard.structure_location_replay import CausalBarClock


class CausalInputUnavailableError(ValueError):
    """Raised when a causal cutoff leaves too little data for a requested replay."""


def clip_analysis_inputs_at_cutoff(
    inputs: AnalysisInputSnapshot,
    *,
    cutoff: Any,
    clock: CausalBarClock | None = None,
    minimum_bars_per_timeframe: int = 1,
) -> AnalysisInputSnapshot:
    """Return an immutable analysis snapshot containing only causally available bars.

    Engines must still be rerun when the cutoff removes future bars. When a timeframe
    is already entirely causal at the requested cutoff, reuse its immutable prepared
    snapshot instead of copying and validating the same frame again.
    """

    if minimum_bars_per_timeframe < 1:
        raise ValueError("minimum_bars_per_timeframe must be >= 1")

    clock = clock or CausalBarClock()
    cutoff_ts = pd.Timestamp(cutoff)
    snapshots: dict[str, TimeframeInputSnapshot] = {}

    for timeframe in inputs.timeframes:
        source_snapshot = inputs.for_timeframe(timeframe)
        source = source_snapshot.input_batch.frame
        available_mask = [
            pd.Timestamp(clock.available_at(timestamp, timeframe)) <= cutoff_ts
            for timestamp in source["timestamp"]
        ]
        causal_count = sum(available_mask)
        if causal_count < minimum_bars_per_timeframe:
            raise CausalInputUnavailableError(
                f"{timeframe} has {causal_count} causal bars at {cutoff_ts}; "
                f"requires {minimum_bars_per_timeframe}"
            )

        if causal_count == len(source):
            snapshots[timeframe] = source_snapshot
            continue

        clipped = source.loc[available_mask].copy().reset_index(drop=True)
        batch = prepare_engine_input(clipped)
        snapshots[timeframe] = TimeframeInputSnapshot(
            timeframe=timeframe,
            raw_frame=clipped.copy(),
            input_batch=batch,
        )

    return AnalysisInputSnapshot(
        symbol=inputs.symbol,
        timeframes=inputs.timeframes,
        by_timeframe=MappingProxyType(snapshots),
        fingerprint=inputs.fingerprint,
    )


__all__ = ["CausalInputUnavailableError", "clip_analysis_inputs_at_cutoff"]
