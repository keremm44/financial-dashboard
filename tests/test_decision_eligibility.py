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
from financial_dashboard.decision.stabil_policy import StabilPolicyEffect
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection, ThesisState
from financial_dashboard.decision.timing import TimingEntryEffect, TimingState


def _structural(
    *,
    horizon=DecisionHorizon.LONG_TERM,
    direction=StructuralDirection.LONG,
    thesis=ThesisState.INTACT,
    quality=ContextDataQuality.VALID,
):
    return SimpleNamespace(
        horizon=horizon,
        direction=direction,
        thesis_state=thesis,
        data_quality=quality,
    )


def _permission(*, gate=GateState.OPEN, side=PermittedSide.LONG):
    return PermissionEnvelope(
        scope=PermissionScope.CONTINUATION_ONLY,
        permitted_side=side,
        gate_state=gate,
        blocking_reasons=("TEST_BLOCK",) if gate is GateState.BLOCKED else (),
        waiting_for=("TEST_PERMISSION_WAIT",) if gate is GateState.WAITING else (),
    )


def _timing(state=TimingState.READY, *, effect=None):
    if effect is None:
        effect = (
            TimingEntryEffect.SUPPORTIVE
            if state is TimingState.READY
            else TimingEntryEffect.UNKNOWN
        )
    return SimpleNamespace(
        state=state,
        entry_effect=effect,
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


def test_permission_side_mismatch_waits_instead_of_hard_blocking():
    result = _assess(permission=_permission(gate=GateState.CONDITIONAL, side=PermittedSide.SHORT))
    assert result.state is EligibilityState.WAITING
    assert result.blockers == ()
    assert "PERMISSION_SCOPE_SIDE_TO_RECONCILE" in result.waiting_for


def test_permission_open_with_unresolved_side_waits_instead_of_hard_blocking():
    result = _assess(permission=_permission(gate=GateState.OPEN, side=PermittedSide.NONE))
    assert result.state is EligibilityState.WAITING
    assert result.blockers == ()
    assert "PERMISSION_SIDE_TO_RESOLVE" in result.waiting_for


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


def test_lt_developing_timing_remains_wait():
    result = _assess(timing=_timing(TimingState.DEVELOPING, effect=TimingEntryEffect.NEUTRAL))
    assert result.state is EligibilityState.WAITING


def test_st_neutral_developing_timing_does_not_veto_eligibility():
    result = _assess(
        structural=_structural(horizon=DecisionHorizon.SHORT_TERM),
        timing=_timing(TimingState.DEVELOPING, effect=TimingEntryEffect.NEUTRAL),
    )
    assert result.state is EligibilityState.ELIGIBLE
    assert result.waiting_for == ()


def test_st_neutral_early_timing_does_not_veto_eligibility():
    result = _assess(
        structural=_structural(horizon=DecisionHorizon.SHORT_TERM),
        timing=_timing(TimingState.EARLY, effect=TimingEntryEffect.NEUTRAL),
    )
    assert result.state is EligibilityState.ELIGIBLE


def test_st_unavailable_timing_is_not_trading_evidence_or_veto():
    result = _assess(
        structural=_structural(horizon=DecisionHorizon.SHORT_TERM),
        timing=_timing(TimingState.UNAVAILABLE, effect=TimingEntryEffect.UNKNOWN),
    )
    assert result.state is EligibilityState.ELIGIBLE


def test_st_adverse_timing_defers_current_entry_attempt():
    result = _assess(
        structural=_structural(horizon=DecisionHorizon.SHORT_TERM),
        timing=_timing(TimingState.EARLY, effect=TimingEntryEffect.ADVERSE),
    )
    assert result.state is EligibilityState.WAITING
    assert "WAIT_EARLY" in result.waiting_for


def test_st_failed_timing_defers_current_entry_attempt():
    result = _assess(
        structural=_structural(horizon=DecisionHorizon.SHORT_TERM),
        timing=_timing(TimingState.FAILED, effect=TimingEntryEffect.FAILED),
    )
    assert result.state is EligibilityState.WAITING
    assert "WAIT_FAILED" in result.waiting_for


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


def test_stabil_hard_contradiction_blocks_fresh_long_only():
    policy = SimpleNamespace(effect=StabilPolicyEffect.HARD_CONTRADICTION)
    result = _assess(stabil_policy=policy)

    assert result.state is EligibilityState.BLOCKED
    assert result.blockers == ("STABIL_LONG_ENTRY_CONTRADICTION",)

    short = _assess(
        structural=_structural(direction=StructuralDirection.SHORT),
        permission=_permission(side=PermittedSide.SHORT),
        stabil_policy=policy,
    )
    assert short.state is EligibilityState.ELIGIBLE


def test_stabil_wait_holds_fresh_long_without_hard_blocking():
    result = _assess(stabil_policy=SimpleNamespace(effect=StabilPolicyEffect.WAIT))

    assert result.state is EligibilityState.WAITING
    assert result.blockers == ()
    assert result.waiting_for == ("STABIL_RECOVERY_TO_CONFIRM",)


def test_stabil_risk_context_does_not_rejudge_clean_entry():
    result = _assess(stabil_policy=SimpleNamespace(effect=StabilPolicyEffect.RISK_CONTEXT))

    assert result.state is EligibilityState.ELIGIBLE
    assert result.blockers == ()
    assert result.waiting_for == ()
