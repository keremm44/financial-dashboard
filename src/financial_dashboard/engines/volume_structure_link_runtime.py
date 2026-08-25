from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .market_structure_events import MarketStructureEventRecord
from .volume_evidence import (
    StructureVolumeLink,
    StructureVolumeTiming,
    VolumeEvidenceSnapshot,
    _eligible_structure_event,
    _resolve_relation,
    _sum,
    _summarize_window,
    _timestamps_equal,
    _validate_namespace,
)


@dataclass(frozen=True, slots=True)
class _VolumeHistoryIndex:
    """Reusable causal index for one same-timeframe Volume history.

    The legacy multi-event linker rebuilt the complete confirmed history and a
    bar-index dictionary once for every Structure event.  This object performs that
    immutable preparation once and keeps the event-window semantics unchanged.
    """

    confirmed: tuple[VolumeEvidenceSnapshot, ...]
    by_bar: dict[int, VolumeEvidenceSnapshot]
    symbol: str
    timeframe: str

    @classmethod
    def build(cls, history: Sequence[VolumeEvidenceSnapshot]) -> _VolumeHistoryIndex:
        confirmed = tuple(snapshot for snapshot in history if snapshot.is_confirmed)
        if not confirmed:
            return cls((), {}, "", "")

        symbol = confirmed[0].symbol
        timeframe = confirmed[0].timeframe
        if any(snapshot.symbol != symbol for snapshot in confirmed):
            raise ValueError("Volume history contains multiple symbols")
        if any(snapshot.timeframe != timeframe for snapshot in confirmed):
            raise ValueError("Volume history contains multiple timeframes")

        # Preserve the legacy `next(...)` behavior when malformed input contains a
        # duplicate bar index: the first confirmed observation wins.
        by_bar: dict[int, VolumeEvidenceSnapshot] = {}
        for snapshot in confirmed:
            by_bar.setdefault(snapshot.bar_index, snapshot)
        return cls(confirmed, by_bar, symbol, timeframe)

    @property
    def last_available_bar(self) -> int:
        return self.confirmed[-1].bar_index if self.confirmed else -1

    @property
    def assessed_at(self):
        return self.confirmed[-1].timestamp if self.confirmed else None


def _link_one(
    event: MarketStructureEventRecord,
    index: _VolumeHistoryIndex,
    *,
    pre_event_bars: int,
    follow_through_bars: int,
) -> StructureVolumeLink:
    if index.confirmed:
        _validate_namespace(event, symbol=index.symbol, timeframe=index.timeframe)
        if index.last_available_bar < event.event_bar:
            raise ValueError("Volume as-of boundary precedes Structure confirmation")
        at_event = index.by_bar.get(event.event_bar)
        if at_event is None:
            raise ValueError("Volume history has no bar aligned to Structure confirmation")
        if not _timestamps_equal(at_event.timestamp, event.confirmed_at):
            raise ValueError("Structure confirmation timestamp is not aligned to Volume history")
        symbol = index.symbol
        timeframe = index.timeframe
    else:
        symbol = (event.symbol or "").strip().upper()
        timeframe = (event.timeframe or "").strip().lower()

    event_direction = int(event.direction)
    follow_end = min(event.event_bar + follow_through_bars, index.last_available_bar)
    windows = (
        _summarize_window(
            timing=StructureVolumeTiming.PRE_EVENT,
            start_bar=event.event_bar - pre_event_bars,
            end_bar=event.event_bar - 1,
            expected_bar_count=pre_event_bars,
            history_by_bar=index.by_bar,
            event_direction=event_direction,
        ),
        _summarize_window(
            timing=StructureVolumeTiming.AT_EVENT,
            start_bar=event.event_bar,
            end_bar=event.event_bar,
            expected_bar_count=1,
            history_by_bar=index.by_bar,
            event_direction=event_direction,
        ),
        _summarize_window(
            timing=StructureVolumeTiming.FOLLOW_THROUGH,
            start_bar=event.event_bar + 1,
            end_bar=follow_end,
            expected_bar_count=follow_through_bars,
            history_by_bar=index.by_bar,
            event_direction=event_direction,
        ),
    )
    relation = _resolve_relation(windows)
    reasons = (
        f"event={event.event_uid}",
        f"aligned_confirmed={_sum(windows, 'aligned_confirmed_count')}",
        f"opposed_confirmed={_sum(windows, 'opposed_confirmed_count')}",
        f"shock={_sum(windows, 'shock_count')}",
        f"unavailable={_sum(windows, 'unavailable_count')}",
    )
    return StructureVolumeLink(
        event_uid=event.event_uid,
        symbol=symbol,
        timeframe=timeframe,
        scope=event.scope,
        event_type=event.event_type,
        bos_maturity=event.bos_maturity,
        event_direction=event_direction,
        event_bar=event.event_bar,
        confirmed_at=event.confirmed_at,
        broken_level=event.broken_level,
        assessed_at=index.assessed_at,
        relation=relation,
        windows=windows,
        reasons=reasons,
    )


def link_structure_events_to_volume_indexed(
    events: Iterable[MarketStructureEventRecord],
    history: Sequence[VolumeEvidenceSnapshot],
    *,
    pre_event_bars: int = 2,
    follow_through_bars: int = 2,
) -> tuple[StructureVolumeLink, ...]:
    """Exact multi-event linker with one reusable history index.

    No evidence category, window, or causal boundary changes.  Complexity falls
    from repeatedly scanning all Volume history for every Structure event to one
    history pass plus the fixed event windows.
    """

    if pre_event_bars < 0 or follow_through_bars < 0:
        raise ValueError("Structure/Volume window lengths must be non-negative")

    eligible = tuple(event for event in events if _eligible_structure_event(event))
    if not eligible:
        return ()

    index = _VolumeHistoryIndex.build(history)
    return tuple(
        _link_one(
            event,
            index,
            pre_event_bars=pre_event_bars,
            follow_through_bars=follow_through_bars,
        )
        for event in eligible
    )


__all__ = ["link_structure_events_to_volume_indexed"]
