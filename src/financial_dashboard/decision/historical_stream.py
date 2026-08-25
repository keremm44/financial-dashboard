from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

import pandas as pd

from financial_dashboard.decision_audit import DecisionAction as AuditDecisionAction
from financial_dashboard.decision_audit import DecisionEvent, DecisionSide
from financial_dashboard.decision_input import DecisionInputSnapshot

from .composer import ActionPolicy, DecisionAction
from .engine import DecisionEngineConfig, HorizonDecisionAssessment, assess_horizon_decision
from .execution import ExecutionTriggerEvent
from .opportunity import OpportunityCalibration
from .position import PositionContext, PositionSide
from .structural import DecisionHorizon, StructuralDirection


@dataclass(frozen=True, slots=True)
class HistoricalDecisionStreamConfig:
    """Configuration for the cheap decision-only historical pass."""

    horizon: DecisionHorizon = DecisionHorizon.SHORT_TERM
    opportunity_calibration: OpportunityCalibration | None = None
    readiness_position_proxy: bool = False
    position_aware: bool = False
    initial_position: PositionContext = PositionContext()
    action_policy: ActionPolicy = ActionPolicy(
        permitted_sides=(StructuralDirection.LONG, StructuralDirection.SHORT)
    )

    def __post_init__(self) -> None:
        if self.readiness_position_proxy and self.position_aware:
            raise ValueError(
                "readiness_position_proxy and position_aware are distinct audit modes"
            )


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _decision_side(side: StructuralDirection) -> DecisionSide:
    if side is StructuralDirection.LONG:
        return DecisionSide.LONG
    if side is StructuralDirection.SHORT:
        return DecisionSide.SHORT
    return DecisionSide.NONE


def _audit_action(action: DecisionAction) -> AuditDecisionAction:
    return AuditDecisionAction(action.value)


def _event_from_assessment(
    assessment: HorizonDecisionAssessment,
    *,
    price: float,
    action_override: AuditDecisionAction | None = None,
    proxy_reason: str | None = None,
) -> DecisionEvent:
    final = assessment.final
    action = action_override or _audit_action(final.action)
    reasons = final.reasons if proxy_reason is None else (*final.reasons, proxy_reason)
    position = getattr(assessment, "position", PositionContext.flat())
    position_before = getattr(final, "position_before", PositionSide.FLAT)
    position_after = getattr(final, "position_after", PositionSide.FLAT)
    snapshot = {
        "historical_stream": True,
        "readiness_position_proxy": proxy_reason is not None,
        "horizon": assessment.horizon.value,
        "position": _jsonable(position),
        "position_before": position_before.value,
        "position_after": position_after.value,
        "structural": _jsonable(assessment.structural),
        "relation": assessment.structural_snapshot.relation.value,
        "durability": _jsonable(assessment.durability),
        "reaction": _jsonable(assessment.reaction),
        "participation": _jsonable(assessment.participation),
        "environment": _jsonable(assessment.environment),
        "opportunity": _jsonable(assessment.opportunity),
        "coverage": _jsonable(assessment.coverage),
        "conflict": _jsonable(assessment.conflict),
        "timing": _jsonable(assessment.timing),
        "eligibility": _jsonable(assessment.eligibility),
        "execution": _jsonable(assessment.execution),
    }
    return DecisionEvent(
        timestamp=assessment.as_of,
        action=action,
        side=_decision_side(final.market_side),
        price=float(price),
        reasons=tuple(reasons),
        blockers=tuple(final.blockers),
        waiting_for=tuple(final.waiting_for),
        source_lineage=tuple(final.source_lineage),
        snapshot=snapshot,
    )


def _advance_position(
    position: PositionContext,
    assessment: HorizonDecisionAssessment,
    *,
    price: float,
) -> PositionContext:
    """Advance exposure only on actual composed BUY/SELL actions."""

    action = assessment.final.action
    if position.side is PositionSide.FLAT:
        if action is DecisionAction.BUY:
            return PositionContext.long(opened_at=assessment.as_of, entry_price=price)
        if action is DecisionAction.SELL:
            return PositionContext.short(opened_at=assessment.as_of, entry_price=price)
        return position

    if position.side is PositionSide.LONG:
        if action is DecisionAction.SELL:
            return PositionContext.flat()
        if action is DecisionAction.BUY:
            raise ValueError("position-aware stream cannot BUY again while already LONG")
        return position

    if action is DecisionAction.BUY:
        return PositionContext.flat()
    if action is DecisionAction.SELL:
        raise ValueError("position-aware stream cannot SELL again while already SHORT")
    return position


def assess_snapshot_stream(
    snapshots: Iterable[DecisionInputSnapshot],
    *,
    config: HistoricalDecisionStreamConfig | None = None,
    execution_events: Mapping[Any, ExecutionTriggerEvent] | None = None,
) -> tuple[tuple[HorizonDecisionAssessment, float], ...]:
    """Evaluate the decision layer over one causal snapshot stream in O(N)."""

    cfg = config or HistoricalDecisionStreamConfig()
    engine_config = DecisionEngineConfig(
        opportunity_calibration=cfg.opportunity_calibration,
        action_policy=cfg.action_policy,
    )
    event_map = execution_events or {}
    previous_as_of: Any | None = None
    rows: list[tuple[HorizonDecisionAssessment, float]] = []
    current_position = cfg.initial_position

    for snapshot in snapshots:
        if previous_as_of is not None and snapshot.as_of <= previous_as_of:
            raise ValueError("historical decision snapshots must be strictly increasing")
        for ref in snapshot.source_refs:
            if not ref.is_available_at(snapshot.as_of):
                raise ValueError("historical decision snapshot contains future-unavailable evidence")

        if cfg.position_aware:
            assessment = assess_horizon_decision(
                snapshot,
                cfg.horizon,
                config=engine_config,
                execution_event=event_map.get(snapshot.as_of),
                position=current_position,
            )
        else:
            # Preserve the original call surface for the raw causal decision stream.
            assessment = assess_horizon_decision(
                snapshot,
                cfg.horizon,
                config=engine_config,
                execution_event=event_map.get(snapshot.as_of),
            )

        price = float(snapshot.current_price)
        rows.append((assessment, price))
        if cfg.position_aware:
            current_position = _advance_position(
                current_position,
                assessment,
                price=price,
            )
        previous_as_of = snapshot.as_of
    return tuple(rows)


def apply_readiness_position_proxy(
    assessments: Iterable[tuple[HorizonDecisionAssessment, float]],
) -> tuple[DecisionEvent, ...]:
    """Turn READY side transitions into a long-only audit proxy.

    This is hindsight-test plumbing only; it is not the production execution trigger.
    A flat proxy opens on LONG READY and an open long closes on SHORT READY.
    """

    holding_long = False
    events: list[DecisionEvent] = []
    for assessment, price in assessments:
        final = assessment.final
        override: AuditDecisionAction | None = None
        proxy_reason: str | None = None
        if final.action is DecisionAction.READY:
            if not holding_long and final.market_side is StructuralDirection.LONG:
                holding_long = True
                override = AuditDecisionAction.BUY
                proxy_reason = "AUDIT_PROXY_LONG_ENTRY_FROM_READY"
            elif holding_long and final.market_side is StructuralDirection.SHORT:
                holding_long = False
                override = AuditDecisionAction.SELL
                proxy_reason = "AUDIT_PROXY_LONG_EXIT_FROM_OPPOSING_READY"
        events.append(
            _event_from_assessment(
                assessment,
                price=price,
                action_override=override,
                proxy_reason=proxy_reason,
            )
        )
    return tuple(events)


def decision_events_from_snapshot_stream(
    snapshots: Iterable[DecisionInputSnapshot],
    *,
    config: HistoricalDecisionStreamConfig | None = None,
    execution_events: Mapping[Any, ExecutionTriggerEvent] | None = None,
) -> tuple[DecisionEvent, ...]:
    cfg = config or HistoricalDecisionStreamConfig()
    assessments = assess_snapshot_stream(
        snapshots,
        config=cfg,
        execution_events=execution_events,
    )
    if cfg.readiness_position_proxy:
        return apply_readiness_position_proxy(assessments)
    return tuple(
        _event_from_assessment(assessment, price=price)
        for assessment, price in assessments
    )


__all__ = [
    "HistoricalDecisionStreamConfig",
    "apply_readiness_position_proxy",
    "assess_snapshot_stream",
    "decision_events_from_snapshot_stream",
]
