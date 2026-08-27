from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from financial_dashboard.decision.indexed_views import IndexedVolumeView
from financial_dashboard.decision.history_source import _volume_view


def _link(hour: int, name: str):
    return SimpleNamespace(
        assessed_at=pd.Timestamp(f"2026-01-01 {hour:02d}:00", tz="Europe/Istanbul"),
        name=name,
    )


def _full(links):
    replay = SimpleNamespace(
        timeframe="1h",
        history=("h0", "h1", "h2"),
        event_links=tuple(links),
    )
    return SimpleNamespace(
        symbol="TEST",
        timeframes=("1h",),
        timeframe_replays=(replay,),
    )


def test_indexed_volume_view_matches_canonical_monotonic_links() -> None:
    full = _full((_link(10, "a"), _link(11, "b"), _link(12, "c")))
    cutoff = pd.Timestamp("2026-01-01 11:00", tz="Europe/Istanbul")

    assert IndexedVolumeView(full).at({"1h": 1}, cutoff).timeframe_replays[0].event_links == _volume_view(
        full,
        {"1h": 1},
        cutoff,
    ).timeframe_replays[0].event_links


def test_indexed_volume_view_falls_back_without_reordering_non_monotonic_links() -> None:
    full = _full((_link(12, "c"), _link(10, "a"), _link(11, "b")))
    cutoff = pd.Timestamp("2026-01-01 11:00", tz="Europe/Istanbul")

    indexed = IndexedVolumeView(full).at({"1h": 2}, cutoff)
    canonical = _volume_view(full, {"1h": 2}, cutoff)
    assert indexed.timeframe_replays[0].event_links == canonical.timeframe_replays[0].event_links
