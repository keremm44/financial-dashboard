from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Iterable, Iterator, Mapping, TypeVar

import pandas as pd


DomainStateT = TypeVar("DomainStateT")
DecisionStateT = TypeVar("DecisionStateT")


def _timestamp(value: Any) -> pd.Timestamp:
    return pd.Timestamp(value)


@dataclass(frozen=True, slots=True)
class TimelineFingerprint:
    """Semantic identity of one generated causal timeline."""

    symbol: str
    engine_config: str
    clock_version: str
    pattern_profile: str | None = None


@dataclass(frozen=True, slots=True)
class DomainStatePoint(Generic[DomainStateT]):
    """Frozen domain read-model at one causal boundary.

    ``as_of`` is the decision boundary. ``watermarks`` record the latest native bar
    index consumed for every timeframe, making the causal prefix explicit and
    auditable without carrying historical DataFrame prefixes inside each point.
    """

    as_of: Any
    watermarks: Mapping[str, int]
    state: DomainStateT


@dataclass(frozen=True, slots=True)
class DecisionStatePoint(Generic[DecisionStateT]):
    """Frozen decision input derived once from a matching domain state point."""

    as_of: Any
    domain_position: int
    state: DecisionStateT


class AppendOnlyTimeline(Generic[DomainStateT]):
    """Small in-memory append-only timeline used by historical and live reducers.

    Mutation of old points is deliberately unsupported. New points must move
    strictly forward in causal time. A restart/catch-up path therefore appends the
    same states a cold historical replay would have produced instead of rewriting
    history in place.
    """

    def __init__(self, *, fingerprint: TimelineFingerprint) -> None:
        self.fingerprint = fingerprint
        self._points: list[DomainStateT] = []
        self._last_as_of: pd.Timestamp | None = None

    def append(self, point: DomainStateT, *, as_of: Any) -> int:
        current = _timestamp(as_of)
        if self._last_as_of is not None and current <= self._last_as_of:
            raise ValueError(
                "causal timeline must append in strictly increasing as_of order: "
                f"{current} <= {self._last_as_of}"
            )
        self._points.append(point)
        self._last_as_of = current
        return len(self._points) - 1

    @property
    def last_as_of(self) -> pd.Timestamp | None:
        return self._last_as_of

    @property
    def points(self) -> tuple[DomainStateT, ...]:
        return tuple(self._points)

    def __len__(self) -> int:
        return len(self._points)

    def __iter__(self) -> Iterator[DomainStateT]:
        return iter(self._points)


@dataclass(frozen=True, slots=True)
class CausalStateStore(Generic[DomainStateT, DecisionStateT]):
    """Shared result contract for historical replay and live catch-up."""

    fingerprint: TimelineFingerprint
    domains: tuple[DomainStatePoint[DomainStateT], ...]
    decisions: tuple[DecisionStatePoint[DecisionStateT], ...]

    def __post_init__(self) -> None:
        if len(self.domains) != len(self.decisions):
            raise ValueError("domain and decision timelines must have the same number of cutoffs")
        previous: pd.Timestamp | None = None
        for position, (domain, decision) in enumerate(zip(self.domains, self.decisions, strict=True)):
            domain_as_of = _timestamp(domain.as_of)
            decision_as_of = _timestamp(decision.as_of)
            if domain_as_of != decision_as_of:
                raise ValueError("domain and decision state as_of values must match")
            if decision.domain_position != position:
                raise ValueError("decision domain_position must reference its matching domain point")
            if previous is not None and domain_as_of <= previous:
                raise ValueError("state-store cutoffs must be strictly increasing")
            previous = domain_as_of

    @property
    def cutoffs(self) -> tuple[Any, ...]:
        return tuple(point.as_of for point in self.decisions)

    @property
    def decision_states(self) -> tuple[DecisionStateT, ...]:
        return tuple(point.state for point in self.decisions)


def build_state_store(
    *,
    fingerprint: TimelineFingerprint,
    domain_points: Iterable[DomainStatePoint[DomainStateT]],
    decision_points: Iterable[DecisionStatePoint[DecisionStateT]],
) -> CausalStateStore[DomainStateT, DecisionStateT]:
    return CausalStateStore(
        fingerprint=fingerprint,
        domains=tuple(domain_points),
        decisions=tuple(decision_points),
    )


__all__ = [
    "AppendOnlyTimeline",
    "CausalStateStore",
    "DecisionStatePoint",
    "DomainStatePoint",
    "TimelineFingerprint",
    "build_state_store",
]
