from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd

from .execution import ExecutionTriggerEvent, execution_event_kind


@dataclass(frozen=True, slots=True)
class ExecutionEventLedger:
    """Run-scoped event-channel accounting; audit-only, no decision authority."""

    total: int = 0
    offered: int = 0
    consumed: int = 0
    expired: int = 0
    pending: int = 0


def _timestamp(value: Any) -> pd.Timestamp:
    return pd.Timestamp(value)


def _refs_key(source_refs) -> tuple:
    return tuple(
        sorted(
            tuple(
                str(getattr(ref, key, None))
                for key in ("domain", "symbol", "timeframe", "fact_type", "native_id")
            )
            for ref in source_refs
        )
    )


def _dedup_key(event: ExecutionTriggerEvent) -> tuple:
    return (
        _timestamp(event.observed_at),
        _timestamp(event.available_at),
        event.state.value,
        event.side.value,
        event.timeframe.strip().lower(),
        execution_event_kind(event).value,
        event.reason,
        _refs_key(event.source_refs),
    )


def _sort_key(event: ExecutionTriggerEvent) -> tuple:
    observed = _timestamp(event.observed_at)
    available = _timestamp(event.available_at)
    return (
        observed.value,
        available.value,
        execution_event_kind(event).value,
        event.state.value,
        event.side.value,
        event.reason,
    )


def _freshness_key(event: ExecutionTriggerEvent) -> tuple:
    observed = _timestamp(event.observed_at)
    available = _timestamp(event.available_at)
    return (
        observed.value,
        available.value,
        execution_event_kind(event).value,
        event.state.value,
        event.side.value,
        event.reason,
    )


class ExecutionEventQueue:
    """Deliver each raw 30m execution event to at most one decision bar.

    A native :30 event may be consumed by the next 1h decision bar when it became
    observable/available after the previous decision. Events that were already
    evaluable on an earlier decision bar are stale and never carried forward.
    """

    def __init__(self, events: Iterable[ExecutionTriggerEvent]) -> None:
        unique: dict[tuple, ExecutionTriggerEvent] = {}
        for event in events:
            if not isinstance(event, ExecutionTriggerEvent):
                raise TypeError("execution event queue accepts ExecutionTriggerEvent instances")
            if str(event.timeframe).strip().lower() != "30m":
                raise ValueError("v1 closed-bar execution event channel is fixed to the 30m timeframe")
            unique[_dedup_key(event)] = event
        self._pending = sorted(unique.values(), key=_sort_key)
        self._total = len(self._pending)
        self._offered = 0
        self._consumed = 0
        self._expired = 0

    @property
    def ledger(self) -> ExecutionEventLedger:
        return ExecutionEventLedger(
            total=self._total,
            offered=self._offered,
            consumed=self._consumed,
            expired=self._expired,
            pending=len(self._pending),
        )

    def take_fresh(
        self,
        as_of: Any,
        *,
        previous_as_of: Any | None = None,
    ) -> ExecutionTriggerEvent | None:
        as_of_value = _timestamp(as_of)
        previous = None if previous_as_of is None else _timestamp(previous_as_of)
        ready: list[ExecutionTriggerEvent] = []
        keep: list[ExecutionTriggerEvent] = []

        for event in self._pending:
            observed = _timestamp(event.observed_at)
            available = _timestamp(event.available_at)
            if observed > as_of_value or available > as_of_value:
                keep.append(event)
                continue

            if previous is None:
                if observed == as_of_value or available == as_of_value:
                    ready.append(event)
                else:
                    self._expired += 1
                continue

            if observed > previous or available > previous:
                ready.append(event)
            else:
                self._expired += 1

        self._pending = keep
        if not ready:
            return None

        chosen = max(ready, key=_freshness_key)
        self._offered += 1
        self._expired += len(ready) - 1
        return chosen

    def record_consumed(self) -> None:
        self._consumed += 1

    def record_expired_event(self) -> None:
        if self._offered > 0:
            self._offered -= 1
            self._expired += 1


__all__ = ["ExecutionEventLedger", "ExecutionEventQueue"]
