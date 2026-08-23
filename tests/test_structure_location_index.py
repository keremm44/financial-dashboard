from __future__ import annotations

import pandas as pd

from financial_dashboard.engines.structure_location import CausalZoneObservation
from financial_dashboard.structure_location_replay import (
    _latest_causal_observation,
    _observation_available_ns,
)


def _timeline() -> tuple[CausalZoneObservation, ...]:
    return tuple(
        CausalZoneObservation(
            symbol="THYAO",
            timeframe="1h",
            bar_index=index,
            observed_at=pd.Timestamp("2026-01-01T10:00:00+03:00")
            + pd.Timedelta(hours=index),
            available_at=pd.Timestamp("2026-01-01T11:00:00+03:00")
            + pd.Timedelta(hours=index),
            zones=(),
        )
        for index in range(8)
    )


def test_indexed_causal_lookup_matches_linear_reference_at_boundaries() -> None:
    timeline = _timeline()
    available_ns = _observation_available_ns(timeline)
    cutoffs = (
        pd.Timestamp("2026-01-01T10:59:59+03:00"),
        pd.Timestamp("2026-01-01T11:00:00+03:00"),
        pd.Timestamp("2026-01-01T13:30:00+03:00"),
        pd.Timestamp("2026-01-01T18:00:00+03:00"),
        pd.Timestamp("2026-01-02T00:00:00+03:00"),
    )

    for cutoff in cutoffs:
        linear = next(
            (
                observation
                for observation in reversed(timeline)
                if observation.available_at <= cutoff
            ),
            None,
        )
        indexed = _latest_causal_observation(timeline, available_ns, cutoff)
        assert indexed == linear
