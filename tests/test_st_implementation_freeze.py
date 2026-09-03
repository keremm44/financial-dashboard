import pandas as pd
import pytest

from financial_dashboard.decision.lifecycle_persistence import (
    CANONICAL_LIFECYCLE_CONTRACT_VERSION,
    TRADE_LIFECYCLE_STATE_SCHEMA_VERSION,
)
from financial_dashboard.decision.st_behavior_validation import (
    STCanonicalBehaviorMetrics,
    STCanonicalBehaviorReport,
)
from financial_dashboard.decision.st_calibration import (
    STExitCalibration,
    STHealthyBaseReactionConfidence,
)
from financial_dashboard.decision.st_implementation_freeze import (
    STCrossRegimeAcceptanceReview,
    STImplementationFreezeStatus,
    STRegimeValidationEvidence,
    ST_IMPLEMENTATION_FREEZE_VERSION,
    assess_st_implementation_freeze,
)
from financial_dashboard.decision.st_thesis_identity import STThesisFamily


def _metrics(*, completed_trade_count: int = 2) -> STCanonicalBehaviorMetrics:
    return STCanonicalBehaviorMetrics(
        completed_trade_count=completed_trade_count,
        profit_harvest_count=completed_trade_count,
        protective_exit_count=0,
        strong_continuation_hold_rows=3,
        healthy_base_hold_rows=2,
        premature_harvest_candidates=0,
        exit_after_healthy_correction_candidates=0,
        same_movement_blocks=1,
        unresolved_continuity_blocks=0,
        novel_setups_released=1,
        novel_setups_executed=1,
        novel_setups_waiting_execution=0,
        novelty_policy_contradictions=0,
        st_reentries_without_novelty=0,
        mean_holding_seconds=3600.0,
        mean_flat_capital_seconds=1800.0,
        open_ended_flat_capital_seconds=0.0,
        mean_harvest_idle_seconds=0.0,
        mean_protective_delay_seconds=0.0,
        mean_giveback_absolute=1.0,
        mean_mfe_return=0.05,
        mean_realized_return=0.03,
    )


def _report(*, source: str = "CANONICAL", production: bool = True, proxy_rows: int = 0, completed_trade_count: int = 2):
    return STCanonicalBehaviorReport(
        source=source,
        production_performance=production,
        row_count=20,
        proxy_row_count=proxy_rows,
        trades=(),
        metrics=_metrics(completed_trade_count=completed_trade_count),
    )


def _evidence(regime_id: str, start: str, end: str) -> STRegimeValidationEvidence:
    return STRegimeValidationEvidence(
        regime_id=regime_id,
        period_start=pd.Timestamp(start),
        period_end=pd.Timestamp(end),
        report=_report(),
    )


def _accepted_review(**overrides) -> STCrossRegimeAcceptanceReview:
    values = dict(
        strong_trends_not_systematically_cut_early=True,
        mature_dead_ranges_not_systematically_held_too_long=True,
        protective_exits_not_systematically_late=True,
        normal_corrections_not_systematically_exited=True,
        same_movement_churn_controlled=True,
        genuine_new_setups_not_systematically_blocked=True,
        review_note="Reviewed canonical Step-11 metrics across distinct historical regimes.",
    )
    values.update(overrides)
    return STCrossRegimeAcceptanceReview(**values)


def test_step13_pins_current_contract_identity_and_default_calibration():
    assert ST_IMPLEMENTATION_FREEZE_VERSION == 1
    assert TRADE_LIFECYCLE_STATE_SCHEMA_VERSION == 6
    assert CANONICAL_LIFECYCLE_CONTRACT_VERSION == 9
    assert set(STThesisFamily) == {
        STThesisFamily.PULLBACK_CONTINUATION,
        STThesisFamily.BREAKOUT_ACCEPTANCE,
        STThesisFamily.FAILED_SELL_RECLAIM,
        STThesisFamily.UNRESOLVED,
    }
    assert STExitCalibration().healthy_base_reaction_confidence is (
        STHealthyBaseReactionConfidence.DEVELOPING_OR_CONFIRMED
    )


def test_freeze_without_real_cross_regime_evidence_fails_closed():
    assessment = assess_st_implementation_freeze()

    assert assessment.status is STImplementationFreezeStatus.VALIDATION_REQUIRED
    assert assessment.production_candidate is False
    assert "freeze/multiple-market-regimes-required" in assessment.release_issues
    assert "freeze/cross-regime-review-required" in assessment.release_issues


def test_one_regime_is_not_enough_even_with_positive_review():
    assessment = assess_st_implementation_freeze(
        (_evidence("TREND", "2024-01-01", "2024-03-01"),),
        review=_accepted_review(),
    )

    assert assessment.status is STImplementationFreezeStatus.VALIDATION_REQUIRED
    assert assessment.regime_count == 1
    assert "freeze/multiple-market-regimes-required" in assessment.release_issues


def test_duplicate_regime_identity_or_period_cannot_fake_cross_regime_coverage():
    trend = _evidence("TREND", "2024-01-01", "2024-03-01")
    duplicate_id = _evidence("TREND", "2024-04-01", "2024-06-01")
    duplicate_period = _evidence("RANGE", "2024-01-01", "2024-03-01")

    by_id = assess_st_implementation_freeze((trend, duplicate_id), review=_accepted_review())
    by_period = assess_st_implementation_freeze((trend, duplicate_period), review=_accepted_review())

    assert "freeze/regime-ids-must-be-distinct" in by_id.release_issues
    assert "freeze/regime-periods-must-be-distinct" in by_period.release_issues


def test_proxy_and_empty_behavior_cannot_be_used_as_freeze_evidence():
    with pytest.raises(ValueError, match="canonical production validation"):
        STRegimeValidationEvidence(
            regime_id="TREND",
            period_start=pd.Timestamp("2024-01-01"),
            period_end=pd.Timestamp("2024-03-01"),
            report=_report(
                source="CANONICAL_READINESS_PROXY",
                production=False,
                proxy_rows=1,
            ),
        )

    with pytest.raises(ValueError, match="completed ST trade"):
        STRegimeValidationEvidence(
            regime_id="RANGE",
            period_start=pd.Timestamp("2024-04-01"),
            period_end=pd.Timestamp("2024-06-01"),
            report=_report(completed_trade_count=0),
        )


def test_cross_regime_review_must_accept_both_early_and_late_behavior_axes():
    evidence = (
        _evidence("TREND", "2024-01-01", "2024-03-01"),
        _evidence("RANGE", "2024-04-01", "2024-06-01"),
    )
    review = _accepted_review(protective_exits_not_systematically_late=False)

    assessment = assess_st_implementation_freeze(evidence, review=review)

    assert assessment.status is STImplementationFreezeStatus.VALIDATION_REQUIRED
    assert "freeze/cross-regime-behavior-not-accepted" in assessment.release_issues


def test_distinct_canonical_regimes_and_explicit_acceptance_can_mark_candidate():
    evidence = (
        _evidence("TREND", "2024-01-01", "2024-03-01"),
        _evidence("RANGE", "2024-04-01", "2024-06-01"),
        _evidence("REVERSAL_VOLATILE", "2024-07-01", "2024-09-01"),
    )

    assessment = assess_st_implementation_freeze(evidence, review=_accepted_review())

    assert assessment.status is STImplementationFreezeStatus.PRODUCTION_CANDIDATE
    assert assessment.production_candidate is True
    assert assessment.release_issues == ()
    assert assessment.regime_count == 3
    assert assessment.evidence_regime_ids == ("RANGE", "REVERSAL_VOLATILE", "TREND")
