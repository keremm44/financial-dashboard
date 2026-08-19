import pytest

from financial_dashboard.engines.pattern_compression_core import (
    PATTERN_BULL_FLAG,
    PATTERN_BULL_PENNANT,
    PATTERN_NONE,
    PATTERN_SYMMETRICAL_TRIANGLE,
    PROFILE_BALANCED,
    PatternCompressionConfig,
    PivotStore,
    PoleInfo,
)
from financial_dashboard.engines.pattern_compression_geometry import PatternGeometryEvaluator
from financial_dashboard.engines.pattern_compression_specialized import (
    PatternPoleEvaluator,
    SpecializedPatternEvaluator,
    depth_quality,
    duration_quality,
)


def _continuation_fixture(*, pennant: bool = False):
    config = PatternCompressionConfig(profile=PROFILE_BALANCED, min_tick=0.01)
    store = PivotStore(config)

    # Prior low is the bull-pole start; the next four pivots define the consolidation.
    store.low_prices[:] = [100.0, 114.5 if pennant else 116.0, 115.5 if pennant else 114.0]
    store.low_bars[:] = [0, 15, 25]
    store.low_confirm_bars[:] = [5, 20, 30]
    store.low_locked[:] = [True, True, False]
    store.high_prices[:] = [120.0, 119.0 if pennant else 118.0]
    store.high_bars[:] = [10, 20]
    store.high_confirm_bars[:] = [15, 25]
    store.high_locked[:] = [True, False]

    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    atrs: list[float] = []
    for bar in range(31):
        if bar < 10:
            close = 100.0 + 2.0 * bar
            highs.append(close + 0.4)
            lows.append(close - 0.4)
            closes.append(close)
        else:
            if pennant:
                upper = 121.0 - 0.1 * bar
                lower = 113.0 + 0.1 * bar
            else:
                upper = 122.0 - 0.2 * bar
                lower = 119.0 - 0.2 * bar
            highs.append(upper - 0.10)
            lows.append(lower + 0.10)
            closes.append((upper + lower) * 0.5)
        atrs.append(4.0)
    return store, highs, lows, closes, atrs


def _pole_and_analysis(*, pennant: bool = False):
    store, highs, lows, closes, atrs = _continuation_fixture(pennant=pennant)
    pole = PatternPoleEvaluator(
        store=store,
        highs=highs,
        lows=lows,
        closes=closes,
        atrs=atrs,
        current_bar=30,
    ).find_pole(end_bar=10, end_price=120.0, direction=1)
    analysis = PatternGeometryEvaluator(
        store=store,
        highs=highs,
        lows=lows,
        closes=closes,
        atrs=atrs,
        current_bar=30,
    ).analyze(high_a=0, high_b=1, low_a=1, low_b=2)
    return store, highs, lows, closes, pole, analysis


def test_bull_pole_requires_multi_bar_efficient_atr_move() -> None:
    _, _, _, _, pole, _ = _pole_and_analysis()

    assert pole.valid
    assert pole.direction == 1
    assert pole.start_bar == 0
    assert pole.end_bar == 10
    assert pole.start_price == 100.0
    assert pole.end_price == 120.0
    assert pole.duration == 10
    assert pole.magnitude == 20.0
    assert pole.efficiency is not None and pole.efficiency >= 0.95
    assert pole.quality >= 49.0


def test_single_bar_shock_is_capped_below_balanced_pole_threshold() -> None:
    config = PatternCompressionConfig(profile=PROFILE_BALANCED, min_tick=0.01)
    store = PivotStore(config)
    store.low_prices[:] = [100.0]
    store.low_bars[:] = [9]
    store.low_confirm_bars[:] = [10]
    store.low_locked[:] = [False]
    highs = [100.5] * 9 + [100.5, 120.5]
    lows = [99.5] * 9 + [99.5, 99.5]
    closes = [100.0] * 10 + [120.0]
    atrs = [4.0] * 11

    pole = PatternPoleEvaluator(
        store=store,
        highs=highs,
        lows=lows,
        closes=closes,
        atrs=atrs,
        current_bar=10,
    ).find_pole(end_bar=10, end_price=120.0, direction=1)

    assert not pole.valid
    assert pole.quality <= 46.0


def test_parallel_correction_with_linked_bull_pole_becomes_bull_flag() -> None:
    store, highs, lows, closes, bull_pole, analysis = _pole_and_analysis()
    assert analysis.parallel_geometry_supported
    assert analysis.generic_type == PATTERN_NONE
    assert analysis.historical_geometry_acceptable
    assert bull_pole.valid

    candidate = SpecializedPatternEvaluator(
        store=store,
        highs=highs,
        lows=lows,
        closes=closes,
    ).apply(analysis=analysis, bull_pole=bull_pole, bear_pole=PoleInfo())

    assert candidate.valid
    assert candidate.pattern_type == PATTERN_BULL_FLAG
    assert candidate.family == "Bayrak"
    assert candidate.classic_dir == 1
    assert candidate.has_pole
    assert candidate.pole_start_bar == 0
    assert candidate.pole_end_bar == 10
    assert candidate.correction_depth is not None and 0.08 <= candidate.correction_depth <= 0.80
    assert candidate.duration_ratio is not None and candidate.duration_ratio <= 3.50
    assert candidate.consolidation_height_ratio is not None and candidate.consolidation_height_ratio <= 0.58
    assert candidate.contraction_score == 0.0
    assert candidate.geometry_score > 0.0
    assert candidate.raw_quality >= 50.0


def test_symmetric_consolidation_with_linked_bull_pole_becomes_bull_pennant() -> None:
    store, highs, lows, closes, bull_pole, analysis = _pole_and_analysis(pennant=True)
    assert analysis.generic_type == PATTERN_SYMMETRICAL_TRIANGLE
    assert analysis.historical_geometry_acceptable
    assert bull_pole.valid

    candidate = SpecializedPatternEvaluator(
        store=store,
        highs=highs,
        lows=lows,
        closes=closes,
    ).apply(analysis=analysis, bull_pole=bull_pole, bear_pole=PoleInfo())

    assert candidate.valid
    assert candidate.pattern_type == PATTERN_BULL_PENNANT
    assert candidate.family == "Flama"
    assert candidate.classic_dir == 1
    assert candidate.has_pole
    assert candidate.correction_depth is not None and 0.06 <= candidate.correction_depth <= 0.70
    assert candidate.duration_ratio is not None and candidate.duration_ratio <= 2.80
    assert candidate.consolidation_height_ratio is not None and candidate.consolidation_height_ratio <= 0.46
    assert candidate.raw_quality >= 50.0


def test_specialization_does_not_override_geometry_without_valid_pole() -> None:
    store, highs, lows, closes, _, analysis = _pole_and_analysis()
    candidate = SpecializedPatternEvaluator(
        store=store,
        highs=highs,
        lows=lows,
        closes=closes,
    ).apply(analysis=analysis, bull_pole=PoleInfo(), bear_pole=PoleInfo())

    assert candidate.pattern_type == PATTERN_NONE
    assert not candidate.valid
    assert not candidate.has_pole


def test_depth_and_duration_quality_keep_source_optimal_bands() -> None:
    assert depth_quality(0.30) == pytest.approx(100.0)
    assert duration_quality(1.00) == pytest.approx(100.0)
    assert depth_quality(0.90) == 0.0
    assert duration_quality(4.00) == 0.0
