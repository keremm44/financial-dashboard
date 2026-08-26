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
    """Persistent exit maturity state.

    Pass 1 only uses MONITOR. EXIT_WATCH and EXIT_READY are reserved for the
    dedicated exit-assessment pass; they are defined now so the transition graph is
    explicit without inventing exit rules prematurely.
    """

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


def transition_trade_lifecycle(
    state: TradeLifecycleState,
    final: FinalDecision,
    *,
    as_of: Any,
) -> TradeLifecycleTransition:
    """Fold one market decision through persistent long-only trade ownership.

    This pass deliberately does not invent exit eligibility. It only enforces the
    ownership invariants around the existing BUY/SELL market-decision stream:

    - BUY may execute only while FLAT.
    - SELL may execute only while OPEN.
    - while OPEN, entry-oriented WAIT/READY/NO_TRADE/BUY outputs surface as HOLD.

    A later exit-assessment pass will own MONITOR -> EXIT_WATCH -> EXIT_READY and
    replace the legacy SELL candidate semantics. Keeping that work separate avoids
    silently treating a short-term bearish market assessment as a valid long exit.
    """

    requested = final.action

    if state.position is PositionState.FLAT:
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

    # OPEN: another entry cannot be executed. Until the dedicated exit assessor is
    # installed, the existing SELL candidate is the only action allowed to close the
    # lifecycle. All entry-path states become HOLD instead of leaking repeated BUYs.
    if requested is DecisionAction.SELL:
        current = TradeLifecycleState()
        return TradeLifecycleTransition(
            state,
            current,
            requested,
            DecisionAction.SELL,
            "LIFECYCLE_OPEN_EXIT_EXECUTED_LEGACY_CANDIDATE",
            as_of,
        )
    if requested in {
        DecisionAction.BUY,
        DecisionAction.WAIT,
        DecisionAction.READY,
        DecisionAction.NO_TRADE,
        DecisionAction.HOLD,
    }:
        reason = (
            "LIFECYCLE_REPEATED_BUY_SUPPRESSED"
            if requested is DecisionAction.BUY
            else "LIFECYCLE_OPEN_POSITION_HELD"
        )
        return TradeLifecycleTransition(
            state,
            state,
            requested,
            DecisionAction.HOLD,
            reason,
            as_of,
        )

    raise ValueError(f"unsupported lifecycle action: {requested}")


__all__ = [
    "ExitStage",
    "PositionState",
    "TradeLifecycleState",
    "TradeLifecycleTransition",
    "transition_trade_lifecycle",
]
