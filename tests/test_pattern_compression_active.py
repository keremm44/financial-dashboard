import pytest

from financial_dashboard.engines.pattern_compression_active import (
    efficiency_between,
    evaluate_normal_state,
    hard_geometry_invalid,
    refresh_active_candidate,
    reset_quality_snapshot,
)
from financial_dashboard.engines.pattern_compression_core import (
    PATTERN_ASCENDING_TRIANGLE,
    PROFILE_BALANCED,
    ST_BREAK_TIMEOUT,
    ST_CANDIDATE,
    ST_COMPRESSING,
    ST_DEFINED,
    ST_GEOMETRY,
    ST_INVALID,
    ST_PREP,
    ST_WEAK,
    PatternCandidate,
    PatternCompressionConfig,
    PivotStore,
)
from financial_dashboard.engines.pattern_compression_geometry import PatternGeometryEvaluator


def _series():
    highs = [110.0] * 40
    lows = [100.0 + index * 0.15 for index in range(40)]
    closes = [(high + low) * 0.5 for high, low in zip(highs, lows, strict=True)]
    atrs = [4.0] * 40
    return highs, lows, closes, atrs


def _candidate(*, quality: float = 70.0, start_bar: int = 0, known_bar: int = 10) -> PatternCandidate:
    return PatternCandidate(
        valid=True,
        identity=3,
        pattern_type=PATTERN_ASCENDING_TRIANGLE,
        family="Üçgen",
        classic_dir=1,
        raw_quality=quality,
        geometry_atr=4.0,
        slope_shape_score=90.0,
        touch_score=80.0,
        upper_touches=2,
        lower_touches=2,
        start_bar=start_bar,
        end_bar=20,
        known_bar=known_bar,
        apex_bar=50,
        hb1=0,
        hp1=110.0,
        hb2=20,
        hp2=110.0,
        lb1=5,
        lp1=100.0,
        lb2=25,
        lp2=105.0,
        upper_slope=0.0,
        lower_slope=0.25,
        start_width=11.25,
        current_width=4.0,
        contraction=0.60,
        upper_now=110.0,
        lower_now=106.0,
        progress=0.60,
    )


def test_reset_quality_snapshot_clears_break_frozen_fields() -> None:
    candidate = _candidate()
    candidate.quality_frozen = True
    candidate.frozen_raw_quality = 70.0
    candidate.frozen_break_buffer = 0.2
    candidate.break_snapshot_bar = 30
    candidate.break_strength = 88.0

    reset = reset_quality_snapshot(candidate)
    assert reset.valid
    assert not reset.quality_frozen
    assert reset.frozen_raw_quality is None
    assert reset.frozen_break_buffer is None
    assert reset.break_snapshot_bar is None
    assert reset.break_strength is None


def test_efficiency_between_matches_net_over_path() -> None:
    closes = [100.0, 102.0, 101.0, 104.0]
    # path = 2 + 1 + 3 = 6, net = 4
    assert efficiency_between(
        closes,
        start_bar=0,
        end_bar=3,
        current_bar=3,
        maximum_bars=120,
        min_tick=0.01,
    ) == pytest.approx(4.0 / 6.0)


def test_frozen_refresh_moves_projected_boundaries_without_repricing_quality() -> None:
    config = PatternCompressionConfig(profile=PROFILE_BALANCED)
    highs, lows, closes, atrs = _series()
    store = PivotStore(config)
    evaluator = PatternGeometryEvaluator(
        store=store,
        highs=highs,
        lows=lows,
        closes=closes,
        atrs=atrs,
        current_bar=30,
    )
    candidate = _candidate()
    candidate.quality_frozen = True
    candidate.frozen_raw_quality = 70.0
    candidate.raw_quality = 20.0

    refreshed = refresh_active_candidate(
        candidate,
        evaluator=evaluator,
        highs=highs,
        lows=lows,
        closes=closes,
        violation_end_bar=29,
    )
    assert refreshed.upper_now == pytest.approx(110.0)
    assert refreshed.lower_now == pytest.approx(111.25)
    assert refreshed.raw_quality == 20.0
    assert refreshed.frozen_raw_quality == 70.0
    assert refreshed.violation_scan_mode == "Dondurulmuş"


def test_generic_normal_state_progression_uses_age_quality_and_near_boundary() -> None:
    config = PatternCompressionConfig(profile=PROFILE_BALANCED)

    early = _candidate(start_bar=20, known_bar=20)
    early.raw_quality = 70.0
    early.upper_now = 110.0
    early.lower_now = 105.0
    early.current_width = 5.0
    early.contraction = 0.60
    early.progress = 0.30
    early_eval = evaluate_normal_state(
        early,
        current_state=ST_CANDIDATE,
        bar_index=23,
        close=107.0,
        safe_atr=4.0,
        config=config,
    )
    assert early_eval.state == ST_CANDIDATE

    geometry = evaluate_normal_state(
        early,
        current_state=ST_CANDIDATE,
        bar_index=32,
        close=107.0,
        safe_atr=4.0,
        config=config,
    )
    assert geometry.state == ST_GEOMETRY

    defined_candidate = _candidate(quality=50.0)
    defined_candidate.current_width = 4.0
    defined_candidate.contraction = 0.35
    defined_candidate.progress = 0.50
    defined_eval = evaluate_normal_state(
        defined_candidate,
        current_state=ST_GEOMETRY,
        bar_index=18,
        close=107.0,
        safe_atr=4.0,
        config=config,
    )
    assert defined_eval.state == ST_DEFINED

    mature_candidate = _candidate(quality=70.0)
    mature_candidate.current_width = 4.0
    mature_candidate.contraction = 0.60
    mature_candidate.progress = 0.60
    mature_eval = evaluate_normal_state(
        mature_candidate,
        current_state=ST_DEFINED,
        bar_index=20,
        close=106.0,
        safe_atr=4.0,
        config=config,
    )
    assert mature_eval.state == ST_COMPRESSING

    prep_eval = evaluate_normal_state(
        mature_candidate,
        current_state=ST_COMPRESSING,
        bar_index=20,
        close=109.8,
        safe_atr=4.0,
        config=config,
    )
    assert prep_eval.state == ST_PREP


def test_historical_degradation_is_weak_not_terminal_invalid() -> None:
    config = PatternCompressionConfig(profile=PROFILE_BALANCED)
    candidate = _candidate(quality=70.0)
    candidate.historical_close_violations = 1
    candidate.historical_violation_penalty = 30.0
    candidate.current_width = 4.0
    candidate.contraction = 0.60
    candidate.progress = 0.60

    result = evaluate_normal_state(
        candidate,
        current_state=ST_DEFINED,
        bar_index=20,
        close=107.0,
        safe_atr=4.0,
        config=config,
    )
    assert result.weak
    assert not result.invalid
    assert result.state == ST_WEAK
    assert result.invalid_reason == "Geçmiş sınır ihlalleri kaliteyi düşürüyor"


def test_generic_hard_geometry_break_is_terminal_invalid() -> None:
    config = PatternCompressionConfig(profile=PROFILE_BALANCED)
    candidate = _candidate()
    candidate.progress = 1.03
    candidate.current_width = 2.0
    candidate.contraction = 0.60
    assert hard_geometry_invalid(candidate, geometry_atr=4.0, config=config)

    result = evaluate_normal_state(
        candidate,
        current_state=ST_DEFINED,
        bar_index=20,
        close=107.0,
        safe_atr=4.0,
        config=config,
    )
    assert result.state == ST_INVALID
    assert result.invalid_reason == "Apex bölgesi geçildi"


def test_break_timeout_recovers_to_normal_preparation_state() -> None:
    config = PatternCompressionConfig(profile=PROFILE_BALANCED)
    candidate = _candidate(quality=70.0)
    candidate.quality_frozen = True
    candidate.frozen_raw_quality = 70.0
    candidate.current_width = 4.0
    candidate.contraction = 0.60
    candidate.progress = 0.60
    candidate.upper_now = 110.0
    candidate.lower_now = 106.0

    result = evaluate_normal_state(
        candidate,
        current_state=ST_BREAK_TIMEOUT,
        bar_index=20,
        close=109.8,
        safe_atr=4.0,
        config=config,
    )
    assert result.state == ST_PREP
