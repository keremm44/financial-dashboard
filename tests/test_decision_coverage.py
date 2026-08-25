from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.coverage import CoverageFamily, assess_coverage


def test_coverage_keeps_valid_degraded_and_unavailable_separate():
    result = assess_coverage(
        {
            CoverageFamily.STRUCTURE: ContextDataQuality.VALID,
            CoverageFamily.PARTICIPATION: ContextDataQuality.DATA_LIMITED,
            CoverageFamily.VOLATILITY: ContextDataQuality.UNAVAILABLE,
        },
        expected_families=(CoverageFamily.STRUCTURE, CoverageFamily.PARTICIPATION, CoverageFamily.VOLATILITY),
        critical_families=(CoverageFamily.STRUCTURE,),
    )
    assert result.valid_fraction == 1 / 3
    assert result.observed_fraction == 2 / 3
    assert result.degraded_families == (CoverageFamily.PARTICIPATION,)
    assert result.unavailable_families == (CoverageFamily.VOLATILITY,)
    assert result.critical_path_missing == ()


def test_missing_critical_family_is_explicit_not_neutral():
    result = assess_coverage(
        {},
        expected_families=(CoverageFamily.STRUCTURE, CoverageFamily.HAM),
        critical_families=(CoverageFamily.STRUCTURE,),
    )
    assert result.valid_fraction == 0.0
    assert result.critical_path_missing == (CoverageFamily.STRUCTURE,)
    assert CoverageFamily.STRUCTURE in result.unavailable_families


def test_degraded_critical_family_is_still_missing_from_valid_critical_path():
    result = assess_coverage(
        {CoverageFamily.STRUCTURE: ContextDataQuality.DATA_LIMITED},
        expected_families=(CoverageFamily.STRUCTURE,),
        critical_families=(CoverageFamily.STRUCTURE,),
    )
    assert result.critical_path_missing == (CoverageFamily.STRUCTURE,)
    assert result.degraded_families == (CoverageFamily.STRUCTURE,)
