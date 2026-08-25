from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.context.permissions import (
    GateState,
    PermissionEnvelope,
    PermissionScope,
    PermittedSide,
)
from financial_dashboard.decision.conflict import ConflictState
from financial_dashboard.decision.coverage import CoverageAssessment, CoverageFamily
from financial_dashboard.decision.eligibility import EligibilityState, assess_eligibility
from financial_dashboard.decision.environment import EnvironmentRisk
from financial_dashboard.decision.opportunity import OpportunityState
from financial_dashboard.decision.structural import StructuralDirection, ThesisState
from financial_dashboard.decision.timing import TimingState


def _structural(*, direction=StructuralDirection.LONG, thesis=ThesisState.INTACT, quality=ContextDataQuality.VALID):
    return SimpleNamespace(direction=direction, thesis_state=thesis, data_quality=quality)


def _permission(*, gate=GateState.OPEN, side=PermittedSide.LONG):
    return PermissionEnvelope(
        scope=PermissionScope.CONTINUATION_ONLY,
        permitted_side=side,
        gate_state=gate,
        blocking_reasons=("TEST_BLOCK",) if gate is GateState.BLOCKED else (),
        waiting_for=("TEST_PERMISSION_WAIT",) if gate is GateState.WAITING else (),
    )


def _timing(state=TimingState.READY):
    return SimpleNamespace(
        state=state,
        waiting_for=() if state is TimingState.READY else (f"WAIT_{state.value}",),
    )


def _opportunity(state=OpportunityState.AMPLE):
    return SimpleNamespace(state=state)


def _conflict(state=ConflictState.NONE):
    return SimpleNamespace(state=state)


def _environment(risk=EnvironmentRisk.NORMAL):
    return SimpleNamespace(risk=risk)


def _coverage(*critical):
    return CoverageAssessment(
        valid_fraction=1.0,
        observed_fraction=1.0,
        critical_path_missing=tuple(critical),
        degraded_families=(),
        unavailable_families=(),
        valid_families=(CoverageFamily.STRUCTURE,),
    )


def _assess(**overrides):
    values = dict(
        structural=_structural(),
        permission=_permission(),
        timing=_timing(),
        opportunity=_opportunity(),
        conflict=_conflict(),
        environment=_environment(),
        coverage=_coverage(),
    )
    values.update(overrides)
    structural = values.pop("structural")
    return assess_eligibility(structural, **values)


def test_clean_path_is_eligible():
    assert _assess().state is EligibilityState.ELIGIBLE


def test_permission_conditional_is_resolved_by_explicit_ready_timing():
    result = _assess(permission=_permission(gate=GateState.CONDITIONAL))
    assert result.state is EligibilityState.ELIGIBLE


def test_permission_block_is_hard_gate():
    result = _assess(permission=_permission(gate=GateState.BLOCKED, side=PermittedSide.NONE))
    assert result.state is EligibilityState.BLOCKED
    assert "TEST_BLOCK" in result.blockers


def test_shock_is_hard_gate():
    result = _assess(environment=_environment(EnvironmentRisk.HARD_BLOCK))
    assert result.state is EligibilityState.BLOCKED
    assert "VOLATILITY_SHOCK" in result.blockers


def test_opportunity_none_is_hard_gate_but_compressed_is_wait():
    assert _assess(opportunity=_opportunity(OpportunityState.NONE)).state is EligibilityState.BLOCKED
    compressed = _assess(opportunity=_opportunity(OpportunityState.COMPRESSED))
    assert compressed.state is EligibilityState.WAITING
    assert "MORE_DIRECTIONAL_ROOM" in compressed.waiting_for


def test_high_conflict_blocks_but_material_conflict_waits():
    assert _assess(conflict=_conflict(ConflictState.HIGH)).state is EligibilityState.BLOCKED
    material = _assess(conflict=_conflict(ConflictState.MATERIAL))
    assert material.state is EligibilityState.WAITING
    assert "MATERIAL_CONFLICT_TO_RESOLVE" in material.waiting_for


def test_elevated_environment_is_soft_not_automatic_wait_or_block():
    result = _assess(environment=_environment(EnvironmentRisk.ELEVATED))
    assert result.state is EligibilityState.ELIGIBLE
    assert "ENVIRONMENT_RISK_ELEVATED_SOFT" in result.reasons


def test_developing_timing_is_wait():
    result = _assess(timing=_timing(TimingState.DEVELOPING))
    assert result.state is EligibilityState.WAITING


def test_transitioning_thesis_waits_without_becoming_opposite_direction():
    result = _assess(structural=_structural(thesis=ThesisState.TRANSITIONING))
    assert result.state is EligibilityState.WAITING
    assert "STRUCTURAL_TRANSITION_TO_RESOLVE" in result.waiting_for


def test_missing_structural_coverage_is_hard_gate():
    result = _assess(coverage=_coverage(CoverageFamily.STRUCTURE))
    assert result.state is EligibilityState.BLOCKED


def test_unresolved_conflict_is_wait_not_high_conflict_gate():
    result = _assess(conflict=_conflict(ConflictState.UNRESOLVED))
    assert result.state is EligibilityState.WAITING
    assert "CONFLICT_EVIDENCE_TO_RESOLVE" in result.waiting_for
