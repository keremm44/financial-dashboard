from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.context.permissions import GateState, PermissionEnvelope, PermissionScope, PermittedSide
from financial_dashboard.decision.conflict import ConflictState
from financial_dashboard.decision.coverage import CoverageFamily
from financial_dashboard.decision.eligibility import EligibilityState, assess_eligibility
from financial_dashboard.decision.environment import EnvironmentRisk
from financial_dashboard.decision.gate_authority import GateAuthority
from financial_dashboard.decision.gate_registry import gate_owner
from financial_dashboard.decision.opportunity import OpportunityState
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection, ThesisState
from financial_dashboard.decision.timing import TimingState


def _permission_context_high() -> PermissionEnvelope:
    return PermissionEnvelope(
        scope=PermissionScope.NONE,
        permitted_side=PermittedSide.NONE,
        gate_state=GateState.BLOCKED,
        blocking_reasons=("CONTEXT_CONFLICT_HIGH",),
    )


def _inputs():
    return dict(
        timing=SimpleNamespace(state=TimingState.READY, waiting_for=()),
        opportunity=SimpleNamespace(state=OpportunityState.AMPLE),
        conflict=SimpleNamespace(state=ConflictState.NONE),
        environment=SimpleNamespace(risk=EnvironmentRisk.NORMAL),
        coverage=SimpleNamespace(critical_path_missing=()),
    )


def _structural(thesis_state: ThesisState):
    return SimpleNamespace(
        horizon=DecisionHorizon.SHORT_TERM,
        data_quality=ContextDataQuality.VALID,
        direction=StructuralDirection.LONG,
        thesis_state=thesis_state,
    )


def test_context_high_conflict_is_structure_owned_in_registry() -> None:
    assert gate_owner("CONTEXT_CONFLICT_HIGH") is GateAuthority.STRUCTURE
    assert gate_owner("CONTEXT_CONFLICT_TO_RECONCILE") is GateAuthority.STRUCTURE


def test_transitioning_structure_does_not_get_duplicate_context_conflict_wait() -> None:
    result = assess_eligibility(
        _structural(ThesisState.TRANSITIONING),
        permission=_permission_context_high(),
        **_inputs(),
    )

    assert result.state is EligibilityState.WAITING
    assert result.waiting_for == ("STRUCTURAL_TRANSITION_TO_RESOLVE",)
    assert "CONTEXT_CONFLICT_TO_RECONCILE" not in result.waiting_for
    assert "CONTEXT_STRUCTURAL_CONFLICT_ALREADY_OWNED_BY_STRUCTURE" in result.reasons


def test_intact_structure_keeps_current_context_contradiction_wait() -> None:
    result = assess_eligibility(
        _structural(ThesisState.INTACT),
        permission=_permission_context_high(),
        **_inputs(),
    )

    assert result.state is EligibilityState.WAITING
    assert result.waiting_for == ("CONTEXT_CONFLICT_TO_RECONCILE",)
    assert "CURRENT_STRUCTURAL_CONTEXT_CONFLICT_TO_RECONCILE" in result.reasons


def test_phase3_does_not_weaken_independent_material_conflict() -> None:
    inputs = _inputs()
    inputs["conflict"] = SimpleNamespace(state=ConflictState.MATERIAL)
    result = assess_eligibility(
        _structural(ThesisState.INTACT),
        permission=PermissionEnvelope(
            scope=PermissionScope.CONTINUATION_ONLY,
            permitted_side=PermittedSide.LONG,
            gate_state=GateState.OPEN,
        ),
        **inputs,
    )

    assert result.state is EligibilityState.WAITING
    assert "MATERIAL_CONFLICT_TO_RESOLVE" in result.waiting_for
