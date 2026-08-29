"""Tolerance package T1/T2/T4 — decision-flexibility contract tests.

Market rationale (docs/karar_esnekligi_analiz_ve_plan.md):
- T1: a blocked/developing LT scenario may yield to a QUALIFIED ST scenario. A
  QUALIFIED ST scenario also stands on its own 1H/30m authority when LT context is
  UNKNOWN; only a non-qualified ST keeps waiting for unresolved LT context.
- T2: a lifecycle-completed zone (fully filled / invalidated FVG) is normal
  gap-fill price discovery, not a directional failure; a live failure on a
  secondary lineage while the current path holds a confirmation is LOW, not
  MATERIAL.
- T4: at a confirmed primary zone, compressed room-to-target is the discount
  itself, not a blocker.
- T5 revision: DEVELOPING is observable/armed context, not execution eligibility.
  READY is required before a setup may become ELIGIBLE.
"""

from __future__ import annotations

from types import SimpleNamespace

from financial_dashboard.decision.arbiter import (
    ArbiterSelection,
    ArbiterState,
    arbitrate_entry_scenarios,
)
from financial_dashboard.decision.conflict import (
    ConflictSeverity,
    _reaction_evidence,
)
from financial_dashboard.decision.eligibility import EligibilityState, assess_eligibility
from financial_dashboard.decision.opportunity import OpportunityState
from financial_dashboard.decision.reaction import ReactionState, assess_reaction
from financial_dashboard.decision.scenario import ScenarioPresence, ScenarioStage
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection

from tests.test_decision_entry_arbiter import _scenario
from tests.test_decision_eligibility import (
    _conflict,
    _coverage,
    _environment,
    _opportunity,
    _permission,
    _structural,
    _timing,
)
from tests.test_decision_reaction_relevance import _fvg, _fvg_projection, _scoped, _DEFAULT


# --------------------------------------------------------------------------- #
# T2a: lifecycle completion is not a directional failure                       #
# --------------------------------------------------------------------------- #

def test_full_fill_is_completion_not_failure():
    projection = _fvg_projection((_fvg(full_fill=True, age_hours=3.0),))
    _, scoped_fvg = _scoped(None, projection, _DEFAULT)

    assessment = assess_reaction(
        StructuralDirection.LONG,
        order_blocks=None,
        fvg_engulfing=scoped_fvg,
        timeframes=("1h",),
    )

    assert assessment.failure_present is False
    assert assessment.state is not ReactionState.FAILED
    assert any("FVG_LIFECYCLE_COMPLETED" in reason for reason in assessment.reasons)


def test_invalidated_zone_is_completion_not_failure():
    projection = _fvg_projection((_fvg(invalid=True, age_hours=2.0),))
    _, scoped_fvg = _scoped(None, projection, _DEFAULT)

    assessment = assess_reaction(
        StructuralDirection.LONG,
        order_blocks=None,
        fvg_engulfing=scoped_fvg,
        timeframes=("1h",),
    )

    assert assessment.failure_present is False
    assert assessment.state is not ReactionState.FAILED


def test_live_failed_reaction_still_votes_failure():
    projection = _fvg_projection((_fvg(failed_reaction=True, age_hours=3.0),))
    _, scoped_fvg = _scoped(None, projection, _DEFAULT)

    assessment = assess_reaction(
        StructuralDirection.LONG,
        order_blocks=None,
        fvg_engulfing=scoped_fvg,
        timeframes=("1h",),
    )

    assert assessment.failure_present is True
    assert assessment.state is ReactionState.FAILED


# --------------------------------------------------------------------------- #
# T2b: secondary-lineage failure is LOW while the path is confirmed            #
# --------------------------------------------------------------------------- #

def _reaction(*, failed: bool, confirmed: bool):
    return SimpleNamespace(
        failure_present=failed,
        confirmation_present=confirmed,
        source_refs=(),
    )


def test_failure_without_confirmation_stays_material():
    evidence = _reaction_evidence(_reaction(failed=True, confirmed=False))
    assert evidence.severity is ConflictSeverity.MATERIAL
    assert evidence.reasons == ("REACTION_FAILED",)


def test_secondary_failure_with_confirmation_is_low():
    evidence = _reaction_evidence(_reaction(failed=True, confirmed=True))
    assert evidence.severity is ConflictSeverity.LOW
    assert evidence.reasons == ("REACTION_FAILED_SECONDARY_LINEAGE",)


def test_clean_reaction_is_none():
    evidence = _reaction_evidence(_reaction(failed=False, confirmed=True))
    assert evidence.severity is ConflictSeverity.NONE


# --------------------------------------------------------------------------- #
# T4: compressed room at a confirmed primary zone is a discount, not a gate    #
# --------------------------------------------------------------------------- #

def _assess(*, opportunity_state, reaction):
    return assess_eligibility(
        _structural(),
        permission=_permission(),
        timing=_timing(),
        opportunity=_opportunity(opportunity_state),
        conflict=_conflict(),
        environment=_environment(),
        coverage=_coverage(),
        reaction=reaction,
    )


def test_compressed_room_with_confirmation_is_discounted():
    result = _assess(
        opportunity_state=OpportunityState.COMPRESSED,
        reaction=SimpleNamespace(confirmation_present=True),
    )
    assert result.state is EligibilityState.ELIGIBLE
    assert "MORE_DIRECTIONAL_ROOM" not in result.waiting_for
    assert "ROOM_COMPRESSED_AT_PRIMARY_ZONE_DISCOUNT" in result.reasons


def test_compressed_room_without_confirmation_still_waits():
    from financial_dashboard.decision.timing import TimingState
    from tests.test_decision_eligibility import _timing

    result = assess_eligibility(
        _structural(),
        permission=_permission(),
        timing=_timing(TimingState.EARLY),
        opportunity=_opportunity(OpportunityState.COMPRESSED),
        conflict=_conflict(),
        environment=_environment(),
        coverage=_coverage(),
        reaction=SimpleNamespace(confirmation_present=False),
    )
    assert result.state is EligibilityState.WAITING
    assert "MORE_DIRECTIONAL_ROOM" in result.waiting_for


def test_compressed_room_without_reaction_input_still_waits():
    from financial_dashboard.decision.timing import TimingState
    from tests.test_decision_eligibility import _timing

    result = assess_eligibility(
        _structural(),
        permission=_permission(),
        timing=_timing(TimingState.EARLY),
        opportunity=_opportunity(OpportunityState.COMPRESSED),
        conflict=_conflict(),
        environment=_environment(),
        coverage=_coverage(),
        reaction=None,
    )
    assert result.state is EligibilityState.WAITING
    assert "MORE_DIRECTIONAL_ROOM" in result.waiting_for


# --------------------------------------------------------------------------- #
# T1: qualified ST is independent; developing ST still cannot bypass LT         #
# --------------------------------------------------------------------------- #

def test_qualified_lt_keeps_absolute_priority_over_qualified_st():
    result = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT),
    )
    assert result.selection is ArbiterSelection.LONG_TERM
    assert DecisionHorizon.SHORT_TERM in result.suppressed_horizons


def test_blocked_lt_yields_to_qualified_st():
    result = arbitrate_entry_scenarios(
        _scenario(
            DecisionHorizon.LONG_TERM,
            ScenarioPresence.PRESENT,
            stage=ScenarioStage.BLOCKED,
        ),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT),
    )
    assert result.state is ArbiterState.SELECTED
    assert result.selection is ArbiterSelection.SHORT_TERM
    assert result.selected_horizon is DecisionHorizon.SHORT_TERM
    assert "SHORT_TERM_FALLBACK_WHILE_LONG_TERM_BLOCKED" in result.reasons


def test_structurally_unresolved_lt_yields_to_qualified_st():
    result = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.UNKNOWN),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT),
    )
    assert result.state is ArbiterState.SELECTED
    assert result.selection is ArbiterSelection.SHORT_TERM
    assert result.selected_horizon is DecisionHorizon.SHORT_TERM
    assert "SHORT_TERM_QUALIFIED_INDEPENDENT_OF_LONG_TERM" in result.reasons


def test_structurally_unresolved_lt_still_waits_for_developing_st():
    result = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.UNKNOWN),
        _scenario(
            DecisionHorizon.SHORT_TERM,
            ScenarioPresence.PRESENT,
            stage=ScenarioStage.DEVELOPING,
        ),
    )
    assert result.state is ArbiterState.WAITING_FOR_LONG_TERM_RESOLUTION
    assert result.selection is ArbiterSelection.UNRESOLVED
    assert result.selected_horizon is None


# --------------------------------------------------------------------------- #
# T5 revision: DEVELOPING is armed context, but not execution eligibility       #
# --------------------------------------------------------------------------- #

def test_developing_setup_waits_for_confirmation_and_material_conflict_resolution():
    from financial_dashboard.decision.conflict import ConflictState
    from financial_dashboard.decision.timing import TimingState

    result = assess_eligibility(
        _structural(),
        permission=_permission(),
        timing=_timing(TimingState.DEVELOPING),
        opportunity=_opportunity(OpportunityState.AMPLE),
        conflict=_conflict(ConflictState.MATERIAL),
        environment=_environment(),
        coverage=_coverage(),
    )
    assert result.state is EligibilityState.WAITING
    assert "SETUP_DEVELOPING_AWAITING_CONFIRMATION" in result.reasons
    assert "WAIT_DEVELOPING" in result.waiting_for
    assert "MATERIAL_CONFLICT_TO_RESOLVE" in result.waiting_for


def test_developing_unknown_opportunity_waits_for_confirmation_and_calibration():
    from financial_dashboard.decision.timing import TimingState

    result = assess_eligibility(
        _structural(),
        permission=_permission(),
        timing=_timing(TimingState.DEVELOPING),
        opportunity=_opportunity(OpportunityState.UNKNOWN),
        conflict=_conflict(),
        environment=_environment(),
        coverage=_coverage(),
    )
    assert result.state is EligibilityState.WAITING
    assert "SETUP_DEVELOPING_AWAITING_CONFIRMATION" in result.reasons
    assert "WAIT_DEVELOPING" in result.waiting_for
    assert "OPPORTUNITY_EVIDENCE_OR_CALIBRATION" in result.waiting_for


def test_failed_timing_is_not_armed():
    from financial_dashboard.decision.timing import TimingState

    result = assess_eligibility(
        _structural(),
        permission=_permission(),
        timing=_timing(TimingState.FAILED),
        opportunity=_opportunity(OpportunityState.AMPLE),
        conflict=_conflict(),
        environment=_environment(),
        coverage=_coverage(),
    )
    assert result.state is EligibilityState.WAITING


def test_blocked_lt_keeps_ownership_when_st_not_qualified():
    result = arbitrate_entry_scenarios(
        _scenario(
            DecisionHorizon.LONG_TERM,
            ScenarioPresence.PRESENT,
            stage=ScenarioStage.BLOCKED,
        ),
        _scenario(
            DecisionHorizon.SHORT_TERM,
            ScenarioPresence.PRESENT,
            stage=ScenarioStage.DEVELOPING,
        ),
    )
    assert result.selection is ArbiterSelection.LONG_TERM
