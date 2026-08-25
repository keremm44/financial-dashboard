from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .eligibility import EligibilityAssessment, EligibilityState
from .execution import ExecutionTriggerAssessment, ExecutionTriggerState
from .position import PositionContext, PositionSide, position_exit_candidate
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
    position_before: PositionSide = PositionSide.FLAT
    position_after: PositionSide = PositionSide.FLAT


def _action_side(side: StructuralDirection) -> ActionSide:
    if side is StructuralDirection.LONG:
        return ActionSide.LONG
    if side is StructuralDirection.SHORT:
        return ActionSide.SHORT
    return ActionSide.NONE


def _position_action_side(side: PositionSide) -> ActionSide:
    if side is PositionSide.LONG:
        return ActionSide.LONG
    if side is PositionSide.SHORT:
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


def _position_hold(
    structural: StructuralAssessment,
    *,
    eligibility: EligibilityAssessment,
    execution: ExecutionTriggerAssessment,
    position: PositionContext,
    reasons: tuple[str, ...],
    waiting_for: tuple[str, ...] = (),
    lineage: tuple[str, ...],
) -> FinalDecision:
    return FinalDecision(
        horizon=structural.horizon,
        market_side=structural.direction,
        action_side=_position_action_side(position.side),
        action=DecisionAction.HOLD,
        eligibility=eligibility.state,
        execution_trigger=execution.state,
        reasons=reasons,
        blockers=(),
        waiting_for=waiting_for,
        source_lineage=lineage,
        position_before=position.side,
        position_after=position.side,
    )


def _compose_open_position(
    structural: StructuralAssessment,
    *,
    eligibility: EligibilityAssessment,
    execution: ExecutionTriggerAssessment,
    position: PositionContext,
    lineage: tuple[str, ...],
) -> FinalDecision:
    """Compose HOLD/exit for an already-open position.

    Fresh-entry gates such as opportunity compression, short-entry brokerage
    capability, or a new-position volatility gate do not become forced exit rules.
    Position management is instead Structure-led and requires the v1 execution
    channel before an opposite action is emitted.
    """

    exit_side = position_exit_candidate(structural, position)
    if exit_side is None:
        if structural.direction is StructuralDirection.UNRESOLVED:
            return _position_hold(
                structural,
                eligibility=eligibility,
                execution=execution,
                position=position,
                reasons=("OPEN_POSITION_STRUCTURE_UNRESOLVED",),
                waiting_for=("STRUCTURAL_POSITION_RISK_RESOLUTION",),
                lineage=lineage,
            )
        return _position_hold(
            structural,
            eligibility=eligibility,
            execution=execution,
            position=position,
            reasons=(f"OPEN_POSITION_{position.side.value}_THESIS_NOT_EXIT_ELIGIBLE",),
            lineage=lineage,
        )

    if execution.side is not exit_side:
        raise ValueError("position exit execution side must match Structure-owned exit side")

    if execution.state is ExecutionTriggerState.CONFIRMED:
        action = (
            DecisionAction.SELL
            if position.side is PositionSide.LONG
            else DecisionAction.BUY
        )
        return FinalDecision(
            horizon=structural.horizon,
            market_side=structural.direction,
            action_side=ActionSide.NONE,
            action=action,
            eligibility=eligibility.state,
            execution_trigger=execution.state,
            reasons=(
                f"POSITION_{position.side.value}_EXIT_CONFIRMED",
                f"EXIT_STRUCTURE_SIDE:{exit_side.value}",
            ),
            blockers=(),
            waiting_for=(),
            source_lineage=lineage,
            position_before=position.side,
            position_after=PositionSide.FLAT,
        )

    if execution.state is ExecutionTriggerState.UNAVAILABLE:
        waiting = (f"{execution.timeframe}:POSITION_EXIT_TRIGGER_DATA",)
    elif execution.state is ExecutionTriggerState.FAILED:
        waiting = ("NEW_POSITION_EXIT_EVENT",)
    else:
        waiting = ("FRESH_POSITION_EXIT_EVENT",)

    return _position_hold(
        structural,
        eligibility=eligibility,
        execution=execution,
        position=position,
        reasons=(
            f"POSITION_{position.side.value}_EXIT_PATH_ACTIVE",
            f"EXIT_STRUCTURE_SIDE:{exit_side.value}",
            *execution.reasons,
        ),
        waiting_for=waiting,
        lineage=lineage,
    )


def compose_final_decision(
    structural: StructuralAssessment,
    *,
    eligibility: EligibilityAssessment,
    execution: ExecutionTriggerAssessment,
    policy: ActionPolicy | None = None,
    additional_lineage: tuple[str, ...] = (),
    position: PositionContext | None = None,
) -> FinalDecision:
    """Compose entry actions or position-management actions after evidence is frozen.

    Flat-state BUY/SELL preserves the original v1 rule: a fresh CONFIRMED execution
    event is required after market eligibility. With an open position, ``HOLD`` is a
    real position-management state and an opposite BUY/SELL means closing exposure,
    not silently opening a new opposite position.
    """

    cfg = policy or ActionPolicy()
    current_position = position or PositionContext.flat()
    market_side = structural.direction
    lineage = tuple(
        sorted(
            set(additional_lineage)
            | set(_lineage(structural.source_refs))
            | set(_lineage(execution.source_refs))
        )
    )

    if current_position.side is not PositionSide.FLAT:
        return _compose_open_position(
            structural,
            eligibility=eligibility,
            execution=execution,
            position=current_position,
            lineage=lineage,
        )

    if eligibility.state is EligibilityState.BLOCKED:
        return FinalDecision(
            horizon=structural.horizon,
            market_side=market_side,
            action_side=ActionSide.NONE,
            action=DecisionAction.NO_TRADE,
            eligibility=eligibility.state,
            execution_trigger=execution.state,
            reasons=eligibility.reasons,
            blockers=eligibility.blockers,
            waiting_for=(),
            source_lineage=lineage,
        )

    if market_side not in cfg.permitted_sides:
        return FinalDecision(
            horizon=structural.horizon,
            market_side=market_side,
            action_side=ActionSide.NONE,
            action=DecisionAction.NO_TRADE,
            eligibility=eligibility.state,
            execution_trigger=execution.state,
            reasons=("MARKET_SIDE_VALID_BUT_ACTION_CAPABILITY_DISALLOWS_SIDE",),
            blockers=(f"ACTION_SIDE_NOT_PERMITTED:{market_side.value}",),
            waiting_for=(),
            source_lineage=lineage,
        )

    side = _action_side(market_side)
    if eligibility.state is EligibilityState.WAITING:
        return FinalDecision(
            horizon=structural.horizon,
            market_side=market_side,
            action_side=side,
            action=DecisionAction.WAIT,
            eligibility=eligibility.state,
            execution_trigger=execution.state,
            reasons=eligibility.reasons,
            blockers=(),
            waiting_for=eligibility.waiting_for,
            source_lineage=lineage,
        )

    if execution.state is ExecutionTriggerState.UNAVAILABLE:
        return FinalDecision(
            horizon=structural.horizon,
            market_side=market_side,
            action_side=side,
            action=DecisionAction.WAIT,
            eligibility=eligibility.state,
            execution_trigger=execution.state,
            reasons=("MARKET_ELIGIBLE_EXECUTION_DATA_UNAVAILABLE",),
            blockers=(),
            waiting_for=(f"{execution.timeframe}:EXECUTION_TRIGGER_DATA",),
            source_lineage=lineage,
        )
    if execution.state is ExecutionTriggerState.FAILED:
        return FinalDecision(
            horizon=structural.horizon,
            market_side=market_side,
            action_side=side,
            action=DecisionAction.WAIT,
            eligibility=eligibility.state,
            execution_trigger=execution.state,
            reasons=("MARKET_ELIGIBLE_EXECUTION_EVENT_FAILED",),
            blockers=(),
            waiting_for=("NEW_EXECUTION_EVENT",),
            source_lineage=lineage,
        )
    if execution.state is ExecutionTriggerState.ABSENT:
        return FinalDecision(
            horizon=structural.horizon,
            market_side=market_side,
            action_side=side,
            action=DecisionAction.READY,
            eligibility=eligibility.state,
            execution_trigger=execution.state,
            reasons=("MARKET_ELIGIBLE_AWAITING_FRESH_EXECUTION_EVENT",),
            blockers=(),
            waiting_for=("FRESH_EXECUTION_EVENT",),
            source_lineage=lineage,
        )

    action = (
        DecisionAction.BUY
        if market_side is StructuralDirection.LONG
        else DecisionAction.SELL
    )
    position_after = (
        PositionSide.LONG
        if action is DecisionAction.BUY
        else PositionSide.SHORT
    )
    return FinalDecision(
        horizon=structural.horizon,
        market_side=market_side,
        action_side=side,
        action=action,
        eligibility=eligibility.state,
        execution_trigger=execution.state,
        reasons=("MARKET_ELIGIBLE_AND_FRESH_EXECUTION_EVENT_CONFIRMED",),
        blockers=(),
        waiting_for=(),
        source_lineage=lineage,
        position_before=PositionSide.FLAT,
        position_after=position_after,
    )


__all__ = [
    "ActionPolicy",
    "ActionSide",
    "DecisionAction",
    "FinalDecision",
    "compose_final_decision",
]
