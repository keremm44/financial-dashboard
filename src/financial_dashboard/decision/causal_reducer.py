from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Generic, Iterable, Mapping, Protocol, TypeVar

import pandas as pd

from .state_timeline import (
    CausalStateStore,
    DecisionStatePoint,
    DomainStatePoint,
    TimelineFingerprint,
    build_state_store,
)


DomainStateT = TypeVar("DomainStateT")
DecisionStateT = TypeVar("DecisionStateT")


@dataclass(frozen=True, slots=True)
class CausalBarEvent:
    """One closed market bar ordered by causal availability, not wall-clock alone."""

    available_at: Any
    timeframe: str
    bar_index: int
    bar: Mapping[str, Any]

    @property
    def sort_key(self) -> tuple[int, str, int]:
        return (pd.Timestamp(self.available_at).value, self.timeframe, self.bar_index)


class DomainRuntime(Protocol[DomainStateT]):
    """Incremental domain adapter shared by cold historical replay and live catch-up."""

    def ingest(self, event: CausalBarEvent) -> None: ...

    def freeze(self, *, as_of: Any, watermarks: Mapping[str, int]) -> DomainStateT: ...


DecisionComposer = Callable[[DomainStateT, Any], DecisionStateT]


def _ordered_events(events: Iterable[CausalBarEvent]) -> tuple[CausalBarEvent, ...]:
    rows = tuple(events)
    if all(
        left.sort_key < right.sort_key
        for left, right in zip(rows, rows[1:])
    ):
        return rows
    return tuple(sorted(rows, key=lambda item: item.sort_key))


class CausalTimelineReducer(Generic[DomainStateT, DecisionStateT]):
    """Advance domain state once per bar and freeze decision inputs at cutoffs.

    Historical mode supplies all known closed bars in one batch. Live mode supplies
    only bars after the persisted/known watermark. The reducer logic is identical in
    both cases, which prevents a separate live semantic path from drifting away from
    backtests.
    """

    def __init__(
        self,
        *,
        runtime: DomainRuntime[DomainStateT],
        compose_decision: DecisionComposer[DomainStateT, DecisionStateT],
        fingerprint: TimelineFingerprint,
    ) -> None:
        self.runtime = runtime
        self.compose_decision = compose_decision
        self.fingerprint = fingerprint
        self._watermarks: dict[str, int] = {}
        self._last_event_key: tuple[int, str, int] | None = None

    @property
    def watermarks(self) -> Mapping[str, int]:
        return dict(self._watermarks)

    def _ingest(self, event: CausalBarEvent) -> None:
        key = event.sort_key
        if self._last_event_key is not None and key <= self._last_event_key:
            raise ValueError(
                "causal bar events must be strictly ordered by "
                "(available_at, timeframe, bar_index)"
            )
        previous = self._watermarks.get(event.timeframe, -1)
        if event.bar_index != previous + 1:
            raise ValueError(
                f"non-contiguous {event.timeframe} watermark: "
                f"expected {previous + 1}, got {event.bar_index}"
            )
        self.runtime.ingest(event)
        self._watermarks[event.timeframe] = event.bar_index
        self._last_event_key = key

    def run(
        self,
        *,
        events: Iterable[CausalBarEvent],
        cutoffs: Iterable[Any],
    ) -> CausalStateStore[DomainStateT, DecisionStateT]:
        ordered_events = _ordered_events(events)
        ordered_cutoffs = tuple(pd.Timestamp(value) for value in cutoffs)
        if any(left >= right for left, right in zip(ordered_cutoffs, ordered_cutoffs[1:])):
            raise ValueError("decision cutoffs must be strictly increasing")

        domain_points: list[DomainStatePoint[DomainStateT]] = []
        decision_points: list[DecisionStatePoint[DecisionStateT]] = []
        event_position = 0

        for cutoff in ordered_cutoffs:
            while event_position < len(ordered_events):
                event = ordered_events[event_position]
                if pd.Timestamp(event.available_at) > cutoff:
                    break
                self._ingest(event)
                event_position += 1

            domain_state = self.runtime.freeze(
                as_of=cutoff,
                watermarks=self._watermarks,
            )
            domain_point = DomainStatePoint(
                as_of=cutoff,
                watermarks=dict(self._watermarks),
                state=domain_state,
            )
            domain_position = len(domain_points)
            domain_points.append(domain_point)
            decision_points.append(
                DecisionStatePoint(
                    as_of=cutoff,
                    domain_position=domain_position,
                    state=self.compose_decision(domain_state, cutoff),
                )
            )

        return build_state_store(
            fingerprint=self.fingerprint,
            domain_points=domain_points,
            decision_points=decision_points,
        )


__all__ = [
    "CausalBarEvent",
    "CausalTimelineReducer",
    "DecisionComposer",
    "DomainRuntime",
]
