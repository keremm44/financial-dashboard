import pytest

from financial_dashboard.engines.pattern_compression_core import (
    PATTERN_ASCENDING_TRIANGLE,
    PATTERN_DESCENDING_TRIANGLE,
    PATTERN_FALLING_WEDGE,
    PATTERN_RISING_WEDGE,
    PATTERN_SYMMETRICAL_TRIANGLE,
    PROFILE_BALANCED,
    PatternCompressionConfig,
    PivotStore,
)
from financial_dashboard.engines.pattern_compression_geometry import (
    PatternGeometryEvaluator,
    classify_generic_pattern,
    violation_penalty_from_stats,
)


def _ascending_triangle_fixture(*, current_bar: int = 30):
    config = PatternCompressionConfig(profile=PROFILE_BALANCED, min_tick=0.01)
    store = PivotStore(config)
    store.high_prices[:] = [110.0, 110.0]
    store.high_bars[:] = [0, 20]
    store.high_confirm_bars[:] = [5, 25]
    store.high_locked[:] = [True, False]
    store.low_prices[:] = [100.0, 106.0]
    store.low_bars[:] = [5, 25]
    store.low_confirm_bars[:] = [10, 30]
    store.low_locked[:] = [True, False]

    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    atrs: list[float] = []
    for bar in range(current_bar + 1):
        upper = 110.0
        lower = 98.5 + 0.3 * bar
        highs.append(upper - 0.20)
        lows.append(lower + 0.20)
        closes.append((upper + lower) * 0.5)
        atrs.append(10.0)
    return store, highs, lows, closes, atrs


def _evaluate_fixture(*, highs=None, lows=None, closes=None, atrs=None):
    store, base_highs, base_lows, base_closes, base_atrs = _ascending_triangle_fixture()
    evaluator = PatternGeometryEvaluator(
        store=store,
        highs=highs if highs is not None else base_highs,
        lows=lows if lows is not None else base_lows,
        closes=closes if closes is not None else base_closes,
        atrs=atrs if atrs is not None else base_atrs,
        current_bar=30,
    )
    return evaluator.analyze(high_a=0, high_b=1, low_a=0, low_b=1)


@pytest.mark.parametrize(
    ("upper_slope", "lower_slope", "highs_lower", "lows_higher", "expected"),
    [
        (0.000, 0.050, True, True, PATTERN_ASCENDING_TRIANGLE),
        (-0.050, 0.000, True, True, PATTERN_DESCENDING_TRIANGLE),
        (-0.050, 0.050, True, True, PATTERN_SYMMETRICAL_TRIANGLE),
        (0.030, 0.060, True, True, PATTERN_RISING_WEDGE),
        (-0.060, -0.030, True, True, PATTERN_FALLING_WEDGE),
    ],
)
def test_generic_pattern_classification_order_matches_pine(
    upper_slope: float,
    lower_slope: float,
    highs_lower: bool,
    lows_higher: bool,
    expected: str,
) -> None:
    result = classify_generic_pattern(
        touch_basics=True,
        converging=True,
        apex_ok=True,
        parallel_like=False,
        upper_slope_norm=upper_slope,
        lower_slope_norm=lower_slope,
        flat_slope_norm_tol=0.024,
        highs_lower=highs_lower,
        lows_higher=lows_higher,
    )
    assert result == expected


def test_balanced_ascending_triangle_builds_from_four_confirmed_pivots() -> None:
    analysis = _evaluate_fixture()
    candidate = analysis.candidate

    assert analysis.chronological
    assert analysis.touch_basics
    assert analysis.touch_distribution
    assert analysis.generic_type == PATTERN_ASCENDING_TRIANGLE
    assert analysis.converging
    assert not analysis.parallel_like
    assert analysis.pre_geometry_score == 100.0
    assert analysis.historical_geometry_acceptable
    assert analysis.post_pivot_survival_passed
    assert candidate.valid
    assert candidate.pattern_type == PATTERN_ASCENDING_TRIANGLE
    assert candidate.family == "Üçgen"
    assert candidate.classic_dir == 1
    assert candidate.upper_touches == 2
    assert candidate.lower_touches == 2
    assert candidate.contraction is not None and candidate.contraction > 0.70
    assert candidate.geometry_score > 0.0
    assert candidate.touch_score > 0.0
    assert candidate.maturity_score > 0.0
    assert candidate.raw_quality >= 46.0
    assert candidate.historical_close_violations == 0
    assert candidate.historical_wick_violations == 0
    assert candidate.violation_scan_mode == "Tam+Survival"


def test_decision_bar_boundary_break_is_not_swallowed_by_historical_survival() -> None:
    store, highs, lows, closes, atrs = _ascending_triangle_fixture()
    highs[-1] = 125.0
    closes[-1] = 120.0

    analysis = PatternGeometryEvaluator(
        store=store,
        highs=highs,
        lows=lows,
        closes=closes,
        atrs=atrs,
        current_bar=30,
    ).analyze(high_a=0, high_b=1, low_a=0, low_b=1)

    assert analysis.post_pivot_survival_passed
    assert analysis.historical_geometry_acceptable
    assert analysis.candidate.valid
    assert analysis.candidate.historical_close_violations == 0
    assert analysis.candidate.violation > 1.0


def test_post_pivot_close_violation_rejects_survival_before_decision_bar() -> None:
    store, highs, lows, closes, atrs = _ascending_triangle_fixture()
    closes[29] = 112.0
    highs[29] = 112.2

    analysis = PatternGeometryEvaluator(
        store=store,
        highs=highs,
        lows=lows,
        closes=closes,
        atrs=atrs,
        current_bar=30,
    ).analyze(high_a=0, high_b=1, low_a=0, low_b=1)

    assert not analysis.post_pivot_survival_passed
    assert not analysis.historical_geometry_acceptable
    assert not analysis.candidate.valid
    assert analysis.candidate.pattern_type == PATTERN_ASCENDING_TRIANGLE
    assert analysis.candidate.historical_close_violations >= 1
    assert analysis.candidate.raw_quality == 0.0


def test_two_historical_close_violations_fail_geometry_acceptance() -> None:
    store, highs, lows, closes, atrs = _ascending_triangle_fixture()
    for bar in (10, 11):
        closes[bar] = 112.0
        highs[bar] = 112.2

    analysis = PatternGeometryEvaluator(
        store=store,
        highs=highs,
        lows=lows,
        closes=closes,
        atrs=atrs,
        current_bar=30,
    ).analyze(high_a=0, high_b=1, low_a=0, low_b=1)

    assert analysis.candidate.historical_close_violations >= 2
    assert not analysis.historical_geometry_acceptable
    assert not analysis.candidate.valid


def test_violation_penalty_uses_profile_event_and_repeat_weights() -> None:
    assert violation_penalty_from_stats(
        profile=PROFILE_BALANCED,
        total_close_violations=0,
        total_wick_violations=0,
        maximum_violation=0.0,
        history_truncated=False,
    ) == 0.0
    assert violation_penalty_from_stats(
        profile=PROFILE_BALANCED,
        total_close_violations=2,
        total_wick_violations=1,
        maximum_violation=0.5,
        history_truncated=True,
    ) == pytest.approx(54.0)


def test_geometry_score_excludes_progress_but_maturity_uses_it() -> None:
    early_store, early_highs, early_lows, early_closes, early_atrs = _ascending_triangle_fixture(current_bar=27)
    late_store, late_highs, late_lows, late_closes, late_atrs = _ascending_triangle_fixture(current_bar=30)

    early = PatternGeometryEvaluator(
        store=early_store,
        highs=early_highs,
        lows=early_lows,
        closes=early_closes,
        atrs=early_atrs,
        current_bar=27,
    ).analyze(high_a=0, high_b=1, low_a=0, low_b=1)
    late = PatternGeometryEvaluator(
        store=late_store,
        highs=late_highs,
        lows=late_lows,
        closes=late_closes,
        atrs=late_atrs,
        current_bar=30,
    ).analyze(high_a=0, high_b=1, low_a=0, low_b=1)

    assert early.candidate.progress != late.candidate.progress
    assert early.candidate.maturity_score != late.candidate.maturity_score
    # Geometry changes only because the projected width/contraction changes with bar index;
    # progress itself has no direct weight in the geometry formula.
    expected_late_geometry = late.candidate.slope_shape_score * 0.65 + late.candidate.contraction_score * 0.35
    assert late.candidate.geometry_score == pytest.approx(expected_late_geometry)
