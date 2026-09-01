from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.context.permissions import GateState, PermissionEnvelope, PermittedSide

from .conflict import ConflictAssessment, ConflictState
from .coverage import CoverageAssessment, CoverageFamily
from .environment import EnvironmentAssessment, EnvironmentRisk
from .gate_authority import GateAuthority, deferred_permission_blocker_owner
from .opportunity import OpportunityAssessment, OpportunityState
from .stabil_policy import StabilEntryPolicyAssessment, StabilPolicyEffect
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
    stabil_policy: StabilEntryPolicyAssessment | None = None,
) -> EligibilityAssessment:
    """Compose accepted hard gates and soft WAIT conditions hierarchically.

    This function does not produce BUY/SELL. It decides only whether a fresh action
    path is blocked, still waiting, or market-eligible. Supporting weakness is not
    promoted into a structural thesis change. Stabil policy applies only to a fresh
    LONG path; it has no Structure-direction or position-exit authority.
    """

    blockers: list[str] = []
    waiting: list[str] = []
    reasons: list[str] = []

    # G1/G2/G3: Structure remains the hard directional dependency.
    if structural.data_quality is not ContextDataQuality.VALID:
        blockers.append(f"STRUCTURE_DATA_{structural.data_quality.value}")
    if structural.direction is StructuralDirection.UNRESOLVED:
        blockers.append("STRUCTURAL_DIRECTION_UNRESOLVED")
    if structural.thesis_state in {ThesisState.INVALIDATED, ThesisState.UNRESOLVED}:
        blockers.append(f"STRUCTURAL_THESIS_{structural.thesis_state.value}")
    structure_hard_blocked = bool(blockers)

    # G4: Permission remains scope/context only. Some Permission reasons summarize
    # facts whose canonical hard-gate owner is already present in this Decision
    # assessment. Do not count those summaries as a second independent veto.
    expected_permission_side = _permission_side(structural.direction)
    if permission.gate_state is GateState.BLOCKED:
        permission_blockers = tuple(permission.blocking_reasons or ("PERMISSION_BLOCKED",))
        non_context_blockers = tuple(
            item
            for item in permission_blockers
            if deferred_permission_blocker_owner(item) is not GateAuthority.CONFLICT
        )
        authoritative_permission_blockers = tuple(
            item
            for item in non_context_blockers
            if not (
                structure_hard_blocked
                and deferred_permission_blocker_owner(item) is GateAuthority.STRUCTURE
            )
        )
        if authoritative_permission_blockers:
            blockers.extend(authoritative_permission_blockers)
        elif non_context_blockers:
            reasons.append("PERMISSION_STRUCTURE_BLOCK_DEFERRED_TO_STRUCTURE")
        elif "CONTEXT_CONFLICT_HIGH" in permission_blockers:
            reasons.append("CONTEXT_CONFLICT_DEFERRED_TO_INDEPENDENT_FAMILY_GATE")
            waiting.append("CONTEXT_CONFLICT_TO_RECONCILE")
    elif permission.permitted_side not in {expected_permission_side, PermittedSide.NONE}:
        waiting.append("PERMISSION_SCOPE_SIDE_TO_RECONCILE")
    elif permission.permitted_side is PermittedSide.NONE and permission.gate_state in {
        GateState.OPEN,
        GateState.CONDITIONAL,
    }:
        waiting.append("PERMISSION_SIDE_TO_RESOLVE")

    # Stabil is independent fresh-LONG entry evidence. A hard contradiction blocks a
    # new LONG without mutating Structure direction/thesis and without affecting an
    # existing position's exit lifecycle.
    if (
        structural.direction is StructuralDirection.LONG
        and stabil_policy is not None
        and stabil_policy.effect is StabilPolicyEffect.HARD_CONTRADICTION
    ):
        blockers.append("STABIL_LONG_ENTRY_CONTRADICTION")

    # G5/G6/G7.
    if environment.risk is EnvironmentRisk.HARD_BLOCK:
        blockers.append("VOLATILITY_SHOCK")
    if opportunity.state is OpportunityState.NONE:
        blockers.append("OPPORTUNITY_NONE")
    if conflict.state is ConflictState.HIGH:
        blockers.append("INDEPENDENT_FAMILY_CONFLICT_HIGH")

    # Coverage may describe many missing families, but only Structure is a hard
    # dependency at this layer. When Structure already owns the hard failure, its
    # coverage summary remains diagnostic rather than becoming a duplicate blocker.
    # 30m execution availability is handled separately by the execution-trigger
    # contract so missing trigger data remains WAIT, not a false structural NO_TRADE.
    if CoverageFamily.STRUCTURE in coverage.critical_path_missing:
        if structure_hard_blocked:
            reasons.append("CRITICAL_STRUCTURE_COVERAGE_DEFERRED_TO_STRUCTURE")
        else:
            blockers.append("CRITICAL_STRUCTURE_COVERAGE_MISSING")

    if blockers:
        return EligibilityAssessment(
            EligibilityState.BLOCKED,
            tuple(reasons or ("HARD_GATE_ACTIVE",)),
            tuple(dict.fromkeys(blockers)),
            (),
        )

    # An established thesis that is structurally transitioning remains analyzable,
    # but continuation on the old side is not fresh-entry eligible yet.
    if structural.thesis_state is ThesisState.TRANSITIONING:
        waiting.append("STRUCTURAL_TRANSITION_TO_RESOLVE")

    if permission.gate_state is GateState.WAITING:
        waiting.extend(permission.waiting_for or ("PERMISSION_TO_OPEN",))
    # CONDITIONAL is intentionally not a WAIT by itself. Its historical placeholder
    # FUTURE_ACTION_LAYER_TIMING is satisfied here by the explicit timing layer.

    if timing.state is not TimingState.READY:
        waiting.extend(timing.waiting_for or (f"TIMING_{timing.state.value}",))

    if opportunity.state is OpportunityState.COMPRESSED:
        waiting.append("MORE_DIRECTIONAL_ROOM")
    elif opportunity.state is OpportunityState.UNKNOWN:
        waiting.append("OPPORTUNITY_EVIDENCE_OR_CALIBRATION")

    if conflict.state is ConflictState.MATERIAL:
        waiting.append("MATERIAL_CONFLICT_TO_RESOLVE")
    elif conflict.state is ConflictState.UNRESOLVED:
        waiting.append("CONFLICT_EVIDENCE_TO_RESOLVE")

    for family in coverage.critical_path_missing:
        if family is not CoverageFamily.STRUCTURE:
            waiting.append(f"CRITICAL_COVERAGE:{family.value}")

    if (
        structural.direction is StructuralDirection.LONG
        and stabil_policy is not None
        and stabil_policy.effect is StabilPolicyEffect.WAIT
    ):
        waiting.append("STABIL_RECOVERY_TO_CONFIRM")

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
