from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.conflict import ConflictState
from financial_dashboard.decision.eligibility import EligibilityAssessment, EligibilityState
from financial_dashboard.decision.engine import _apply_counter_lt_st_risk
from financial_dashboard.decision.opportunity import OpportunityState
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection, ThesisState


def _counter_lt_snapshot():
    return SimpleNamespace(
        long_term=SimpleNamespace(
            direction=StructuralDirection.SHORT,
            thesis_state=ThesisState.INTACT,
            data_quality=ContextDataQuality.VALID,
        )
    )


def _st_long_structure():
    return SimpleNamespace(direction=StructuralDirection.LONG)


def _eligible():
    return EligibilityAssessment(
        EligibilityState.ELIGIBLE,
        ("BASE_ELIGIBLE",),
        (),
        (),
    )


def test_counter_lt_does_not_recheck_timing_but_requires_usable_room():
    result = _apply_counter_lt_st_risk(
        DecisionHorizon.SHORT_TERM,
        _counter_lt_snapshot(),
        _st_long_structure(),
        _eligible(),
        opportunity=SimpleNamespace(state=OpportunityState.COMPRESSED),
        conflict=SimpleNamespace(state=ConflictState.LOW),
    )

    assert result.state is EligibilityState.WAITING
    assert result.waiting_for == ("COUNTER_LT_ST_REQUIRES_USABLE_DIRECTIONAL_ROOM",)
    assert "COUNTER_LT_ST_REQUIRES_CONFIRMED_1H_SETUP" not in result.waiting_for


def test_counter_lt_accepts_moderate_or_ample_room_without_extra_setup_gate():
    for opportunity_state in (OpportunityState.MODERATE, OpportunityState.AMPLE):
        result = _apply_counter_lt_st_risk(
            DecisionHorizon.SHORT_TERM,
            _counter_lt_snapshot(),
            _st_long_structure(),
            _eligible(),
            opportunity=SimpleNamespace(state=opportunity_state),
            conflict=SimpleNamespace(state=ConflictState.LOW),
        )

        assert result.state is EligibilityState.ELIGIBLE
        assert result.waiting_for == ()
        assert "COUNTER_LT_ST_RISK_ACCEPTED_WITH_USABLE_ROOM" in result.reasons


def test_counter_lt_keeps_independent_conflict_guard():
    result = _apply_counter_lt_st_risk(
        DecisionHorizon.SHORT_TERM,
        _counter_lt_snapshot(),
        _st_long_structure(),
        _eligible(),
        opportunity=SimpleNamespace(state=OpportunityState.AMPLE),
        conflict=SimpleNamespace(state=ConflictState.MATERIAL),
    )

    assert result.state is EligibilityState.WAITING
    assert result.waiting_for == ("COUNTER_LT_ST_REQUIRES_LOW_CONFLICT",)


def test_counter_lt_preserves_existing_base_waits():
    eligibility = EligibilityAssessment(
        EligibilityState.WAITING,
        ("BASE_WAIT",),
        (),
        ("TARGET_PATH_TO_RESOLVE",),
    )

    result = _apply_counter_lt_st_risk(
        DecisionHorizon.SHORT_TERM,
        _counter_lt_snapshot(),
        _st_long_structure(),
        eligibility,
        opportunity=SimpleNamespace(state=OpportunityState.MODERATE),
        conflict=SimpleNamespace(state=ConflictState.LOW),
    )

    assert result.state is EligibilityState.WAITING
    assert result.waiting_for == ("TARGET_PATH_TO_RESOLVE",)


def test_counter_lt_room_guard_does_not_apply_when_long_term_is_not_bearish_intact():
    snapshot = SimpleNamespace(
        long_term=SimpleNamespace(
            direction=StructuralDirection.LONG,
            thesis_state=ThesisState.INTACT,
            data_quality=ContextDataQuality.VALID,
        )
    )

    result = _apply_counter_lt_st_risk(
        DecisionHorizon.SHORT_TERM,
        snapshot,
        _st_long_structure(),
        _eligible(),
        opportunity=SimpleNamespace(state=OpportunityState.COMPRESSED),
        conflict=SimpleNamespace(state=ConflictState.LOW),
    )

    assert result.state is EligibilityState.ELIGIBLE
    assert result.waiting_for == ()
