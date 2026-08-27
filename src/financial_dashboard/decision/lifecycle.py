from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .composer import DecisionAction, FinalDecision


class PositionState(StrEnum):
    """Persistent ownership state for the long-only trade lifecycle."""

    FLAT = "FLAT"
    OPEN = "OPEN"


class ExitStage(StrEnum):
    """Persistent maturity of the dedicated long-position exit path."""

    MONITOR = "MONITOR"
    EXIT_WATCH = "EXIT_WATCH"
    EXIT_READY = "EXIT_READY"


@dataclass(frozen=True, slots=True)
class TradeLifecycleState:
    position: PositionState = PositionState.FLAT
    exit_stage: ExitStage | None = None
    trade_id: str | None = None
    entry_as_of: Any | None = None

    def __post_init__(self) -> None:
        if self.position is PositionState.FLAT:
            if self.exit_stage is not None or self.trade_id is not None or self.entry_as_of is not None:
                raise ValueError("FLAT lifecycle state cannot carry open-trade metadata")
        elif self.exit_stage is None or self.trade_id is None or self.entry_as_of is None:
            raise ValueError("OPEN lifecycle state requires exit stage and entry metadata")


@dataclass(frozen=True, slots=True)
class TradeLifecycleTransition:
    previous: TradeLifecycleState
    current: TradeLifecycleState
    requested_action: DecisionAction
    action: DecisionAction
    reason: str
    as_of: Any

    @property
    def changed_position(self) -> bool:
        return self.previous.position is not self.current.position


def _trade_id(as_of: Any) -> str:
    iso = as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of)
    return f"trade:{iso}"


def _open_state_with_stage(state: TradeLifecycleState, stage: ExitStage) -> TradeLifecycleState:
    return TradeLifecycleState(
        position=PositionState.OPEN,
        exit_stage=stage,
        trade_id=state.trade_id,
        entry_as_of=state.entry_as_of,
    )


def transition_trade_lifecycle(
    state: TradeLifecycleState,
    final: FinalDecision,
    *,
    as_of: Any,
    exit_stage: ExitStage | None = None,
    exit_execution_confirmed: bool = False,
) -> TradeLifecycleTransition:
    """Fold one market decision through persistent long-only ownership.

    FLAT uses the existing long entry decision. OPEN never treats a bearish market
    decision as a sell by itself: its action space is owned by the dedicated exit
    assessment. A SELL can execute only when the exit path is EXIT_READY and a fresh
    exit execution event has been confirmed by that separate contract.
    """

    requested = final.action

    if state.position is PositionState.FLAT:
        if exit_execution_confirmed:
            raise ValueError("exit execution cannot be confirmed while lifecycle is FLAT")
        if requested is DecisionAction.BUY:
            current = TradeLifecycleState(
                position=PositionState.OPEN,
                exit_stage=ExitStage.MONITOR,
                trade_id=_trade_id(as_of),
                entry_as_of=as_of,
            )
            return TradeLifecycleTransition(
                state,
                current,
                requested,
                DecisionAction.BUY,
                "LIFECYCLE_FLAT_ENTRY_EXECUTED",
                as_of,
            )
        if requested is DecisionAction.SELL:
            return TradeLifecycleTransition(
                state,
                state,
                requested,
                DecisionAction.WAIT,
                "LIFECYCLE_FLAT_SELL_SUPPRESSED",
                as_of,
            )
        if requested is DecisionAction.HOLD:
            raise ValueError("HOLD cannot be requested while lifecycle is FLAT")
        return TradeLifecycleTransition(
            state,
            state,
            requested,
            requested,
            "LIFECYCLE_FLAT_NO_POSITION_CHANGE",
            as_of,
        )

    target_stage = exit_stage or state.exit_stage or ExitStage.MONITOR
    if exit_execution_confirmed:
        if target_stage is not ExitStage.EXIT_READY:
            raise ValueError("long exit execution requires EXIT_READY stage")
        return TradeLifecycleTransition(
            state,
            TradeLifecycleState(),
            requested,
            DecisionAction.SELL,
            "LIFECYCLE_OPEN_EXIT_EXECUTED_CONFIRMED_EVENT",
            as_of,
        )

    current = _open_state_with_stage(state, target_stage)
    if requested is DecisionAction.BUY:
        reason = "LIFECYCLE_REPEATED_BUY_SUPPRESSED"
    elif requested is DecisionAction.SELL:
        reason = "LIFECYCLE_LEGACY_SELL_IGNORED_BY_LONG_EXIT_CONTRACT"
    elif state.exit_stage is not target_stage:
        reason = f"LIFECYCLE_EXIT_STAGE_{state.exit_stage.value}_TO_{target_stage.value}"
    elif target_stage is ExitStage.EXIT_READY:
        reason = "LIFECYCLE_EXIT_READY_AWAITING_FRESH_EVENT"
    elif target_stage is ExitStage.EXIT_WATCH:
        reason = "LIFECYCLE_EXIT_WATCH_POSITION_HELD"
    else:
        reason = "LIFECYCLE_OPEN_POSITION_HELD"

    return TradeLifecycleTransition(
        state,
        current,
        requested,
        DecisionAction.HOLD,
        reason,
        as_of,
    )


__all__ = [
    "ExitStage",
    "PositionState",
    "TradeLifecycleState",
    "TradeLifecycleTransition",
    "transition_trade_lifecycle",
]
