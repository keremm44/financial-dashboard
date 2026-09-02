from types import SimpleNamespace

import financial_dashboard.decision.engine as engine
from financial_dashboard.context.axes import ConflictState, ContextDirection
from financial_dashboard.context.permissions import (
    GateState,
    PermissionEnvelope,
    PermissionScope,
    PermittedSide,
)
from financial_dashboard.decision.structural import DecisionHorizon


_NEUTRAL_WAIT = "QUALIFIED_CONTINUATION_REACTION_OR_TRANSITION_CONTEXT"


def _snapshot():
    return SimpleNamespace(
        structure=object(),
        qualified_zones=None,
        liquidity=None,
        participation=None,
        pattern=None,
        volatility=None,
        ham=None,
    )


def _neutral_permission() -> PermissionEnvelope:
    return PermissionEnvelope(
        scope=PermissionScope.NONE,
        permitted_side=PermittedSide.NONE,
        gate_state=GateState.WAITING,
        waiting_for=(_NEUTRAL_WAIT,),
        source_refs=("structure:1h:test",),
    )


def test_st_neutral_context_does_not_require_extra_positive_confirmation(monkeypatch) -> None:
    axes = SimpleNamespace(
        structural_direction=ContextDirection.UP,
        conflict=ConflictState.LOW,
    )
    monkeypatch.setattr(engine, "evaluate_context_axes", lambda **kwargs: axes)
    monkeypatch.setattr(engine, "resolve_permission_axes", lambda value: _neutral_permission())

    result = engine._horizon_permission(_snapshot(), DecisionHorizon.SHORT_TERM)

    assert result.gate_state is GateState.CONDITIONAL
    assert result.permitted_side is PermittedSide.LONG
    assert result.waiting_for == ()
    assert "NEUTRAL_CONTEXT_DOES_NOT_VETO_SHORT_TERM" in result.allowed_reasons


def test_lt_keeps_existing_neutral_context_wait(monkeypatch) -> None:
    axes = SimpleNamespace(
        structural_direction=ContextDirection.UP,
        conflict=ConflictState.LOW,
    )
    permission = _neutral_permission()
    monkeypatch.setattr(engine, "evaluate_context_axes", lambda **kwargs: axes)
    monkeypatch.setattr(engine, "resolve_permission_axes", lambda value: permission)

    result = engine._horizon_permission(_snapshot(), DecisionHorizon.LONG_TERM)

    assert result == permission


def test_st_material_context_conflict_keeps_wait(monkeypatch) -> None:
    axes = SimpleNamespace(
        structural_direction=ContextDirection.UP,
        conflict=ConflictState.MATERIAL,
    )
    permission = _neutral_permission()
    monkeypatch.setattr(engine, "evaluate_context_axes", lambda **kwargs: axes)
    monkeypatch.setattr(engine, "resolve_permission_axes", lambda value: permission)

    result = engine._horizon_permission(_snapshot(), DecisionHorizon.SHORT_TERM)

    assert result == permission


def test_st_other_permission_waits_are_not_relaxed(monkeypatch) -> None:
    axes = SimpleNamespace(
        structural_direction=ContextDirection.UP,
        conflict=ConflictState.NONE,
    )
    permission = PermissionEnvelope(
        scope=PermissionScope.STRUCTURAL_TRANSITION,
        permitted_side=PermittedSide.LONG,
        gate_state=GateState.WAITING,
        waiting_for=("CANONICAL_STRUCTURAL_FOLLOW_THROUGH",),
    )
    monkeypatch.setattr(engine, "evaluate_context_axes", lambda **kwargs: axes)
    monkeypatch.setattr(engine, "resolve_permission_axes", lambda value: permission)

    result = engine._horizon_permission(_snapshot(), DecisionHorizon.SHORT_TERM)

    assert result == permission
