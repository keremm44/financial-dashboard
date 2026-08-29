from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from .composer import DecisionAction
from .entry import EntryDecision
from .execution import ExecutionTriggerEvent, ExecutionTriggerState
from .exit import PositionExitDecision, transition_position_exit_lifecycle
from .lifecycle import (
    ExitStage,
    PositionState,
    TradeLifecycleState,
    TradeLifecycleTransition,
    transition_entry_lifecycle,
)
from .structural import StructuralDirection

if TYPE_CHECKING:
    from financial_dashboard.decision_input import DecisionInputSnapshot
    from .engine import DecisionEngineConfig


@dataclass(frozen=True, slots=True)
class CanonicalLifecycleReplayRow:
    """One causal ownership transition produced from one frozen market snapshot."""

    snapshot: "DecisionInputSnapshot"
    previous_state: TradeLifecycleState
    current_state: TradeLifecycleState
    transition: TradeLifecycleTransition
    entry_decision: EntryDecision | None
    exit_decision: PositionExitDecision | None
    execution_proxy_used: bool = False

    def __post_init__(self) -> None:
        if self.previous_state != self.transition.previous:
            raise ValueError("replay row previous state must match lifecycle transition")
        if self.current_state != self.transition.current:
            raise ValueError("replay row current state must match lifecycle transition")
        if (self.entry_decision is None) == (self.exit_decision is None):
            raise ValueError("replay row must contain exactly one entry or exit decision")
        if self.previous_state.position is PositionState.FLAT and self.entry_decision is None:
            raise ValueError("FLAT replay row must be owned by entry decision")
        if self.previous_state.position is PositionState.OPEN and self.exit_decision is None:
            raise ValueError("OPEN replay row must be owned by position exit decision")
        if self.execution_proxy_used and not self.event_consumed:
            raise ValueError("canonical readiness proxy must be consumed on the same bar")

    @property
    def action(self):
        return self.transition.action

    @property
    def event_consumed(self) -> bool:
        if self.entry_decision is not None:
            return bool(self.entry_decision.execution_event_consumed)
        assert self.exit_decision is not None
        return bool(self.exit_decision.execution_event_consumed)


@dataclass(frozen=True, slots=True)
class CanonicalLifecycleReplayResult:
    """Sequential Turn 6 -> 7 -> 8 replay result with explicit ownership state."""

    initial_state: TradeLifecycleState
    final_state: TradeLifecycleState
    rows: tuple[CanonicalLifecycleReplayRow, ...]

    def __post_init__(self) -> None:
        expected = self.initial_state
        for row in self.rows:
            if row.previous_state != expected:
                raise ValueError("canonical lifecycle replay rows must form one contiguous chain")
            expected = row.current_state
        if expected != self.final_state:
            raise ValueError("canonical lifecycle replay final state must match row chain")


def _validate_initial_state(state: TradeLifecycleState) -> None:
    if state.position is PositionState.OPEN and state.entry_metadata is None:
        raise ValueError(
            "canonical lifecycle replay requires entry metadata for an initial OPEN position"
        )


def _proxy_event(as_of: Any, *, side: StructuralDirection, reason: str) -> ExecutionTriggerEvent:
    return ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=side,
        timeframe="30m",
        observed_at=as_of,
        available_at=as_of,
        reason=reason,
        source_refs=(),
    )


def replay_canonical_trade_lifecycle(
    snapshots: Iterable["DecisionInputSnapshot"],
    *,
    config: "DecisionEngineConfig | None" = None,
    entry_execution_events: Mapping[Any, ExecutionTriggerEvent] | None = None,
    exit_execution_events: Mapping[Any, ExecutionTriggerEvent] | None = None,
    initial_state: TradeLifecycleState | None = None,
    readiness_execution_proxy: bool = False,
    exit_event_carry_decision_bars: int = 1,
) -> CanonicalLifecycleReplayResult:
    """Replay the canonical long-only ownership path over frozen snapshots.

    FLAT bars evaluate only the Turn 6 entry path and can open only through the Turn 7
    metadata transition. OPEN bars evaluate only the Turn 8 exit path.

    Entry events remain same-window only. Exit events have one narrowly bounded
    synchronization exception: a confirmed 30m SHORT click that was causally available
    while the position was OPEN but could not be consumed because the structural exit
    path was not EXIT_READY may be offered to the next decision snapshot only. This
    preserves the 1H structural exit gate while preventing a :30 click from disappearing
    immediately before that gate becomes ready. The event is never carried across a
    second decision bar, a closed position, or into a different trade.

    ``readiness_execution_proxy`` is hindsight-audit infrastructure only. When no raw
    execution event exists, it substitutes a same-bar confirmed 30m event exactly at
    an already-computed READY or EXIT_READY boundary. It does not change scenario,
    structure, eligibility, target, or exit-stage semantics and is explicitly marked
    on the replay row so audit output cannot be confused with production execution.
    """

    if exit_event_carry_decision_bars < 0:
        raise ValueError("exit_event_carry_decision_bars must be >= 0")

    state = initial_state or TradeLifecycleState()
    _validate_initial_state(state)
    starting_state = state
    entry_events = entry_execution_events or {}
    exit_events = exit_execution_events or {}
    rows: list[CanonicalLifecycleReplayRow] = []
    previous_as_of: Any | None = None
    stream_symbol: str | None = None
    pending_exit_event: ExecutionTriggerEvent | None = None
    pending_exit_age = 0
    pending_exit_trade_id: str | None = None

    for snapshot in snapshots:
        if snapshot.as_of is None:
            raise ValueError("canonical lifecycle replay snapshot as_of must be known")
        if previous_as_of is not None and snapshot.as_of <= previous_as_of:
            raise ValueError("canonical lifecycle replay snapshots must be strictly increasing")
        if not str(snapshot.symbol).strip():
            raise ValueError("canonical lifecycle replay snapshot symbol must be non-empty")
        if stream_symbol is None:
            stream_symbol = str(snapshot.symbol)
        elif str(snapshot.symbol) != stream_symbol:
            raise ValueError("canonical lifecycle replay stream must contain one symbol")
        if state.entry_metadata is not None and state.entry_metadata.symbol != str(snapshot.symbol):
            raise ValueError("open position symbol must match replay snapshot symbol")
        for ref in snapshot.source_refs:
            if not ref.is_available_at(snapshot.as_of):
                raise ValueError("canonical lifecycle replay contains future-unavailable evidence")

        previous = state
        entry: EntryDecision | None = None
        exit_decision: PositionExitDecision | None = None
        proxy_used = False

        if state.position is PositionState.FLAT:
            pending_exit_event = None
            pending_exit_age = 0
            pending_exit_trade_id = None
            raw_entry_event = entry_events.get(snapshot.as_of)
            entry = snapshot.entry_decision(
                config=config,
                execution_event=raw_entry_event,
            )
            if (
                readiness_execution_proxy
                and raw_entry_event is None
                and entry.action is DecisionAction.READY
            ):
                raw_entry_event = _proxy_event(
                    snapshot.as_of,
                    side=StructuralDirection.LONG,
                    reason="AUDIT_PROXY_CANONICAL_ENTRY_READY",
                )
                entry = snapshot.entry_decision(
                    config=config,
                    execution_event=raw_entry_event,
                )
                proxy_used = entry.action is DecisionAction.BUY
            transition = transition_entry_lifecycle(
                state,
                entry,
                snapshot,
                execution_event=raw_entry_event,
            )
        else:
            current_trade_id = state.trade_id
            if pending_exit_trade_id is not None and pending_exit_trade_id != current_trade_id:
                pending_exit_event = None
                pending_exit_age = 0
                pending_exit_trade_id = None

            current_exit_event = exit_events.get(snapshot.as_of)
            carried_exit_event = None
            if (
                current_exit_event is None
                and pending_exit_event is not None
                and pending_exit_age <= exit_event_carry_decision_bars
                and pending_exit_trade_id == current_trade_id
            ):
                carried_exit_event = pending_exit_event
            raw_exit_event = current_exit_event or carried_exit_event

            exit_decision = snapshot.position_exit_decision(
                state,
                execution_event=raw_exit_event,
            )
            if (
                readiness_execution_proxy
                and raw_exit_event is None
                and exit_decision.stage is ExitStage.EXIT_READY
                and exit_decision.action is DecisionAction.HOLD
            ):
                raw_exit_event = _proxy_event(
                    snapshot.as_of,
                    side=StructuralDirection.SHORT,
                    reason="AUDIT_PROXY_CANONICAL_EXIT_READY",
                )
                exit_decision = snapshot.position_exit_decision(
                    state,
                    execution_event=raw_exit_event,
                )
                proxy_used = exit_decision.action is DecisionAction.SELL
            transition = transition_position_exit_lifecycle(state, exit_decision)

            if exit_decision.execution_event_consumed or transition.current.position is PositionState.FLAT:
                pending_exit_event = None
                pending_exit_age = 0
                pending_exit_trade_id = None
            elif current_exit_event is not None and exit_event_carry_decision_bars > 0:
                pending_exit_event = current_exit_event
                pending_exit_age = 1
                pending_exit_trade_id = current_trade_id
            elif carried_exit_event is not None:
                pending_exit_age += 1
                if pending_exit_age > exit_event_carry_decision_bars:
                    pending_exit_event = None
                    pending_exit_age = 0
                    pending_exit_trade_id = None
            else:
                pending_exit_event = None
                pending_exit_age = 0
                pending_exit_trade_id = None

        state = transition.current
        rows.append(
            CanonicalLifecycleReplayRow(
                snapshot=snapshot,
                previous_state=previous,
                current_state=state,
                transition=transition,
                entry_decision=entry,
                exit_decision=exit_decision,
                execution_proxy_used=proxy_used,
            )
        )
        previous_as_of = snapshot.as_of

    return CanonicalLifecycleReplayResult(
        initial_state=starting_state,
        final_state=state,
        rows=tuple(rows),
    )


__all__ = [
    "CanonicalLifecycleReplayResult",
    "CanonicalLifecycleReplayRow",
    "replay_canonical_trade_lifecycle",
]
