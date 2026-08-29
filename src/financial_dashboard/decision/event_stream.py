from __future__ import annotations

from dataclasses import dataclass
from numbers import Real
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


def _order_value(value: Any) -> tuple[str, float | int]:
    """Return a stable ordering value without corrupting numeric test clocks.

    Production uses timestamp-like values, while focused contract tests also use
    numeric clocks such as 10.0 / 10.5 / 11.0. ``pd.Timestamp(10.5)`` truncates
    that distinction to integer nanoseconds, so numeric clocks must stay numeric.
    """

    if isinstance(value, Real) and not isinstance(value, bool):
        return ("number", float(value))
    return ("timestamp", int(pd.Timestamp(value).value))


def _compare(left: Any, right: Any) -> int:
    left_kind, left_value = _order_value(left)
    right_kind, right_value = _order_value(right)
    if left_kind != right_kind:
        raise TypeError("execution event clocks must use comparable timestamp types")
    if left_value < right_value:
        return -1
    if left_value > right_value:
        return 1
    return 0


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
        _order_value(event.observed_at),
        _order_value(event.available_at),
        event.state.value,
        event.side.value,
        event.timeframe.strip().lower(),
        execution_event_kind(event).value,
        event.reason,
        _refs_key(event.source_refs),
    )


def _sort_key(event: ExecutionTriggerEvent) -> tuple:
    return (
        _order_value(event.observed_at),
        _order_value(event.available_at),
        execution_event_kind(event).value,
        event.state.value,
        event.side.value,
        event.reason,
    )


def _freshness_key(event: ExecutionTriggerEvent) -> tuple:
    return (
        _order_value(event.observed_at),
        _order_value(event.available_at),
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
        ready: list[ExecutionTriggerEvent] = []
        keep: list[ExecutionTriggerEvent] = []

        for event in self._pending:
            observed_after_current = _compare(event.observed_at, as_of) > 0
            available_after_current = _compare(event.available_at, as_of) > 0
            if observed_after_current or available_after_current:
                keep.append(event)
                continue

            if previous_as_of is None:
                # First decision bar can consume only an event that is exactly fresh
                # on that bar. Anything older is stale; anything newer stayed pending.
                if _compare(event.observed_at, as_of) == 0 or _compare(event.available_at, as_of) == 0:
                    ready.append(event)
                else:
                    self._expired += 1
                continue

            became_observed = _compare(event.observed_at, previous_as_of) > 0
            became_available = _compare(event.available_at, previous_as_of) > 0
            if became_observed or became_available:
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
