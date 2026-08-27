from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Mapping

import pandas as pd


@dataclass(frozen=True, slots=True)
class _LinkBoundary:
    links: tuple[Any, ...]
    assessed_ns: tuple[int, ...] | None

    @classmethod
    def build(cls, links: tuple[Any, ...]) -> "_LinkBoundary":
        assessed = tuple(pd.Timestamp(link.assessed_at).value for link in links)
        monotonic = all(left <= right for left, right in zip(assessed, assessed[1:]))
        return cls(links=links, assessed_ns=assessed if monotonic else None)

    def through(self, cutoff: Any) -> tuple[Any, ...]:
        cutoff_ts = pd.Timestamp(cutoff)
        if self.assessed_ns is None:
            # Exact fallback for any replay whose canonical link order is not
            # monotonic by assessed_at. Never reorder native evidence for speed.
            return tuple(link for link in self.links if pd.Timestamp(link.assessed_at) <= cutoff_ts)
        boundary = bisect_right(self.assessed_ns, cutoff_ts.value)
        return self.links[:boundary]


class IndexedVolumeView:
    """Point-in-time Volume views without re-scanning all event links per cutoff."""

    def __init__(self, full: Any) -> None:
        self.full = full
        self._boundaries = {
            replay.timeframe: _LinkBoundary.build(tuple(replay.event_links))
            for replay in full.timeframe_replays
        }

    def at(self, indices: Mapping[str, int], cutoff: Any) -> Any:
        timeframe_replays = []
        for replay in self.full.timeframe_replays:
            index = indices[replay.timeframe]
            timeframe_replays.append(
                SimpleNamespace(
                    timeframe=replay.timeframe,
                    latest=replay.history[index],
                    event_links=self._boundaries[replay.timeframe].through(cutoff),
                )
            )
        return SimpleNamespace(
            symbol=self.full.symbol,
            timeframes=tuple(self.full.timeframes),
            timeframe_replays=tuple(timeframe_replays),
        )


__all__ = ["IndexedVolumeView"]
