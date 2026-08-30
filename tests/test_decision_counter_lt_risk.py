from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.conflict import ConflictState
from financial_dashboard.decision.eligibility import EligibilityAssessment, EligibilityState
from financial_dashboard.decision.engine import _apply_counter_lt_st_risk
from financial_dashboard.decision.opportunity import OpportunityState
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection, ThesisState
from financial_dashboard.decision.timing import TimingState


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


def test_counter_lt_does_not_recheck_timing_or_opportunity():
    eligibility = EligibilityAssessment(
        EligibilityState.ELIGIBLE,
        ("BASE_ELIGIBLE",),
        (),
        (),
    )

    result = _apply_counter_lt_st_risk(
        DecisionHorizon.SHORT_TERM,
        _counter_lt_snapshot(),
        _st_long_structure(),
        eligibility,
        timing=SimpleNamespace(state=TimingState.DEVELOPING),
        opportunity=SimpleNamespace(state=OpportunityState.COMPRESSED),
        conflict=SimpleNamespace(state=ConflictState.LOW),
    )

    assert result.state is EligibilityState.ELIGIBLE
    assert "COUNTER_LT_ST_REQUIRES_CONFIRMED_1H_SETUP" not in result.waiting_for
    assert "COUNTER_LT_ST_REQUIRES_CLEAR_DIRECTIONAL_ROOM" not in result.waiting_for
    assert "COUNTER_LT_ST_RISK_ACCEPTED_AS_CONTEXT" in result.reasons


def test_counter_lt_keeps_independent_conflict_guard():
    eligibility = EligibilityAssessment(
        EligibilityState.ELIGIBLE,
        ("BASE_ELIGIBLE",),
        (),
        (),
    )

    result = _apply_counter_lt_st_risk(
        DecisionHorizon.SHORT_TERM,
        _counter_lt_snapshot(),
        _st_long_structure(),
        eligibility,
        timing=SimpleNamespace(state=TimingState.READY),
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
        timing=SimpleNamespace(state=TimingState.READY),
        opportunity=SimpleNamespace(state=OpportunityState.MODERATE),
        conflict=SimpleNamespace(state=ConflictState.LOW),
    )

    assert result.state is EligibilityState.WAITING
    assert result.waiting_for == ("TARGET_PATH_TO_RESOLVE",)
