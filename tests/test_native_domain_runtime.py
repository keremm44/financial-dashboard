from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from financial_dashboard.decision.native_domain_runtime import causal_bar_events


class _Inputs:
    symbol = "TEST"
    timeframes = ("1h", "30m")

    def __init__(self) -> None:
        self._frames = {
            "1h": pd.DataFrame(
                [
                    {"timestamp": pd.Timestamp("2026-01-01 10:00", tz="Europe/Istanbul"), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
                    {"timestamp": pd.Timestamp("2026-01-01 11:00", tz="Europe/Istanbul"), "open": 2, "high": 2, "low": 2, "close": 2, "volume": 1},
                ]
            ),
            "30m": pd.DataFrame(
                [
                    {"timestamp": pd.Timestamp("2026-01-01 10:00", tz="Europe/Istanbul"), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
                    {"timestamp": pd.Timestamp("2026-01-01 10:30", tz="Europe/Istanbul"), "open": 2, "high": 2, "low": 2, "close": 2, "volume": 1},
                ]
            ),
        }

    def for_timeframe(self, timeframe: str):
        return SimpleNamespace(input_batch=SimpleNamespace(frame=self._frames[timeframe]))


def test_causal_bar_events_are_deterministically_sorted() -> None:
    events = causal_bar_events(_Inputs())

    assert tuple((event.timeframe, event.bar_index) for event in events) == tuple(
        sorted(
            ((event.timeframe, event.bar_index) for event in events),
            key=lambda pair: next(
                item.sort_key
                for item in events
                if (item.timeframe, item.bar_index) == pair
            ),
        )
    )
    assert len(events) == 4
    assert all(events[index].sort_key < events[index + 1].sort_key for index in range(3))
