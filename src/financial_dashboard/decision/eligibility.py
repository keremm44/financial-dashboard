from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.context.permissions import GateState, PermissionEnvelope, PermittedSide

from .conflict import ConflictAssessment, ConflictState
from .coverage import CoverageAssessment, CoverageFamily
from .environment import EnvironmentAssessment, EnvironmentRisk
from .opportunity import OpportunityAssessment, OpportunityState
from .reaction import ReactionAssessment
from .structural import StructuralAssessment, StructuralDirection, ThesisState
from .timing import TimingAssessment, TimingState


class EligibilityState(StrEnum):
    BLOCKED = "BLOCKED"
    WAITING = "WAITING"
    ELIGIBLE = "ELIGIBLE"


@dataclass(frozen=True, slots=True)
class EligibilityAssessment:
    state: EligibilityState
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    waiting_for: tuple[str, ...]


def _permission_side(side: StructuralDirection) -> PermittedSide:
    if side is StructuralDirection.LONG:
        return PermittedSide.LONG
    if side is StructuralDirection.SHORT:
        return PermittedSide.SHORT
    return PermittedSide.NONE


def assess_eligibility(
    structural: StructuralAssessment,
    *,
    permission: PermissionEnvelope,
    timing: TimingAssessment,
    opportunity: OpportunityAssessment,
    conflict: ConflictAssessment,
    environment: EnvironmentAssessment,
    coverage: CoverageAssessment,
    reaction: "ReactionAssessment | None" = None,
) -> EligibilityAssessment:
    """Compose hard gates and WAIT conditions without letting FORMING execute.

    ``DEVELOPING`` means a setup exists and should be watched, not that it is ready
    for a fresh trade. Only ``TimingState.READY`` is armed strongly enough to soften
    same-zone opportunity/context conflicts and reach ELIGIBLE.
    """

    blockers: list[str] = []
    waiting: list[str] = []
    reasons: list[str] = []
    armed = timing.state is TimingState.READY

    if structural.data_quality is not ContextDataQuality.VALID:
        blockers.append(f"STRUCTURE_DATA_{structural.data_quality.value}")
    if structural.direction is StructuralDirection.UNRESOLVED:
        blockers.append("STRUCTURAL_DIRECTION_UNRESOLVED")
    if structural.thesis_state in {ThesisState.INVALIDATED, ThesisState.UNRESOLVED}:
        blockers.append(f"STRUCTURAL_THESIS_{structural.thesis_state.value}")

    expected_permission_side = _permission_side(structural.direction)
    if permission.gate_state is GateState.BLOCKED:
        permission_blockers = tuple(permission.blocking_reasons or ("PERMISSION_BLOCKED",))
        non_context_blockers = tuple(
            item for item in permission_blockers if item != "CONTEXT_CONFLICT_HIGH"
        )
        if non_context_blockers:
            blockers.extend(non_context_blockers)
        elif "CONTEXT_CONFLICT_HIGH" in permission_blockers:
            reasons.append("CONTEXT_CONFLICT_DEFERRED_TO_INDEPENDENT_FAMILY_GATE")
            if not armed:
                waiting.append("CONTEXT_CONFLICT_TO_RECONCILE")
    elif permission.permitted_side not in {expected_permission_side, PermittedSide.NONE}:
        if armed:
            reasons.append("PERMISSION_SCOPE_SIDE_ARMED_SOFT")
        else:
            waiting.append("PERMISSION_SCOPE_SIDE_TO_RECONCILE")
    elif permission.permitted_side is PermittedSide.NONE and permission.gate_state in {
        GateState.OPEN,
        GateState.CONDITIONAL,
    }:
        if armed:
            reasons.append("PERMISSION_SIDE_ARMED_SOFT")
        else:
            waiting.append("PERMISSION_SIDE_TO_RESOLVE")

    if environment.risk is EnvironmentRisk.HARD_BLOCK:
        blockers.append("VOLATILITY_SHOCK")
    if opportunity.state is OpportunityState.NONE:
        if bool(getattr(opportunity, "hard_room_constraint", True)):
            blockers.append("OPPORTUNITY_NONE")
        else:
            reasons.append("SOFT_TECHNICAL_ROOM_CONSTRAINT_NOT_HARD_BLOCK")
    if conflict.state is ConflictState.HIGH:
        blockers.append("INDEPENDENT_FAMILY_CONFLICT_HIGH")

    if CoverageFamily.STRUCTURE in coverage.critical_path_missing:
        blockers.append("CRITICAL_STRUCTURE_COVERAGE_MISSING")

    if blockers:
        return EligibilityAssessment(
            EligibilityState.BLOCKED,
            tuple(reasons or ("HARD_GATE_ACTIVE",)),
            tuple(dict.fromkeys(blockers)),
            (),
        )

    if structural.thesis_state is ThesisState.TRANSITIONING:
        waiting.append("STRUCTURAL_TRANSITION_TO_RESOLVE")

    if permission.gate_state is GateState.WAITING:
        waiting.extend(permission.waiting_for or ("PERMISSION_TO_OPEN",))

    if timing.state is not TimingState.READY:
        if timing.state is TimingState.DEVELOPING:
            reasons.append("SETUP_DEVELOPING_AWAITING_CONFIRMATION")
        waiting.extend(timing.waiting_for or (f"TIMING_{timing.state.value}",))

    if opportunity.state is OpportunityState.COMPRESSED:
        if armed or (reaction is not None and reaction.confirmation_present):
            reasons.append("ROOM_COMPRESSED_AT_PRIMARY_ZONE_DISCOUNT")
        else:
            waiting.append("MORE_DIRECTIONAL_ROOM")
    elif opportunity.state is OpportunityState.UNKNOWN:
        if armed:
            reasons.append("OPPORTUNITY_UNKNOWN_WHILE_ARMED")
        else:
            waiting.append("OPPORTUNITY_EVIDENCE_OR_CALIBRATION")

    if conflict.state is ConflictState.MATERIAL:
        if armed:
            reasons.append("MATERIAL_CONFLICT_ARMED_SOFT")
        else:
            waiting.append("MATERIAL_CONFLICT_TO_RESOLVE")
    elif conflict.state is ConflictState.UNRESOLVED:
        waiting.append("CONFLICT_EVIDENCE_TO_RESOLVE")

    for family in coverage.critical_path_missing:
        if family is not CoverageFamily.STRUCTURE:
            waiting.append(f"CRITICAL_COVERAGE:{family.value}")

    if environment.risk is EnvironmentRisk.ELEVATED:
        reasons.append("ENVIRONMENT_RISK_ELEVATED_SOFT")
    if conflict.state is ConflictState.LOW:
        reasons.append("LOW_CONFLICT_SOFT")

    if waiting:
        return EligibilityAssessment(
            EligibilityState.WAITING,
            tuple(reasons or ("KNOWN_CONDITIONS_INCOMPLETE",)),
            (),
            tuple(dict.fromkeys(waiting)),
        )

    return EligibilityAssessment(
        EligibilityState.ELIGIBLE,
        tuple(reasons or ("ALL_MARKET_ELIGIBILITY_GUARDS_SATISFIED",)),
        (),
        (),
    )


__all__ = ["EligibilityAssessment", "EligibilityState", "assess_eligibility"]
