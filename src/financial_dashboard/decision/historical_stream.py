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
from .lifecycle import TradeLifecycleState, TradeLifecycleTransition, transition_trade_lifecycle
from .opportunity import OpportunityCalibration
from .structural import DecisionHorizon, StructuralDirection


@dataclass(frozen=True, slots=True)
class HistoricalDecisionStreamConfig:
    """Configuration for the cheap decision-only historical pass.

    Native/domain engines are deliberately out of scope here. The caller must supply
    a causal, strictly increasing stream of already-built ``DecisionInputSnapshot``
    objects from a single-pass upstream replay. This prevents the decision audit from
    accidentally rebuilding the full market workspace once per historical bar.

    ``enforce_trade_lifecycle`` folds final market decisions through the same
    persistent FLAT/OPEN ownership contract intended for production. Pass 1 preserves
    the existing SELL candidate semantics; the dedicated exit assessor will replace
    that candidate in a later pass.
    """

    horizon: DecisionHorizon = DecisionHorizon.SHORT_TERM
    opportunity_calibration: OpportunityCalibration | None = None
    readiness_position_proxy: bool = False
    enforce_trade_lifecycle: bool = True
    action_policy: ActionPolicy = ActionPolicy(
        permitted_sides=(StructuralDirection.LONG, StructuralDirection.SHORT)
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
    lifecycle: TradeLifecycleTransition | None = None,
) -> DecisionEvent:
    final = assessment.final
    action = action_override or _audit_action(final.action)
    extra_reasons: tuple[str, ...] = ()
    if proxy_reason is not None:
        extra_reasons += (proxy_reason,)
    if lifecycle is not None:
        extra_reasons += (lifecycle.reason,)
    reasons = (*final.reasons, *extra_reasons)

    waiting_for = tuple(final.waiting_for)
    if lifecycle is not None and lifecycle.action is DecisionAction.WAIT and not waiting_for:
        waiting_for = ("LIFECYCLE_LONG_ENTRY_PATH",)

    snapshot = {
        "historical_stream": True,
        "readiness_position_proxy": proxy_reason is not None,
        "trade_lifecycle": None
        if lifecycle is None
        else {
            "previous_position": lifecycle.previous.position.value,
            "position_state": lifecycle.current.position.value,
            "previous_exit_stage": None
            if lifecycle.previous.exit_stage is None
            else lifecycle.previous.exit_stage.value,
            "exit_stage": None
            if lifecycle.current.exit_stage is None
            else lifecycle.current.exit_stage.value,
            "trade_id": lifecycle.current.trade_id,
            "entry_as_of": lifecycle.current.entry_as_of,
            "requested_action": lifecycle.requested_action.value,
            "action": lifecycle.action.value,
            "transition_reason": lifecycle.reason,
            "changed_position": lifecycle.changed_position,
        },
        "horizon": assessment.horizon.value,
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
        waiting_for=waiting_for,
        source_lineage=tuple(final.source_lineage),
        snapshot=snapshot,
    )


def assess_snapshot_stream(
    snapshots: Iterable[DecisionInputSnapshot],
    *,
    config: HistoricalDecisionStreamConfig | None = None,
    execution_events: Mapping[Any, ExecutionTriggerEvent] | None = None,
) -> tuple[tuple[HorizonDecisionAssessment, float], ...]:
    """Evaluate the decision layer over one causal snapshot stream.

    This function is intentionally O(number of decision snapshots) over the decision
    layer only. It never loads cache files, clips OHLCV, imports the workspace runner,
    or reruns any native market engine.
    """

    cfg = config or HistoricalDecisionStreamConfig()
    engine_config = DecisionEngineConfig(
        opportunity_calibration=cfg.opportunity_calibration,
        action_policy=cfg.action_policy,
    )
    event_map = execution_events or {}
    previous_as_of: Any | None = None
    rows: list[tuple[HorizonDecisionAssessment, float]] = []

    for snapshot in snapshots:
        if previous_as_of is not None and snapshot.as_of <= previous_as_of:
            raise ValueError("historical decision snapshots must be strictly increasing")
        for ref in snapshot.source_refs:
            if not ref.is_available_at(snapshot.as_of):
                raise ValueError("historical decision snapshot contains future-unavailable evidence")
        assessment = assess_horizon_decision(
            snapshot,
            cfg.horizon,
            config=engine_config,
            execution_event=event_map.get(snapshot.as_of),
        )
        rows.append((assessment, float(snapshot.current_price)))
        previous_as_of = snapshot.as_of
    return tuple(rows)


def apply_trade_lifecycle(
    assessments: Iterable[tuple[HorizonDecisionAssessment, float]],
) -> tuple[DecisionEvent, ...]:
    """Fold causal decisions through one persistent long-only trade lifecycle."""

    state = TradeLifecycleState()
    events: list[DecisionEvent] = []
    for assessment, price in assessments:
        lifecycle = transition_trade_lifecycle(
            state,
            assessment.final,
            as_of=assessment.as_of,
        )
        state = lifecycle.current
        events.append(
            _event_from_assessment(
                assessment,
                price=price,
                action_override=_audit_action(lifecycle.action),
                lifecycle=lifecycle,
            )
        )
    return tuple(events)


def apply_readiness_position_proxy(
    assessments: Iterable[tuple[HorizonDecisionAssessment, float]],
) -> tuple[DecisionEvent, ...]:
    """Turn READY side transitions into a long-only audit proxy.

    This is legacy hindsight-test plumbing only; it is not the production execution
    trigger and it is bypassed by the real lifecycle fold unless explicitly enabled.
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
    if cfg.enforce_trade_lifecycle:
        return apply_trade_lifecycle(assessments)
    return tuple(
        _event_from_assessment(assessment, price=price)
        for assessment, price in assessments
    )


__all__ = [
    "HistoricalDecisionStreamConfig",
    "apply_readiness_position_proxy",
    "apply_trade_lifecycle",
    "assess_snapshot_stream",
    "decision_events_from_snapshot_stream",
]
