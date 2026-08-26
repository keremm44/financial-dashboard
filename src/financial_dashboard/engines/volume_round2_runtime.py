from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Iterator, Sequence

import pandas as pd

from .volume_round2 import VolumeRound2Assessment, build_volume_round2_assessment


@dataclass(frozen=True, slots=True)
class _CachedIterrowsFrame:
    """Read-only frame facade that materializes pandas row Series exactly once.

    Volume Round 2 has several independent audits that call ``iterrows()`` over the
    same native frame. The mathematical consumers only read the returned rows, so
    retaining those immutable read rows removes repeated Series construction without
    changing bar order, labels, or values.
    """

    rows: tuple[tuple[Any, pd.Series], ...]

    @classmethod
    def from_frame(cls, frame: pd.DataFrame) -> "_CachedIterrowsFrame":
        return cls(tuple(frame.iterrows()))

    def iterrows(self) -> Iterator[tuple[Any, pd.Series]]:
        return iter(self.rows)


def _runtime_replay(replay: Any) -> Any:
    cached_frame = _CachedIterrowsFrame.from_frame(replay.input_batch.frame)
    input_batch = SimpleNamespace(frame=cached_frame)
    return SimpleNamespace(
        symbol=getattr(replay, "symbol", None),
        timeframe=replay.timeframe,
        input_batch=input_batch,
        history=replay.history,
        latest=replay.latest,
        event_links=replay.event_links,
        participation_without_structure=replay.participation_without_structure,
    )


def build_volume_round2_assessment_runtime(
    *,
    symbol: str,
    timeframe_replays: Sequence[Any],
    structure_snapshots: Sequence[Any],
    clock: Any,
) -> VolumeRound2Assessment:
    """Run canonical Round 2 with row materialization shared per timeframe."""

    runtime_replays = tuple(_runtime_replay(replay) for replay in timeframe_replays)
    return build_volume_round2_assessment(
        symbol=symbol,
        timeframe_replays=runtime_replays,
        structure_snapshots=structure_snapshots,
        clock=clock,
    )


__all__ = ["build_volume_round2_assessment_runtime"]
