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
        stage,
        PositionHealth.PRESSURED if stage is not ExitStage.MONITOR else PositionHealth.HEALTHY,
        ("STRUCTURE_REASON",),
        ("FRESH_LONG_EXIT_EXECUTION_EVENT",) if stage is ExitStage.EXIT_READY else (),
        (),
    )


def _st(*, direction, thesis_state, transition_target=None):
    return SimpleNamespace(
        direction=direction,
        thesis_state=thesis_state,
        transition_target=transition_target,
    )


def _stabil(state: StabilDecisionState) -> StabilDecisionAssessment:
    return StabilDecisionAssessment(
        state,
        ContextDataQuality.VALID,
        (f"TEST_STABIL:{state.value}",),
        (),
    )


def test_structure_exit_ready_is_not_downgraded_when_stabil_is_balance() -> None:
    result = refine_short_term_exit_with_stabil(
        _assessment(ExitStage.EXIT_READY),
        _st(direction=StructuralDirection.SHORT, thesis_state=ThesisState.INTACT),
        _stabil(StabilDecisionState.BALANCE),
    )

    assert result.stage is ExitStage.EXIT_READY
    assert "STABIL_BREAKDOWN_CONFIRMATION" not in result.waiting_for
    assert "STABIL_NEUTRAL_CONTEXT_NO_EXIT_VETO:BALANCE" in result.reasons


def test_structure_exit_ready_is_not_downgraded_when_stabil_is_still_bullish() -> None:
    result = refine_short_term_exit_with_stabil(
        _assessment(ExitStage.EXIT_READY),
        _st(
            direction=StructuralDirection.LONG,
            thesis_state=ThesisState.TRANSITIONING,
            transition_target=StructuralDirection.SHORT,
        ),
        _stabil(StabilDecisionState.BULLISH_SUPPORTED),
    )

    assert result.stage is ExitStage.EXIT_READY
    assert "STABIL_BREAKDOWN_CONFIRMATION" not in result.waiting_for
    assert "STABIL_STILL_SUPPORTIVE_BUT_NO_EXIT_VETO:BULLISH_SUPPORTED" in result.reasons


def test_stabil_breakdown_confirms_but_does_not_create_extra_exit_requirement() -> None:
    result = refine_short_term_exit_with_stabil(
        _assessment(ExitStage.EXIT_READY),
        _st(direction=StructuralDirection.SHORT, thesis_state=ThesisState.INTACT),
        _stabil(StabilDecisionState.BREAKDOWN_CONFIRMED),
    )

    assert result.stage is ExitStage.EXIT_READY
    assert result.waiting_for == ("FRESH_LONG_EXIT_EXECUTION_EVENT",)
    assert "STABIL_CONFIRMS_ST_EXIT:BREAKDOWN_CONFIRMED" in result.reasons


def test_stabil_softening_can_raise_watch_but_cannot_arm_sell() -> None:
    result = refine_short_term_exit_with_stabil(
        _assessment(ExitStage.MONITOR),
        _st(direction=StructuralDirection.LONG, thesis_state=ThesisState.INTACT),
        _stabil(StabilDecisionState.BULLISH_SOFTENING),
    )

    assert result.stage is ExitStage.EXIT_WATCH
    assert "STABIL_BULLISH_FOUNDATION_SOFTENING" in result.reasons
    assert "ST_STRUCTURE_DETERIORATION" in result.waiting_for


def test_stabil_breakdown_ahead_of_structure_is_watch_only() -> None:
    result = refine_short_term_exit_with_stabil(
        _assessment(ExitStage.MONITOR),
        _st(direction=StructuralDirection.LONG, thesis_state=ThesisState.INTACT),
        _stabil(StabilDecisionState.BREAKDOWN_CONFIRMED),
    )

    assert result.stage is ExitStage.EXIT_WATCH
    assert "STABIL_BREAKDOWN_AHEAD_OF_STRUCTURE:BREAKDOWN_CONFIRMED" in result.reasons
    assert "ST_STRUCTURE_DETERIORATION" in result.waiting_for
