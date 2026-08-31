from __future__ import annotations

from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.exit import _short_term_position_exit, refine_short_term_exit_with_stabil
from financial_dashboard.decision.lifecycle import ExitStage
from financial_dashboard.decision.stabil_authority import StabilDecisionAssessment, StabilDecisionState
from financial_dashboard.decision.structural import StructuralDirection, ThesisState


def _snapshot(*, direction, thesis_state, transition_target=None):
    short_term = SimpleNamespace(
        data_quality=ContextDataQuality.VALID,
        direction=direction,
        thesis_state=thesis_state,
        transition_target=transition_target,
        source_refs=(),
    )
    return SimpleNamespace(short_term=short_term)


def _stabil(state: StabilDecisionState) -> StabilDecisionAssessment:
    return StabilDecisionAssessment(
        state,
        ContextDataQuality.VALID,
        (f"TEST_STABIL:{state.value}",),
        (),
    )


def test_established_short_side_during_transition_arms_short_term_exit() -> None:
    snapshot = _snapshot(
        direction=StructuralDirection.SHORT,
        thesis_state=ThesisState.TRANSITIONING,
        transition_target=StructuralDirection.LONG,
    )

    result = _short_term_position_exit(snapshot)

    assert result.stage is ExitStage.EXIT_READY
    assert result.waiting_for == ("FRESH_LONG_EXIT_EXECUTION_EVENT",)
    assert "ST_ESTABLISHED_SIDE_SHORT_BUT_TRANSITIONING" in result.reasons


def test_bullish_stabil_does_not_downgrade_new_short_term_exit_arm() -> None:
    snapshot = _snapshot(
        direction=StructuralDirection.SHORT,
        thesis_state=ThesisState.TRANSITIONING,
        transition_target=StructuralDirection.LONG,
    )
    structural = _short_term_position_exit(snapshot)

    result = refine_short_term_exit_with_stabil(
        structural,
        snapshot.short_term,
        _stabil(StabilDecisionState.RECOVERY_CONFIRMED),
    )

    assert result.stage is ExitStage.EXIT_READY
    assert result.waiting_for == ("FRESH_LONG_EXIT_EXECUTION_EVENT",)
    assert "STABIL_STILL_SUPPORTIVE_BUT_NO_EXIT_VETO:RECOVERY_CONFIRMED" in result.reasons


def test_intact_short_term_long_remains_monitor_only() -> None:
    result = _short_term_position_exit(
        _snapshot(
            direction=StructuralDirection.LONG,
            thesis_state=ThesisState.INTACT,
        )
    )

    assert result.stage is ExitStage.MONITOR
    assert result.waiting_for == ()
