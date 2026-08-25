from pathlib import Path

import pandas as pd

from financial_dashboard.data.analysis_inputs import AnalysisInputSnapshot, TimeframeInputSnapshot
from financial_dashboard.data.engine_input import prepare_engine_input
from financial_dashboard.decision.history_source import (
    _capture_indices,
    _wilder_atr_history,
)
from financial_dashboard.structure_location_replay import CausalBarClock


def _snapshot() -> AnalysisInputSnapshot:
    rows = {}
    for timeframe, stamps in {
        "1d": ["2026-01-02 18:00", "2026-01-05 18:00"],
        "4h": ["2026-01-05 10:00", "2026-01-05 14:00"],
        "2h": ["2026-01-05 10:00", "2026-01-05 12:00", "2026-01-05 14:00"],
        "1h": ["2026-01-05 10:00", "2026-01-05 11:00", "2026-01-05 12:00"],
        "30m": ["2026-01-05 10:00", "2026-01-05 10:30", "2026-01-05 11:00", "2026-01-05 11:30"],
    }.items():
        ts = pd.to_datetime(stamps)
        frame = pd.DataFrame(
            {
                "timestamp": ts,
                "open": [10.0 + i for i in range(len(ts))],
                "high": [11.0 + i for i in range(len(ts))],
                "low": [9.0 + i for i in range(len(ts))],
                "close": [10.5 + i for i in range(len(ts))],
                "volume": [100.0] * len(ts),
                "is_closed": [True] * len(ts),
                "is_complete": [True] * len(ts),
            }
        )
        rows[timeframe] = TimeframeInputSnapshot(
            timeframe=timeframe,
            raw_frame=frame,
            input_batch=prepare_engine_input(frame),
        )
    return AnalysisInputSnapshot(
        symbol="TEST",
        timeframes=("1d", "4h", "2h", "1h", "30m"),
        by_timeframe=rows,
        fingerprint=(),
    )


def test_capture_indices_use_causal_availability_not_raw_bar_label():
    inputs = _snapshot()
    clock = CausalBarClock()
    cutoffs = (
        pd.Timestamp(clock.available_at(pd.Timestamp("2026-01-05 11:00"), "1h")),
        pd.Timestamp(clock.available_at(pd.Timestamp("2026-01-05 12:00"), "1h")),
    )

    indices = _capture_indices(inputs, cutoffs=cutoffs, clock=clock)

    assert indices["1h"] == (1, 2)
    assert indices["30m"] == (3, 3)
    # Daily cache is close-labelled. The Jan 5 daily bar at 18:00 is not known at
    # the noon/13:00 decision cutoffs, so the last causal daily bar remains Jan 2.
    assert indices["1d"] == (0, 0)


def test_wilder_atr_history_is_prefix_only_and_length_preserving():
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=4, freq="h"),
            "open": [10.0, 10.5, 11.0, 11.5],
            "high": [11.0, 12.0, 12.5, 13.0],
            "low": [9.0, 10.0, 10.5, 11.0],
            "close": [10.5, 11.0, 12.0, 12.5],
        }
    )
    first = _wilder_atr_history(frame.iloc[:3], length=2)
    extended = _wilder_atr_history(frame, length=2)

    assert len(extended) == 4
    assert first == extended[:3]
    assert all(value > 0 for value in extended)


def test_history_source_does_not_import_full_workspace_runner():
    source = Path("src/financial_dashboard/decision/history_source.py").read_text(encoding="utf-8")
    assert "MarketAnalysisWorkspaceRunner" not in source
    assert "market_workspace" not in source
