import pytest

from financial_dashboard.decision.arbiter import (
    ArbiterSelection,
    ArbiterState,
    arbitrate_entry_scenarios,
)
from financial_dashboard.decision.eligibility import EligibilityState
from financial_dashboard.decision.market_state import StructuralRegime
from financial_dashboard.decision.opportunity import OpportunityState
from financial_dashboard.decision.scenario import (
    EntryScenarioAssessment,
    ScenarioKind,
    ScenarioPresence,
    ScenarioStage,
)
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection, ThesisState
from financial_dashboard.decision.target_path import TargetPathStatus


def _scenario(
    horizon,
    presence,
    *,
    stage=ScenarioStage.QUALIFIED,
    direction=StructuralDirection.LONG,
    kind=None,
):
    if presence is ScenarioPresence.UNKNOWN:
        stage = ScenarioStage.UNAVAILABLE
    elif presence is ScenarioPresence.ABSENT:
        stage = ScenarioStage.NOT_APPLICABLE
    if kind is None:
        kind = (
            ScenarioKind.NONE
            if presence is not ScenarioPresence.PRESENT
            else ScenarioKind.CONTINUATION
        )
    return EntryScenarioAssessment(
        horizon=horizon,
        presence=presence,
        stage=stage,
        kind=kind,
        structural_direction=direction,
        thesis_state=ThesisState.INTACT,
        structural_regime=StructuralRegime.DIRECTIONAL,
        opportunity_state=(
            OpportunityState.UNKNOWN
            if presence is ScenarioPresence.UNKNOWN
            else OpportunityState.NONE
            if presence is ScenarioPresence.ABSENT
            else OpportunityState.MODERATE
        ),
        target_path_status=(
            TargetPathStatus.UNKNOWN
            if presence is ScenarioPresence.UNKNOWN
            else TargetPathStatus.READY
        ),
        active_target_identity="T1" if presence is ScenarioPresence.PRESENT else None,
        eligibility_state=(
            EligibilityState.ELIGIBLE
            if stage is ScenarioStage.QUALIFIED
            else EligibilityState.BLOCKED
            if stage is ScenarioStage.BLOCKED
            else EligibilityState.WAITING
        ),
        reasons=(),
        blockers=("BLOCKED",) if stage is ScenarioStage.BLOCKED else (),
        waiting_for=("WAIT",) if stage is ScenarioStage.DEVELOPING else (),
        source_lineage=(f"{horizon.value}-lineage",),
    )


def test_lt_present_and_st_present_selects_lt_only():
    lt = _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT)
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT)

    result = arbitrate_entry_scenarios(lt, st)

    assert result.state is ArbiterState.SELECTED
    assert result.selection is ArbiterSelection.LONG_TERM
    assert result.selected_scenario is lt
    assert result.suppressed_horizons == (DecisionHorizon.SHORT_TERM,)
    assert result.is_actionable_signal is False


def test_lt_developing_st_qualified_falls_back_to_st():
    lt = _scenario(
        DecisionHorizon.LONG_TERM,
        ScenarioPresence.PRESENT,
        stage=ScenarioStage.DEVELOPING,
    )
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT)

    result = arbitrate_entry_scenarios(lt, st)

    assert result.selection is ArbiterSelection.SHORT_TERM
    assert result.selected_scenario.stage is ScenarioStage.QUALIFIED
    assert "SHORT_TERM_FALLBACK_WHILE_LONG_TERM_BLOCKED" in result.reasons
    assert DecisionHorizon.SHORT_TERM not in result.suppressed_horizons


def test_lt_blocked_st_qualified_falls_back_to_st():
    lt = _scenario(
        DecisionHorizon.LONG_TERM,
        ScenarioPresence.PRESENT,
        stage=ScenarioStage.BLOCKED,
    )
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT)

    result = arbitrate_entry_scenarios(lt, st)

    assert result.selection is ArbiterSelection.SHORT_TERM
    assert "LONG_TERM_SCENARIO_BLOCKED_NOT_QUALIFIED" in result.reasons


def test_only_explicit_lt_absence_allows_short_term_fallback():
    lt = _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.ABSENT)
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT)

    result = arbitrate_entry_scenarios(lt, st)

    assert result.state is ArbiterState.SELECTED
    assert result.selection is ArbiterSelection.SHORT_TERM
    assert result.selected_horizon is DecisionHorizon.SHORT_TERM


def test_lt_unknown_falls_back_to_qualified_st():
    lt = _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.UNKNOWN)
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT)

    result = arbitrate_entry_scenarios(lt, st)

    assert result.state is ArbiterState.SELECTED
    assert result.selection is ArbiterSelection.SHORT_TERM
    assert result.selected_horizon is DecisionHorizon.SHORT_TERM
    assert "SHORT_TERM_FALLBACK_WHILE_LONG_TERM_UNRESOLVED" in result.reasons


def test_lt_unknown_waits_when_st_not_qualified():
    lt = _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.UNKNOWN)
    st = _scenario(
        DecisionHorizon.SHORT_TERM,
        ScenarioPresence.PRESENT,
        stage=ScenarioStage.DEVELOPING,
    )

    result = arbitrate_entry_scenarios(lt, st)

    assert result.state is ArbiterState.WAITING_FOR_LONG_TERM_RESOLUTION
    assert result.selection is ArbiterSelection.UNRESOLVED
    assert result.selected_horizon is None
    assert result.suppressed_horizons == (DecisionHorizon.SHORT_TERM,)


def test_lt_absent_and_st_unknown_waits_for_st_resolution():
    lt = _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.ABSENT)
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.UNKNOWN)

    result = arbitrate_entry_scenarios(lt, st)

    assert result.state is ArbiterState.WAITING_FOR_SHORT_TERM_RESOLUTION
    assert result.selection is ArbiterSelection.UNRESOLVED


def test_both_absent_produces_no_scenario():
    lt = _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.ABSENT)
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.ABSENT)

    result = arbitrate_entry_scenarios(lt, st)

    assert result.state is ArbiterState.NO_SCENARIO
    assert result.selection is ArbiterSelection.NONE
    assert result.selected_scenario is None


def test_lt_absent_can_allow_st_even_when_lt_structural_context_is_bearish():
    lt = _scenario(
        DecisionHorizon.LONG_TERM,
        ScenarioPresence.ABSENT,
        direction=StructuralDirection.SHORT,
    )
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT)

    result = arbitrate_entry_scenarios(lt, st)

    assert result.selection is ArbiterSelection.SHORT_TERM
    assert result.short_term.horizon is DecisionHorizon.SHORT_TERM


def test_repeat_is_deterministic_and_never_selects_two_horizons():
    lt = _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT)
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT)

    first = arbitrate_entry_scenarios(lt, st)
    second = arbitrate_entry_scenarios(lt, st)

    assert first == second
    assert first.selected_horizon is DecisionHorizon.LONG_TERM
    assert len(first.suppressed_horizons) == 1


def test_qualified_lt_pullback_defers_to_qualified_st():
    lt = _scenario(
        DecisionHorizon.LONG_TERM,
        ScenarioPresence.PRESENT,
        kind=ScenarioKind.PULLBACK_CONTINUATION,
    )
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT)

    result = arbitrate_entry_scenarios(lt, st)

    assert result.selection is ArbiterSelection.SHORT_TERM
    assert result.selected_horizon is DecisionHorizon.SHORT_TERM
    assert result.selected_scenario is st
    assert "SHORT_TERM_OWNS_PULLBACK_CONTINUATION" in result.reasons
    assert DecisionHorizon.SHORT_TERM not in result.suppressed_horizons


def test_horizon_arguments_are_validated_instead_of_swapped_silently():
    lt = _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT)
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT)

    with pytest.raises(ValueError):
        arbitrate_entry_scenarios(st, lt)
