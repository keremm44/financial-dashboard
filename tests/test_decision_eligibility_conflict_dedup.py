from __future__ import annotations

from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.context.permissions import (
    GateState,
    PermissionEnvelope,
    PermissionScope,
    PermittedSide,
)
from financial_dashboard.context.projections import (
    StructuralFactsProjection,
    StructuralTimeframeProjection,
)
from financial_dashboard.decision.conflict import ConflictState
from financial_dashboard.decision.eligibility import EligibilityState, assess_eligibility
from financial_dashboard.decision.environment import EnvironmentRisk
from financial_dashboard.decision.opportunity import OpportunityState
from financial_dashboard.decision.structural import StructuralDirection, ThesisState
from financial_dashboard.decision.structure_projection import normalize_decision_structure_projection
from financial_dashboard.decision.timing import TimingState


def _structural():
    return SimpleNamespace(
        data_quality=ContextDataQuality.VALID,
        direction=StructuralDirection.LONG,
        thesis_state=ThesisState.INTACT,
    )


def _permission(*, blocked_reason: str | None = None):
    if blocked_reason is not None:
        return PermissionEnvelope(
            scope=PermissionScope.NONE,
            permitted_side=PermittedSide.NONE,
            gate_state=GateState.BLOCKED,
            blocking_reasons=(blocked_reason,),
        )
    return PermissionEnvelope(
        scope=PermissionScope.CONTINUATION_ONLY,
        permitted_side=PermittedSide.LONG,
        gate_state=GateState.OPEN,
    )


def _kwargs(*, permission, conflict_state: ConflictState):
    return dict(
        permission=permission,
        timing=SimpleNamespace(state=TimingState.READY, waiting_for=()),
        opportunity=SimpleNamespace(state=OpportunityState.AMPLE),
        conflict=SimpleNamespace(state=conflict_state),
        environment=SimpleNamespace(risk=EnvironmentRisk.NORMAL),
        coverage=SimpleNamespace(critical_path_missing=()),
    )


def test_decision_structure_treats_generic_limited_quality_as_price_usable() -> None:
    row = StructuralTimeframeProjection(
        timeframe="1h",
        as_of=None,
        data_quality=ContextDataQuality.DATA_LIMITED,
        external=None,
        internal=None,
        events=(),
    )
    source = StructuralFactsProjection(
        symbol="ASELS",
        timeframes=("1h",),
        timeframe_facts=(row,),
    )

    normalized = normalize_decision_structure_projection(source)

    assert source.for_timeframe("1h").data_quality is ContextDataQuality.DATA_LIMITED
    assert normalized.for_timeframe("1h").data_quality is ContextDataQuality.VALID


def test_context_conflict_high_is_not_a_second_hard_veto() -> None:
    result = assess_eligibility(
        _structural(),
        **_kwargs(
            permission=_permission(blocked_reason="CONTEXT_CONFLICT_HIGH"),
            conflict_state=ConflictState.NONE,
        ),
    )

    assert result.state is EligibilityState.WAITING
    assert result.blockers == ()
    assert "CONTEXT_CONFLICT_DEFERRED_TO_INDEPENDENT_FAMILY_GATE" in result.reasons
    assert "CONTEXT_CONFLICT_TO_RECONCILE" in result.waiting_for


def test_independent_family_high_remains_a_hard_gate() -> None:
    result = assess_eligibility(
        _structural(),
        **_kwargs(
            permission=_permission(),
            conflict_state=ConflictState.HIGH,
        ),
    )

    assert result.state is EligibilityState.BLOCKED
    assert "INDEPENDENT_FAMILY_CONFLICT_HIGH" in result.blockers


def test_other_permission_blocks_remain_hard_gates() -> None:
    result = assess_eligibility(
        _structural(),
        **_kwargs(
            permission=_permission(blocked_reason="CANONICAL_STRUCTURE_UNRESOLVED"),
            conflict_state=ConflictState.NONE,
        ),
    )

    assert result.state is EligibilityState.BLOCKED
    assert "CANONICAL_STRUCTURE_UNRESOLVED" in result.blockers
