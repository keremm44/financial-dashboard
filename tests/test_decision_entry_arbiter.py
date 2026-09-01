import pytest

from financial_dashboard.decision.arbiter import (
    ArbiterSelection,
    ArbiterState,
    arbitrate_entry_scenarios,
)
from financial_dashboard.decision.eligibility import EligibilityAssessment, EligibilityState
from financial_dashboard.decision.entry_qualification import EntryQualificationAssessment
from financial_dashboard.decision.market_state import StructuralRegime
from financial_dashboard.decision.opportunity import OpportunityState
from financial_dashboard.decision.scenario import (
    EntryScenarioAssessment,
    ScenarioKind,
    ScenarioPresence,
    ScenarioStage,
)
from financial_dashboard.decision.st_ownership import STEconomicOwnership
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection, ThesisState
from financial_dashboard.decision.target_path import TargetPathStatus


def _scenario(
    horizon,
    presence,
    *,
    stage=ScenarioStage.QUALIFIED,
    direction=StructuralDirection.LONG,
):
    if presence is ScenarioPresence.UNKNOWN:
        stage = ScenarioStage.UNAVAILABLE
    elif presence is ScenarioPresence.ABSENT:
        stage = ScenarioStage.NOT_APPLICABLE

    eligibility = EligibilityAssessment(
        (
            EligibilityState.ELIGIBLE
            if stage is ScenarioStage.QUALIFIED
            else EligibilityState.BLOCKED
            if stage is ScenarioStage.BLOCKED
            else EligibilityState.WAITING
        ),
        (),
        ("BLOCKED",) if stage is ScenarioStage.BLOCKED else (),
        ("WAIT",) if stage is ScenarioStage.DEVELOPING else (),
    )
    target_path_status = (
        TargetPathStatus.UNKNOWN
        if presence is ScenarioPresence.UNKNOWN
        else TargetPathStatus.READY
    )
    qualification = (
        EntryQualificationAssessment(
            state=stage,
            eligibility=eligibility,
            target_path_status=target_path_status,
            target_path_waiting_for=(),
            reasons=(),
        )
        if presence is ScenarioPresence.PRESENT
        else None
    )
    return EntryScenarioAssessment(
        horizon=horizon,
        presence=presence,
        kind=(
            ScenarioKind.NONE
            if presence is not ScenarioPresence.PRESENT
            else ScenarioKind.CONTINUATION
        ),
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
        target_path_status=target_path_status,
        active_target_identity="T1" if presence is ScenarioPresence.PRESENT else None,
        eligibility=eligibility,
        qualification=qualification,
        reasons=(),
        presence_waiting_for=(),
        source_lineage=(f"{horizon.value}-lineage",),
    )


def test_both_qualified_independent_st_gets_product_priority():
    lt = _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT)
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT)

    result = arbitrate_entry_scenarios(
        lt,
        st,
        short_term_ownership=STEconomicOwnership.INDEPENDENT_ST,
    )

    assert result.state is ArbiterState.SELECTED
    assert result.selection is ArbiterSelection.SHORT_TERM
    assert result.selected_scenario is st
    assert result.suppressed_horizons == (DecisionHorizon.LONG_TERM,)
    assert "SHORT_TERM_INDEPENDENT_PRODUCT_PRIORITY" in result.reasons
    assert result.is_actionable_signal is False


def test_both_qualified_lt_timing_only_stays_long_term_owned():
    lt = _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT)
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT)

    result = arbitrate_entry_scenarios(
        lt,
        st,
        short_term_ownership=STEconomicOwnership.LT_TIMING_ONLY,
    )

    assert result.selection is ArbiterSelection.LONG_TERM
    assert result.selected_scenario is lt
    assert result.suppressed_horizons == (DecisionHorizon.SHORT_TERM,)
    assert "SHORT_TERM_IS_LONG_TERM_TIMING_NOT_INDEPENDENT_PRODUCT" in result.reasons


def test_both_qualified_unresolved_st_cannot_claim_product_priority():
    lt = _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT)
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT)

    result = arbitrate_entry_scenarios(lt, st)

    assert result.selection is ArbiterSelection.LONG_TERM
    assert "SHORT_TERM_INDEPENDENCE_UNRESOLVED" in result.reasons


def test_independent_qualified_st_bypasses_developing_lt():
    lt = _scenario(
        DecisionHorizon.LONG_TERM,
        ScenarioPresence.PRESENT,
        stage=ScenarioStage.DEVELOPING,
    )
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT)

    result = arbitrate_entry_scenarios(
        lt,
        st,
        short_term_ownership=STEconomicOwnership.INDEPENDENT_ST,
    )

    assert result.selection is ArbiterSelection.SHORT_TERM
    assert result.selected_scenario is st
    assert result.suppressed_horizons == (DecisionHorizon.LONG_TERM,)


def test_timing_only_qualified_st_does_not_bypass_developing_lt():
    lt = _scenario(
        DecisionHorizon.LONG_TERM,
        ScenarioPresence.PRESENT,
        stage=ScenarioStage.DEVELOPING,
    )
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT)

    result = arbitrate_entry_scenarios(
        lt,
        st,
        short_term_ownership=STEconomicOwnership.LT_TIMING_ONLY,
    )

    assert result.selection is ArbiterSelection.LONG_TERM
    assert result.selected_scenario is lt


def test_independent_qualified_st_bypasses_blocked_lt():
    lt = _scenario(
        DecisionHorizon.LONG_TERM,
        ScenarioPresence.PRESENT,
        stage=ScenarioStage.BLOCKED,
    )
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT)

    result = arbitrate_entry_scenarios(
        lt,
        st,
        short_term_ownership=STEconomicOwnership.INDEPENDENT_ST,
    )

    assert result.selection is ArbiterSelection.SHORT_TERM
    assert result.selected_scenario is st
    assert result.suppressed_horizons == (DecisionHorizon.LONG_TERM,)


def test_independent_qualified_st_can_proceed_while_lt_presence_is_unknown():
    lt = _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.UNKNOWN)
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT)

    result = arbitrate_entry_scenarios(
        lt,
        st,
        short_term_ownership=STEconomicOwnership.INDEPENDENT_ST,
    )

    assert result.state is ArbiterState.SELECTED
    assert result.selection is ArbiterSelection.SHORT_TERM
    assert result.selected_horizon is DecisionHorizon.SHORT_TERM
    assert result.waiting_for == ()
    assert "LONG_TERM_UNKNOWN_DOES_NOT_VETO_INDEPENDENT_SHORT_TERM" in result.reasons


def test_unresolved_qualified_st_does_not_bypass_unknown_lt():
    lt = _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.UNKNOWN)
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT)

    result = arbitrate_entry_scenarios(lt, st)

    assert result.state is ArbiterState.WAITING_FOR_LONG_TERM_RESOLUTION
    assert result.selection is ArbiterSelection.UNRESOLVED
    assert result.selected_horizon is None
    assert result.suppressed_horizons == (DecisionHorizon.SHORT_TERM,)
    assert "SHORT_TERM_INDEPENDENCE_NOT_PROVEN" in result.reasons


def test_nonqualified_st_does_not_bypass_unknown_lt():
    lt = _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.UNKNOWN)
    st = _scenario(
        DecisionHorizon.SHORT_TERM,
        ScenarioPresence.PRESENT,
        stage=ScenarioStage.DEVELOPING,
    )

    result = arbitrate_entry_scenarios(
        lt,
        st,
        short_term_ownership=STEconomicOwnership.INDEPENDENT_ST,
    )

    assert result.state is ArbiterState.WAITING_FOR_LONG_TERM_RESOLUTION
    assert result.selection is ArbiterSelection.UNRESOLVED
    assert result.selected_horizon is None
    assert result.suppressed_horizons == (DecisionHorizon.SHORT_TERM,)


def test_when_neither_is_qualified_present_lt_retains_nonaction_ownership():
    lt = _scenario(
        DecisionHorizon.LONG_TERM,
        ScenarioPresence.PRESENT,
        stage=ScenarioStage.DEVELOPING,
    )
    st = _scenario(
        DecisionHorizon.SHORT_TERM,
        ScenarioPresence.PRESENT,
        stage=ScenarioStage.DEVELOPING,
    )

    result = arbitrate_entry_scenarios(
        lt,
        st,
        short_term_ownership=STEconomicOwnership.INDEPENDENT_ST,
    )

    assert result.selection is ArbiterSelection.LONG_TERM
    assert result.selected_scenario is lt
    assert result.suppressed_horizons == (DecisionHorizon.SHORT_TERM,)


def test_explicit_lt_absence_keeps_short_term_fallback_for_nonqualified_st():
    lt = _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.ABSENT)
    st = _scenario(
        DecisionHorizon.SHORT_TERM,
        ScenarioPresence.PRESENT,
        stage=ScenarioStage.DEVELOPING,
    )

    result = arbitrate_entry_scenarios(lt, st)

    assert result.state is ArbiterState.SELECTED
    assert result.selection is ArbiterSelection.SHORT_TERM
    assert result.selected_horizon is DecisionHorizon.SHORT_TERM
    assert result.selected_scenario.stage is ScenarioStage.DEVELOPING


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
    lt = _scenario(
        DecisionHorizon.LONG_TERM,
        ScenarioPresence.PRESENT,
        stage=ScenarioStage.DEVELOPING,
    )
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT)

    first = arbitrate_entry_scenarios(
        lt,
        st,
        short_term_ownership=STEconomicOwnership.INDEPENDENT_ST,
    )
    second = arbitrate_entry_scenarios(
        lt,
        st,
        short_term_ownership=STEconomicOwnership.INDEPENDENT_ST,
    )

    assert first == second
    assert first.selected_horizon is DecisionHorizon.SHORT_TERM
    assert len(first.suppressed_horizons) == 1


def test_horizon_arguments_are_validated_instead_of_swapped_silently():
    lt = _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT)
    st = _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT)

    with pytest.raises(ValueError):
        arbitrate_entry_scenarios(st, lt)
