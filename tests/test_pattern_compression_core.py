import pytest

from financial_dashboard.engines.pattern_compression_core import (
    PATTERN_ASCENDING_TRIANGLE,
    PATTERN_BEAR_FLAG,
    PATTERN_BEAR_PENNANT,
    PATTERN_BULL_FLAG,
    PATTERN_BULL_PENNANT,
    PATTERN_DESCENDING_TRIANGLE,
    PATTERN_EXPORT_TITLES,
    PATTERN_FALLING_WEDGE,
    PATTERN_RISING_WEDGE,
    PATTERN_SYMMETRICAL_TRIANGLE,
    PROFILE_BALANCED,
    PROFILE_SELECTIVE,
    PROFILE_SENSITIVE,
    ST_BREAK_ATTEMPT,
    ST_BREAK_CANDIDATE,
    ST_BREAK_CONFIRMED,
    ST_BREAK_FAILED,
    ST_BREAK_TIMEOUT,
    ST_CANDIDATE,
    ST_COMPLETED,
    ST_COMPRESSING,
    ST_DEFINED,
    ST_GEOMETRY,
    ST_INVALID,
    ST_MATURING,
    ST_PREP,
    ST_RETESTING,
    ST_RETEST_OK,
    ST_RETEST_WAIT,
    ST_WEAK,
    PatternCandidate,
    PatternCompressionConfig,
    PivotStore,
    age_quality,
    band_quality,
    chronological_swings,
    classic_direction,
    contraction_quality,
    export_break_state,
    export_pattern_state,
    export_pattern_type,
    export_retest_state,
    line_price,
    progress_quality,
    slope,
    smoothstep,
)


def test_profiles_match_final_pine_contract() -> None:
    sensitive = PatternCompressionConfig(profile=PROFILE_SENSITIVE).resolve()
    balanced = PatternCompressionConfig(profile=PROFILE_BALANCED).resolve()
    selective = PatternCompressionConfig(profile=PROFILE_SELECTIVE).resolve()

    assert (sensitive.pivot_len, balanced.pivot_len, selective.pivot_len) == (3, 5, 7)
    assert (sensitive.min_age, balanced.min_age, selective.min_age) == (10, 16, 24)
    assert (sensitive.min_touch_gap, balanced.min_touch_gap, selective.min_touch_gap) == (3, 5, 7)
    assert (sensitive.touch_atr_mult, balanced.touch_atr_mult, selective.touch_atr_mult) == (0.20, 0.15, 0.12)
    assert (sensitive.min_contraction, balanced.min_contraction, selective.min_contraction) == (0.15, 0.25, 0.35)
    assert (sensitive.break_atr, balanced.break_atr, selective.break_atr) == (0.04, 0.06, 0.08)
    assert (sensitive.confirm_window, balanced.confirm_window, selective.confirm_window) == (2, 2, 3)
    assert (sensitive.min_pole_atr, balanced.min_pole_atr, selective.min_pole_atr) == (2.0, 2.8, 3.6)
    assert (sensitive.min_pole_efficiency, balanced.min_pole_efficiency, selective.min_pole_efficiency) == (0.55, 0.62, 0.70)
    assert (sensitive.max_pole_bars, balanced.max_pole_bars, selective.max_pole_bars) == (16, 20, 26)
    assert (sensitive.max_consolidation_bars, balanced.max_consolidation_bars, selective.max_consolidation_bars) == (30, 40, 50)
    assert (sensitive.min_raw_quality, balanced.min_raw_quality, selective.min_raw_quality) == (38.0, 46.0, 55.0)
    assert (sensitive.min_specialized_quality, balanced.min_specialized_quality, selective.min_specialized_quality) == (42.0, 50.0, 58.0)
    assert (sensitive.min_pole_quality, balanced.min_pole_quality, selective.min_pole_quality) == (40.0, 49.0, 58.0)
    assert (sensitive.min_break_strength, balanced.min_break_strength, selective.min_break_strength) == (42.0, 50.0, 58.0)
    assert (sensitive.retest_window, balanced.retest_window, selective.retest_window) == (5, 5, 7)
    assert (sensitive.retest_hold_window, balanced.retest_hold_window, selective.retest_hold_window) == (2, 3, 4)
    assert (sensitive.flat_slope_norm_tol, balanced.flat_slope_norm_tol, selective.flat_slope_norm_tol) == (0.030, 0.024, 0.018)
    assert balanced.parallel_slope_norm_tol == pytest.approx(0.018)


def test_manual_config_overrides_only_pine_manual_fields() -> None:
    resolved = PatternCompressionConfig(
        profile=PROFILE_SELECTIVE,
        use_manual=True,
        manual_pivot_len=4,
        manual_min_age=12,
        manual_min_touch_gap=4,
        manual_touch_atr=0.19,
        manual_min_contraction_pct=22.0,
        manual_min_break_strength=47.0,
    ).resolve()

    assert resolved.pivot_len == 4
    assert resolved.min_age == 12
    assert resolved.min_touch_gap == 4
    assert resolved.touch_atr_mult == 0.19
    assert resolved.min_contraction == 0.22
    assert resolved.min_break_strength == 47.0
    assert resolved.break_atr == 0.08
    assert resolved.confirm_window == 3
    assert resolved.min_pole_atr == 3.6


def test_quality_helpers_follow_pine_smoothstep_math() -> None:
    assert smoothstep(0.0, 1.0, -1.0) == 0.0
    assert smoothstep(0.0, 1.0, 0.5) == pytest.approx(0.5)
    assert smoothstep(0.0, 1.0, 2.0) == 1.0
    assert band_quality(0.5, 0.0, 0.25, 0.75, 1.0) == pytest.approx(100.0)
    assert progress_quality(None) == 0.0
    assert progress_quality(0.42) > progress_quality(0.15)
    assert progress_quality(1.03) == pytest.approx(0.0)
    assert age_quality(0, 16) == 0.0
    assert age_quality(16, 16) > age_quality(6, 16)
    assert contraction_quality(None, 0.25) == 0.0
    assert contraction_quality(0.25, 0.25) == pytest.approx(45.0)
    assert contraction_quality(0.52, 0.25) == pytest.approx(100.0)


def test_line_and_slope_helpers_are_deterministic() -> None:
    assert line_price(10, 100.0, 20, 110.0, 15) == pytest.approx(105.0)
    assert line_price(10, 100.0, 10, 112.0, 25) == 112.0
    assert slope(10, 100.0, 20, 110.0) == pytest.approx(1.0)
    assert slope(10, 100.0, 10, 112.0) == 0.0


def test_export_contract_codes_match_final_pine() -> None:
    state_codes = {
        ST_CANDIDATE: 1,
        ST_GEOMETRY: 2,
        ST_DEFINED: 3,
        ST_MATURING: 4,
        ST_COMPRESSING: 5,
        ST_PREP: 6,
        ST_BREAK_ATTEMPT: 7,
        ST_BREAK_CANDIDATE: 8,
        ST_BREAK_CONFIRMED: 9,
        ST_RETEST_WAIT: 10,
        ST_RETESTING: 11,
        ST_RETEST_OK: 12,
        ST_COMPLETED: 13,
        ST_BREAK_TIMEOUT: 14,
        ST_BREAK_FAILED: 15,
        ST_WEAK: 16,
        ST_INVALID: 17,
    }
    assert {state: export_pattern_state(state) for state in state_codes} == state_codes
    assert export_pattern_state("UNKNOWN") == 0

    type_codes = {
        PATTERN_ASCENDING_TRIANGLE: 1,
        PATTERN_DESCENDING_TRIANGLE: 2,
        PATTERN_SYMMETRICAL_TRIANGLE: 3,
        PATTERN_RISING_WEDGE: 4,
        PATTERN_FALLING_WEDGE: 5,
        PATTERN_BULL_FLAG: 6,
        PATTERN_BEAR_FLAG: 7,
        PATTERN_BULL_PENNANT: 8,
        PATTERN_BEAR_PENNANT: 9,
    }
    assert {pattern: export_pattern_type(pattern) for pattern in type_codes} == type_codes
    assert export_pattern_type("UNKNOWN") == 0

    assert export_break_state(ST_BREAK_ATTEMPT, 1) == 1
    assert export_break_state(ST_BREAK_CANDIDATE, -1) == -2
    assert export_break_state(ST_RETESTING, 1) == 3
    assert export_break_state(ST_COMPLETED, -1) == -4
    assert export_break_state(ST_BREAK_TIMEOUT, 1) == 5
    assert export_break_state(ST_BREAK_FAILED, -1) == -6
    assert export_break_state(ST_DEFINED, 1) == 0
    assert export_break_state(ST_BREAK_CONFIRMED, 0) == 0

    assert export_retest_state(ST_BREAK_CONFIRMED, 10, None) == 1
    assert export_retest_state(ST_RETESTING, 10, None) == 2
    assert export_retest_state(ST_RETEST_OK, 10, 12) == 3
    assert export_retest_state(ST_COMPLETED, 10, 12) == 4
    assert export_retest_state(ST_BREAK_FAILED, 10, None) == -1
    assert export_retest_state(ST_BREAK_FAILED, None, None) == 0


def test_export_title_set_is_exact_and_does_not_invent_handshake() -> None:
    assert len(PATTERN_EXPORT_TITLES) == 10
    assert len(set(PATTERN_EXPORT_TITLES)) == 10
    assert "ARGENT | PATTERN | BREAK_STATE" in PATTERN_EXPORT_TITLES
    assert all("HANDSHAKE" not in title for title in PATTERN_EXPORT_TITLES)


def test_classic_direction_is_pattern_semantics_not_actual_break_direction() -> None:
    assert classic_direction(PATTERN_ASCENDING_TRIANGLE) == 1
    assert classic_direction(PATTERN_FALLING_WEDGE) == 1
    assert classic_direction(PATTERN_BULL_FLAG) == 1
    assert classic_direction(PATTERN_DESCENDING_TRIANGLE) == -1
    assert classic_direction(PATTERN_RISING_WEDGE) == -1
    assert classic_direction(PATTERN_BEAR_PENNANT) == -1
    assert classic_direction(PATTERN_SYMMETRICAL_TRIANGLE) == 0


def test_pivot_store_replaces_open_same_type_and_appends_locked_stronger_extreme() -> None:
    store = PivotStore(PatternCompressionConfig(profile=PROFILE_BALANCED))

    assert store.add_pivot(side="high", price=100.0, source_bar=10, confirm_bar=15) == (True, True)
    assert store.add_pivot(side="high", price=99.0, source_bar=11, confirm_bar=16) == (False, False)
    assert store.add_pivot(side="high", price=101.0, source_bar=12, confirm_bar=17) == (True, False)
    assert store.high_prices == [101.0]
    assert store.high_bars == [12]

    assert store.lock_pivot_by_bar("high", 12) == 1
    assert store.add_pivot(side="high", price=102.0, source_bar=13, confirm_bar=18) == (True, True)
    assert store.high_prices == [101.0, 102.0]
    assert store.high_locked == [True, False]


def test_opposite_pivot_appends_and_same_bar_selection_prefers_alternation() -> None:
    store = PivotStore(PatternCompressionConfig(profile=PROFILE_BALANCED))
    store.add_pivot(side="high", price=100.0, source_bar=10, confirm_bar=15)

    selected, reason, high_distance, low_distance = store.choose_same_bar_pivot(
        high_candidate=101.0,
        low_candidate=98.0,
        source_atr=2.0,
        source_open=99.0,
        source_high=101.0,
        source_low=98.0,
        source_close=100.0,
    )

    assert selected == -1
    assert reason == "Dönüşümlü swing önceliği: Low"
    assert high_distance > 0
    assert low_distance > 0

    accepted, appended = store.add_pivot(side="low", price=98.0, source_bar=12, confirm_bar=17)
    assert (accepted, appended) == (True, True)
    assert store.last_accepted_pivot_type == -1


def test_same_bar_neutral_start_uses_candle_tiebreak_deterministically() -> None:
    store = PivotStore(PatternCompressionConfig(profile=PROFILE_BALANCED))
    selected, reason, high_distance, low_distance = store.choose_same_bar_pivot(
        high_candidate=102.0,
        low_candidate=98.0,
        source_atr=2.0,
        source_open=99.0,
        source_high=102.0,
        source_low=98.0,
        source_close=101.0,
    )
    assert high_distance == pytest.approx(low_distance)
    assert selected == 1
    assert reason == "Yakın mesafe; güçlü yükseliş mumu"


def test_touch_spacing_and_chronology_match_source_rules() -> None:
    store = PivotStore(
        PatternCompressionConfig(
            profile=PROFILE_BALANCED,
            use_manual=True,
            manual_min_touch_gap=5,
        )
    )
    for bar, price in ((10, 100.0), (12, 100.1), (15, 100.0), (20, 100.0)):
        store.high_prices.append(price)
        store.high_bars.append(bar)
        store.high_confirm_bars.append(bar + 5)
        store.high_locked.append(False)

    touches, avg_distance, first_touch, last_touch = store.touch_stats(
        side="high",
        x1=10,
        y1=100.0,
        x2=20,
        y2=100.0,
        start_bar=10,
        end_bar=20,
        tolerance=0.2,
    )

    assert touches == 3
    assert first_touch == 10
    assert last_touch == 20
    assert avg_distance >= 0.0
    assert chronological_swings(10, 20, 15, 25)
    assert chronological_swings(15, 25, 10, 20)
    assert not chronological_swings(10, 15, 20, 25)


def test_lock_used_pivots_locks_only_candidate_identity_bars() -> None:
    store = PivotStore()
    for side, price, bar in (
        ("high", 100.0, 10),
        ("low", 95.0, 15),
        ("high", 99.0, 20),
        ("low", 96.0, 25),
    ):
        store.add_pivot(side=side, price=price, source_bar=bar, confirm_bar=bar + 5)

    candidate = PatternCandidate(hb1=10, hb2=20, lb1=15, lb2=25)
    assert store.lock_used_pivots(candidate) == 4
    assert all(store.high_locked)
    assert all(store.low_locked)
    assert store.lock_used_pivots(candidate) == 0
