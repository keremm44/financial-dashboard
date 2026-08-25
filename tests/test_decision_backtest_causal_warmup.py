from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.structure_location_replay import CausalBarClock


_SPEC = importlib.util.spec_from_file_location(
    "decision_backtest_script",
    Path("scripts/decision_backtest.py"),
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class _Store:
    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames

    def load(self, symbol: str, timeframe: str) -> pd.DataFrame:
        del symbol
        return self.frames[timeframe].copy()


def test_causal_warmup_skips_1h_bars_before_all_timeframes_are_available():
    tz = "Europe/Istanbul"
    decision_times = pd.date_range("2026-01-02 09:00", periods=12, freq="1h", tz=tz)
    frames: dict[str, pd.DataFrame] = {}
    for timeframe in ANALYSIS_TIMEFRAMES:
        if timeframe == "1h":
            timestamps = decision_times
        else:
            timestamps = pd.DatetimeIndex([pd.Timestamp("2026-01-02 09:00", tz=tz)])
        frames[timeframe] = pd.DataFrame({"timestamp": timestamps})

    store = _Store(frames)
    clock = CausalBarClock()
    first_common_cutoff = max(
        pd.Timestamp(clock.available_at(frames[tf].iloc[0]["timestamp"], tf))
        for tf in ANALYSIS_TIMEFRAMES
    )
    expected = next(
        timestamp
        for timestamp in decision_times
        if pd.Timestamp(clock.available_at(timestamp, "1h")) >= first_common_cutoff
    )

    actual = _MODULE._causal_warmup_start(
        store,
        symbol="ASELS",
        requested_start=None,
    )

    assert actual == expected
    assert actual > decision_times[0]


def test_requested_start_cannot_move_before_causal_warmup():
    tz = "Europe/Istanbul"
    decision_times = pd.date_range("2026-01-02 09:00", periods=12, freq="1h", tz=tz)
    frames = {
        timeframe: pd.DataFrame(
            {
                "timestamp": (
                    decision_times
                    if timeframe == "1h"
                    else pd.DatetimeIndex([pd.Timestamp("2026-01-02 09:00", tz=tz)])
                )
            }
        )
        for timeframe in ANALYSIS_TIMEFRAMES
    }
    store = _Store(frames)

    warmup = _MODULE._causal_warmup_start(
        store,
        symbol="ASELS",
        requested_start=None,
    )
    actual = _MODULE._causal_warmup_start(
        store,
        symbol="ASELS",
        requested_start="2026-01-02 09:00",
    )

    assert actual == warmup
