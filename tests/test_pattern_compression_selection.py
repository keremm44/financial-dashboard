import pytest

from financial_dashboard.engines.pattern_compression_core import (
    PATTERN_ASCENDING_TRIANGLE,
    PATTERN_DESCENDING_TRIANGLE,
    PATTERN_SYMMETRICAL_TRIANGLE,
    PROFILE_BALANCED,
    ST_DEFINED,
    ST_PREP,
    PatternCandidate,
    PatternCompressionConfig,
)
from financial_dashboard.engines.pattern_compression_selection import (
    CompletedPatternReference,
    candidate_preferred,
    continuity_score,
    identity_compatible,
    overlap_ratio,
    pivot_overlap_count,
    quality_priority_gap,
    replacement_margin,
    same_completed_structure,
    selection_score,
    should_replace_active,
)


def _candidate(
    *,
    pattern_type: str = PATTERN_ASCENDING_TRIANGLE,
    family: str = "Üçgen",
    direction: int = 1,
    quality: float = 60.0,
    selection: float = 10.0,
    identity: int = 0,
    start: int = 10,
    end: int = 40,
    shift: float = 0.0,
) -> PatternCandidate:
    return PatternCandidate(
        valid=True,
        identity=identity,
        pattern_type=pattern_type,
        family=family,
        classic_dir=direction,
        raw_quality=quality,
        selection_score=selection,
        geometry_atr=4.0,
        start_bar=start,
        end_bar=end,
        hb1=10,
        hp1=110.0 + shift,
        hb2=30,
        hp2=108.0 + shift,
        lb1=20,
        lp1=100.0 + shift,
        lb2=40,
        lp2=104.0 + shift,
        upper_now=107.0 + shift,
        lower_now=106.0 + shift,
    )


def test_identity_compatibility_uses_family_and_classic_direction() -> None:
    current = _candidate()
    assert identity_compatible(current, _candidate(pattern_type=PATTERN_SYMMETRICAL_TRIANGLE, direction=0))
    assert not identity_compatible(current, _candidate(pattern_type=PATTERN_DESCENDING_TRIANGLE, direction=-1))
    assert not identity_compatible(current, _candidate(family="Kama"))


def test_pivot_overlap_and_overlap_ratio_match_source_math() -> None:
    first = _candidate()
    second = _candidate()
    assert pivot_overlap_count(first, second) == 4
    assert overlap_ratio(110.0, 100.0, 108.0, 102.0, min_tick=0.01) == pytest.approx(0.6)
    assert overlap_ratio(100.0, 100.0, 100.0, 100.0, min_tick=0.01) == 0.0


def test_identical_structure_continuity_reaches_full_100() -> None:
    config = PatternCompressionConfig(profile=PROFILE_BALANCED)
    current = _candidate()
    incoming = _candidate()
    score = continuity_score(current, incoming, bar_index=45, safe_atr=4.0, config=config)
    assert score == pytest.approx(100.0)


def test_incompatible_structure_has_zero_continuity() -> None:
    config = PatternCompressionConfig(profile=PROFILE_BALANCED)
    current = _candidate(direction=1)
    incoming = _candidate(direction=-1)
    assert continuity_score(current, incoming, bar_index=45, safe_atr=4.0, config=config) == 0.0


def test_selection_score_does_not_readd_formation_quality() -> None:
    config = PatternCompressionConfig(profile=PROFILE_BALANCED)
    active = PatternCandidate()
    low_quality = _candidate(quality=40.0)
    high_quality = _candidate(quality=90.0)
    low_score = selection_score(
        low_quality,
        active=active,
        pattern_state=ST_DEFINED,
        bar_index=45,
        close=106.5,
        safe_atr=4.0,
        config=config,
    )
    high_score = selection_score(
        high_quality,
        active=active,
        pattern_state=ST_DEFINED,
        bar_index=45,
        close=106.5,
        safe_atr=4.0,
        config=config,
    )
    assert low_score == pytest.approx(high_score)


def test_quality_gap_beats_selection_priority() -> None:
    config = PatternCompressionConfig(profile=PROFILE_BALANCED)
    current_best = _candidate(quality=60.0, selection=50.0)
    incoming = _candidate(quality=66.0, selection=-20.0)
    assert quality_priority_gap(config) == 6.0
    assert candidate_preferred(incoming, current_best, config=config)


def test_near_quality_uses_selection_priority() -> None:
    config = PatternCompressionConfig(profile=PROFILE_BALANCED)
    current_best = _candidate(quality=60.0, selection=10.0)
    incoming = _candidate(quality=63.0, selection=20.0)
    assert candidate_preferred(incoming, current_best, config=config)


def test_replacement_requires_quality_gap_or_state_margin() -> None:
    config = PatternCompressionConfig(profile=PROFILE_BALANCED)
    active = _candidate(quality=60.0, selection=20.0)

    better = _candidate(quality=66.0, selection=-50.0)
    replace, reason = should_replace_active(
        active,
        better,
        state=ST_PREP,
        lifecycle_can_update=True,
        terminal=False,
        config=config,
    )
    assert replace and reason == "Yok"

    near_but_not_enough = _candidate(quality=62.0, selection=33.9)
    replace, reason = should_replace_active(
        active,
        near_but_not_enough,
        state=ST_PREP,
        lifecycle_can_update=True,
        terminal=False,
        config=config,
    )
    assert not replace
    assert reason == "Selection priority marjı yetersiz: +14.0"

    near_and_context_wins = _candidate(quality=62.0, selection=34.0)
    replace, _ = should_replace_active(
        active,
        near_and_context_wins,
        state=ST_PREP,
        lifecycle_can_update=True,
        terminal=False,
        config=config,
    )
    assert replace


def test_lifecycle_lock_prevents_replacement() -> None:
    config = PatternCompressionConfig(profile=PROFILE_BALANCED)
    active = _candidate(quality=60.0, selection=20.0)
    incoming = _candidate(quality=90.0, selection=90.0)
    replace, reason = should_replace_active(
        active,
        incoming,
        state=ST_PREP,
        lifecycle_can_update=False,
        terminal=False,
        config=config,
    )
    assert not replace
    assert reason == "Kırılım lifecycle kilidi"


def test_replacement_margin_matches_state_specific_source_values() -> None:
    assert replacement_margin(ST_PREP) == 14.0
    assert replacement_margin(ST_DEFINED) == 8.0
    assert replacement_margin("OTHER") == 6.0


def test_completed_structure_overlap_blocks_same_geometry() -> None:
    config = PatternCompressionConfig(profile=PROFILE_BALANCED)
    original = _candidate()
    completed = CompletedPatternReference.from_candidate(original, safe_atr=4.0, min_tick=config.min_tick)
    assert same_completed_structure(original, completed, safe_atr=4.0, config=config)

    shifted = _candidate(shift=8.0)
    assert not same_completed_structure(shifted, completed, safe_atr=4.0, config=config)
