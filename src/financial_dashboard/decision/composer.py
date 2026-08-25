from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .eligibility import EligibilityAssessment, EligibilityState
from .execution import ExecutionTriggerAssessment, ExecutionTriggerState
from .structural import DecisionHorizon, StructuralAssessment, StructuralDirection


class DecisionAction(StrEnum):
    WAIT = "WAIT"
    READY = "READY"
    BUY = "BUY"
    SELL = "SELL"
    NO_TRADE = "NO_TRADE"
    HOLD = "HOLD"


class ActionSide(StrEnum):
    NONE = "NONE"
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True, slots=True)
class ActionPolicy:
    """Thin brokerage/action-capability policy, separate from market assessment."""

    permitted_sides: tuple[StructuralDirection, ...] = (StructuralDirection.LONG,)

    def __post_init__(self) -> None:
        if not self.permitted_sides:
            raise ValueError("action policy must permit at least one directional side")
        if any(side is StructuralDirection.UNRESOLVED for side in self.permitted_sides):
            raise ValueError("action policy cannot permit UNRESOLVED")
        if len(set(self.permitted_sides)) != len(self.permitted_sides):
            raise ValueError("action policy permitted_sides must be unique")


@dataclass(frozen=True, slots=True)
class FinalDecision:
    horizon: DecisionHorizon
    market_side: StructuralDirection
    action_side: ActionSide
    action: DecisionAction
    eligibility: EligibilityState
    execution_trigger: ExecutionTriggerState
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    waiting_for: tuple[str, ...]
    source_lineage: tuple[str, ...] = ()


def _action_side(side: StructuralDirection) -> ActionSide:
    if side is StructuralDirection.LONG:
        return ActionSide.LONG
    if side is StructuralDirection.SHORT:
        return ActionSide.SHORT
    return ActionSide.NONE


def _lineage(refs) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                ref.lineage_id or f"{ref.domain.value}:{ref.timeframe}:{ref.native_id}"
                for ref in refs
            }
        )
    )


def compose_final_decision(
    structural: StructuralAssessment,
    *,
    eligibility: EligibilityAssessment,
    execution: ExecutionTriggerAssessment,
    policy: ActionPolicy | None = None,
    additional_lineage: tuple[str, ...] = (),
) -> FinalDecision:
    """Compose WAIT/READY/BUY/SELL/NO_TRADE after eligibility is frozen.

    BUY/SELL requires a fresh CONFIRMED execution event. A valid channel with no
    event produces READY, while unavailable trigger data produces WAIT. HOLD remains
    reserved for future position-aware logic and is never emitted here.
    """

    cfg = policy or ActionPolicy()
    market_side = structural.direction
    lineage = tuple(
        sorted(
            set(additional_lineage)
            | set(_lineage(structural.source_refs))
            | set(_lineage(execution.source_refs))
        )
    )

    if eligibility.state is EligibilityState.BLOCKED:
        return FinalDecision(
            structural.horizon,
            market_side,
            ActionSide.NONE,
            DecisionAction.NO_TRADE,
            eligibility.state,
            execution.state,
            eligibility.reasons,
            eligibility.blockers,
            (),
            lineage,
        )

    if market_side not in cfg.permitted_sides:
        return FinalDecision(
            structural.horizon,
            market_side,
            ActionSide.NONE,
            DecisionAction.NO_TRADE,
            eligibility.state,
            execution.state,
            ("MARKET_SIDE_VALID_BUT_ACTION_CAPABILITY_DISALLOWS_SIDE",),
            (f"ACTION_SIDE_NOT_PERMITTED:{market_side.value}",),
            (),
            lineage,
        )

    side = _action_side(market_side)
    if eligibility.state is EligibilityState.WAITING:
        return FinalDecision(
            structural.horizon,
            market_side,
            side,
            DecisionAction.WAIT,
            eligibility.state,
            execution.state,
            eligibility.reasons,
            (),
            eligibility.waiting_for,
            lineage,
        )

    if execution.state is ExecutionTriggerState.UNAVAILABLE:
        return FinalDecision(
            structural.horizon,
            market_side,
            side,
            DecisionAction.WAIT,
            eligibility.state,
            execution.state,
            ("MARKET_ELIGIBLE_EXECUTION_DATA_UNAVAILABLE",),
            (),
            (f"{execution.timeframe}:EXECUTION_TRIGGER_DATA",),
            lineage,
        )
    if execution.state is ExecutionTriggerState.FAILED:
        return FinalDecision(
            structural.horizon,
            market_side,
            side,
            DecisionAction.WAIT,
            eligibility.state,
            execution.state,
            ("MARKET_ELIGIBLE_EXECUTION_EVENT_FAILED",),
            (),
            ("NEW_EXECUTION_EVENT",),
            lineage,
        )
    if execution.state is ExecutionTriggerState.ABSENT:
        return FinalDecision(
            structural.horizon,
            market_side,
            side,
            DecisionAction.READY,
            eligibility.state,
            execution.state,
            ("MARKET_ELIGIBLE_AWAITING_FRESH_EXECUTION_EVENT",),
            (),
            ("FRESH_EXECUTION_EVENT",),
            lineage,
        )

    action = (
        DecisionAction.BUY
        if market_side is StructuralDirection.LONG
        else DecisionAction.SELL
    )
    return FinalDecision(
        structural.horizon,
        market_side,
        side,
        action,
        eligibility.state,
        execution.state,
        ("MARKET_ELIGIBLE_AND_FRESH_EXECUTION_EVENT_CONFIRMED",),
        (),
        (),
        lineage,
    )


__all__ = [
    "ActionPolicy",
    "ActionSide",
    "DecisionAction",
    "FinalDecision",
    "compose_final_decision",
]
