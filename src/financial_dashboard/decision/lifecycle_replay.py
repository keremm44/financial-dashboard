from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from .composer import DecisionAction
from .entry import EntryDecision
from .execution import ExecutionEventKind, ExecutionTriggerEvent, ExecutionTriggerState
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


def _synthetic_confirmation_event(
    as_of: Any,
    *,
    side: StructuralDirection,
    reason: str,
    kind: ExecutionEventKind,
) -> ExecutionTriggerEvent:
    return ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=side,
        timeframe="1h",
        observed_at=as_of,
        available_at=as_of,
        reason=reason,
        source_refs=(),
        kind=kind,
    )


def replay_canonical_trade_lifecycle(
    snapshots: Iterable["DecisionInputSnapshot"],
    *,
    config: "DecisionEngineConfig | None" = None,
    entry_execution_events: Mapping[Any, ExecutionTriggerEvent] | None = None,
    exit_execution_events: Mapping[Any, ExecutionTriggerEvent] | None = None,
    initial_state: TradeLifecycleState | None = None,
    readiness_execution_proxy: bool = False,
    exit_event_carry_decision_bars: int = 0,
    exit_ready_persistence_bars: int = 3,
) -> CanonicalLifecycleReplayResult:
    """Replay the canonical long-only ownership path over frozen snapshots.

    Primary entry/exit execution is 1H. 30m is not consumed by this path. A raw 1H
    confirmation is preferred. To avoid an EXIT_READY state waiting indefinitely for
    another pattern edge, three consecutive 1H EXIT_READY decisions without recovery
    causally confirm the exit by persistence. No future bars or prices are consulted.
    """

    if exit_event_carry_decision_bars < 0:
        raise ValueError("exit_event_carry_decision_bars must be >= 0")
    if exit_ready_persistence_bars < 1:
        raise ValueError("exit_ready_persistence_bars must be >= 1")

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
    exit_ready_streak = 0

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
            exit_ready_streak = 0
            raw_entry_event = entry_events.get(snapshot.as_of)
            entry = snapshot.entry_decision(config=config, execution_event=raw_entry_event)
            if (
                readiness_execution_proxy
                and raw_entry_event is None
                and entry.action is DecisionAction.READY
            ):
                raw_entry_event = _synthetic_confirmation_event(
                    snapshot.as_of,
                    side=StructuralDirection.LONG,
                    reason="AUDIT_PROXY_CANONICAL_ENTRY_READY",
                    kind=ExecutionEventKind.AUDIT_PROXY,
                )
                entry = snapshot.entry_decision(config=config, execution_event=raw_entry_event)
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

            exit_decision = snapshot.position_exit_decision(state, execution_event=raw_exit_event)

            prospective_ready_streak = (
                exit_ready_streak + 1
                if exit_decision.stage is ExitStage.EXIT_READY
                else 0
            )
            if (
                raw_exit_event is None
                and exit_decision.stage is ExitStage.EXIT_READY
                and exit_decision.action is DecisionAction.HOLD
                and prospective_ready_streak >= exit_ready_persistence_bars
            ):
                raw_exit_event = _synthetic_confirmation_event(
                    snapshot.as_of,
                    side=StructuralDirection.SHORT,
                    reason="1H_EXIT_READY_PERSISTENCE_CONFIRMED",
                    kind=ExecutionEventKind.REACTION_CONFIRMATION,
                )
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
                raw_exit_event = _synthetic_confirmation_event(
                    snapshot.as_of,
                    side=StructuralDirection.SHORT,
                    reason="AUDIT_PROXY_CANONICAL_EXIT_READY",
                    kind=ExecutionEventKind.AUDIT_PROXY,
                )
                exit_decision = snapshot.position_exit_decision(
                    state,
                    execution_event=raw_exit_event,
                )
                proxy_used = exit_decision.action is DecisionAction.SELL

            transition = transition_position_exit_lifecycle(state, exit_decision)

            if transition.current.position is PositionState.FLAT:
                exit_ready_streak = 0
            elif exit_decision.stage is ExitStage.EXIT_READY:
                exit_ready_streak = prospective_ready_streak
            else:
                exit_ready_streak = 0

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
