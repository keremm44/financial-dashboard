from __future__ import annotations

from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.exit import refine_short_term_exit_with_stabil
from financial_dashboard.decision.lifecycle import ExitStage
from financial_dashboard.decision.stabil_authority import (
    StabilDecisionAssessment,
    StabilDecisionState,
)
from financial_dashboard.decision.structural import StructuralDirection, ThesisState
from financial_dashboard.decision.trade_exit import LongExitAssessment, PositionHealth


def _assessment(stage: ExitStage) -> LongExitAssessment:
    return LongExitAssessment(
        stage=stage,
        position_health=(
            PositionHealth.HEALTHY if stage is ExitStage.MONITOR else PositionHealth.PRESSURED
        ),
        reasons=("BASE",),
        waiting_for=(() if stage is ExitStage.MONITOR else ("FRESH_LONG_EXIT_EXECUTION_EVENT",)),
        source_refs=(),
    )


def _stabil(state: StabilDecisionState) -> StabilDecisionAssessment:
    return StabilDecisionAssessment(
        state=state,
        data_quality=ContextDataQuality.VALID,
        reasons=(f"STABIL_PRIMARY_STATE:{state.value}",),
        source_refs=(),
    )


def test_softening_only_raises_exit_watch_not_exit_ready() -> None:
    st = SimpleNamespace(
        direction=StructuralDirection.LONG,
        thesis_state=ThesisState.INTACT,
        transition_target=None,
    )

    result = refine_short_term_exit_with_stabil(
        _assessment(ExitStage.MONITOR),
        st,
        _stabil(StabilDecisionState.BULLISH_SOFTENING),
    )

    assert result.stage is ExitStage.EXIT_WATCH
    assert result.position_health is PositionHealth.PRESSURED
    assert "STABIL_BULLISH_FOUNDATION_SOFTENING" in result.reasons


def test_breakdown_ahead_of_structure_is_watch_only() -> None:
    st = SimpleNamespace(
        direction=StructuralDirection.LONG,
        thesis_state=ThesisState.INTACT,
        transition_target=None,
    )

    result = refine_short_term_exit_with_stabil(
        _assessment(ExitStage.MONITOR),
        st,
        _stabil(StabilDecisionState.BREAKDOWN_CONFIRMED),
    )

    assert result.stage is ExitStage.EXIT_WATCH
    assert "ST_STRUCTURE_DETERIORATION" in result.waiting_for


def test_structure_deterioration_without_stabil_confirmation_is_not_exit_ready() -> None:
    st = SimpleNamespace(
        direction=StructuralDirection.LONG,
        thesis_state=ThesisState.TRANSITIONING,
        transition_target=StructuralDirection.SHORT,
    )

    result = refine_short_term_exit_with_stabil(
        _assessment(ExitStage.EXIT_READY),
        st,
        _stabil(StabilDecisionState.BREAKDOWN_DEVELOPING),
    )

    assert result.stage is ExitStage.EXIT_WATCH
    assert "STABIL_BREAKDOWN_CONFIRMATION" in result.waiting_for


def test_structure_deterioration_plus_confirmed_stabil_arms_exit_ready() -> None:
    st = SimpleNamespace(
        direction=StructuralDirection.LONG,
        thesis_state=ThesisState.TRANSITIONING,
        transition_target=StructuralDirection.SHORT,
    )

    result = refine_short_term_exit_with_stabil(
        _assessment(ExitStage.EXIT_READY),
        st,
        _stabil(StabilDecisionState.BREAKDOWN_CONFIRMED),
    )

    assert result.stage is ExitStage.EXIT_READY
    assert result.waiting_for == ("FRESH_LONG_EXIT_EXECUTION_EVENT",)
    assert "STABIL_CONFIRMS_ST_EXIT:BREAKDOWN_CONFIRMED" in result.reasons


def test_bearish_structure_is_not_enough_when_stabil_is_still_bullish() -> None:
    st = SimpleNamespace(
        direction=StructuralDirection.SHORT,
        thesis_state=ThesisState.INTACT,
        transition_target=None,
    )

    result = refine_short_term_exit_with_stabil(
        _assessment(ExitStage.EXIT_READY),
        st,
        _stabil(StabilDecisionState.BULLISH_SUPPORTED),
    )

    assert result.stage is ExitStage.EXIT_WATCH
    assert "STABIL_BREAKDOWN_CONFIRMATION" in result.waiting_for


def test_structural_invalidation_remains_exit_ready_even_if_stabil_recovers() -> None:
    st = SimpleNamespace(
        direction=StructuralDirection.LONG,
        thesis_state=ThesisState.INVALIDATED,
        transition_target=None,
    )

    result = refine_short_term_exit_with_stabil(
        _assessment(ExitStage.EXIT_READY),
        st,
        _stabil(StabilDecisionState.RECOVERY_CONFIRMED),
    )

    assert result.stage is ExitStage.EXIT_READY
    assert result.waiting_for == ("FRESH_LONG_EXIT_EXECUTION_EVENT",)
