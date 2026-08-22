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

    This is deliberately stricter than filtering target evidence after an engine has
    replayed the full cache. Engines are rerun on the clipped prefix so a future bar
    cannot consume, invalidate, promote, or otherwise rewrite the state that was
    knowable at ``cutoff``.
    """

    if minimum_bars_per_timeframe < 1:
        raise ValueError("minimum_bars_per_timeframe must be >= 1")

    clock = clock or CausalBarClock()
    cutoff_ts = pd.Timestamp(cutoff)
    snapshots: dict[str, TimeframeInputSnapshot] = {}

    for timeframe in inputs.timeframes:
        source = inputs.for_timeframe(timeframe).input_batch.frame
        available_mask = [
            pd.Timestamp(clock.available_at(timestamp, timeframe)) <= cutoff_ts
            for timestamp in source["timestamp"]
        ]
        clipped = source.loc[available_mask].copy().reset_index(drop=True)
        if len(clipped) < minimum_bars_per_timeframe:
            raise CausalInputUnavailableError(
                f"{timeframe} has {len(clipped)} causal bars at {cutoff_ts}; "
                f"requires {minimum_bars_per_timeframe}"
            )
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
