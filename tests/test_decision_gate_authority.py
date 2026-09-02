from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.context.permissions import (
    GateState,
    PermissionEnvelope,
    PermissionScope,
    PermittedSide,
)
from financial_dashboard.decision.conflict import ConflictState
from financial_dashboard.decision.coverage import CoverageFamily
from financial_dashboard.decision.eligibility import EligibilityState, assess_eligibility
from financial_dashboard.decision.environment import EnvironmentRisk
from financial_dashboard.decision.gate_authority import (
    GateAuthority,
    HARD_GATE_OWNERSHIP,
    deferred_permission_blocker_owner,
)
from financial_dashboard.decision.opportunity import OpportunityState
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection, ThesisState
from financial_dashboard.decision.timing import TimingState


def test_hard_gate_rules_have_one_declared_owner():
    rules = [item.rule for item in HARD_GATE_OWNERSHIP]

    assert len(rules) == len(set(rules))
    assert {item.owner for item in HARD_GATE_OWNERSHIP} == {
        GateAuthority.STRUCTURE,
        GateAuthority.PERMISSION,
        GateAuthority.STABIL,
        GateAuthority.ENVIRONMENT,
        GateAuthority.OPPORTUNITY,
        GateAuthority.CONFLICT,
        GateAuthority.COVERAGE,
    }


def test_permission_summary_reasons_defer_to_canonical_decision_owners():
    assert deferred_permission_blocker_owner("CANONICAL_STRUCTURE_UNRESOLVED") is GateAuthority.STRUCTURE
    assert deferred_permission_blocker_owner("CONTEXT_CONFLICT_HIGH") is GateAuthority.CONFLICT
    assert deferred_permission_blocker_owner("REVERSAL_DIRECTION_UNRESOLVED") is None
    assert deferred_permission_blocker_owner("PERMISSION_BLOCKED") is None


def _ready_inputs():
    return dict(
        timing=SimpleNamespace(state=TimingState.READY, waiting_for=()),
        opportunity=SimpleNamespace(state=OpportunityState.MODERATE),
        conflict=SimpleNamespace(state=ConflictState.NONE),
        environment=SimpleNamespace(risk=EnvironmentRisk.NORMAL),
        coverage=SimpleNamespace(critical_path_missing=()),
    )


def test_structure_owned_failures_are_not_counted_again_as_permission_or_coverage_vetoes():
    structural = SimpleNamespace(
        horizon=DecisionHorizon.LONG_TERM,
        data_quality=ContextDataQuality.UNAVAILABLE,
        direction=StructuralDirection.UNRESOLVED,
        thesis_state=ThesisState.UNRESOLVED,
    )
    permission = PermissionEnvelope(
        scope=PermissionScope.NONE,
        permitted_side=PermittedSide.NONE,
        gate_state=GateState.BLOCKED,
        blocking_reasons=("CANONICAL_STRUCTURE_UNRESOLVED",),
    )
    inputs = _ready_inputs()
    inputs["coverage"] = SimpleNamespace(critical_path_missing=(CoverageFamily.STRUCTURE,))

    result = assess_eligibility(structural, permission=permission, **inputs)

    assert result.state is EligibilityState.BLOCKED
    assert "STRUCTURE_DATA_UNAVAILABLE" in result.blockers
    assert "STRUCTURAL_DIRECTION_UNRESOLVED" in result.blockers
    assert "STRUCTURAL_THESIS_UNRESOLVED" in result.blockers
    assert "CANONICAL_STRUCTURE_UNRESOLVED" not in result.blockers
    assert "CRITICAL_STRUCTURE_COVERAGE_MISSING" not in result.blockers


def test_context_conflict_disagreement_keeps_existing_wait_behavior():
    structural = SimpleNamespace(
        horizon=DecisionHorizon.LONG_TERM,
        data_quality=ContextDataQuality.VALID,
        direction=StructuralDirection.LONG,
        thesis_state=ThesisState.INTACT,
    )
    permission = PermissionEnvelope(
        scope=PermissionScope.NONE,
        permitted_side=PermittedSide.NONE,
        gate_state=GateState.BLOCKED,
        blocking_reasons=("CONTEXT_CONFLICT_HIGH",),
    )

    result = assess_eligibility(structural, permission=permission, **_ready_inputs())

    assert result.state is EligibilityState.WAITING
    assert result.blockers == ()
    assert result.waiting_for == ("CONTEXT_CONFLICT_TO_RECONCILE",)
