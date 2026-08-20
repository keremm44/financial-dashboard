import pytest

from financial_dashboard.engines.pattern_compression_core import (
    PATTERN_ASCENDING_TRIANGLE,
    PROFILE_BALANCED,
    ST_BREAK_ATTEMPT,
    ST_BREAK_CANDIDATE,
    ST_BREAK_CONFIRMED,
    ST_BREAK_FAILED,
    ST_BREAK_TIMEOUT,
    ST_COMPLETED,
    ST_PREP,
    ST_RETESTING,
    ST_RETEST_OK,
    ST_RETEST_WAIT,
    PatternCandidate,
    PatternCompressionConfig,
)
from financial_dashboard.engines.pattern_compression_runtime import (
    LifecycleBar,
    PatternLifecycleConfig,
    PatternLifecycleRuntime,
    breakout_strength,
)


def _candidate() -> PatternCandidate:
    return PatternCandidate(
        valid=True,
        identity=7,
        pattern_type=PATTERN_ASCENDING_TRIANGLE,
        family="Üçgen",
        classic_dir=1,
        raw_quality=72.0,
        geometry_atr=2.0,
        hb1=0,
        hp1=110.0,
        hb2=10,
        hp2=110.0,
        lb1=0,
        lp1=100.0,
        lb2=10,
        lp2=105.0,
        upper_now=110.0,
        lower_now=105.0,
        start_bar=0,
        end_bar=10,
    )


def _bar(
    index: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    atr: float = 2.0,
    volume: float | None = 1000.0,
    volume_sma: float | None = 1000.0,
) -> LifecycleBar:
    return LifecycleBar(
        bar_index=index,
        open=open_,
        high=high,
        low=low,
        close=close,
        atr=atr,
        volume=volume,
        volume_sma=volume_sma,
    )


def test_breakout_strength_matches_weighted_source_components() -> None:
    bar = _bar(20, open_=110.0, high=112.2, low=109.8, close=112.0)
    result = breakout_strength(
        direction=1,
        boundary=110.0,
        bar=bar,
        base_break_atr=0.06,
        min_tick=0.01,
    )

    expected = (
        result.body_score * 0.24
        + result.close_score * 0.28
        + result.penetration_score * 0.25
        + result.expansion_score * 0.15
        + result.volume_score * 0.08
    )
    assert result.strength == pytest.approx(expected)
    assert result.strength >= 50.0


def test_missing_volume_uses_neutral_fifty_score() -> None:
    result = breakout_strength(
        direction=1,
        boundary=110.0,
        bar=_bar(20, open_=110.0, high=112.0, low=109.5, close=111.8, volume=None, volume_sma=None),
        base_break_atr=0.06,
        min_tick=0.01,
    )
    assert result.volume_score == 50.0


def test_strong_closed_break_starts_candidate_and_freezes_quality_snapshot() -> None:
    runtime = PatternLifecycleRuntime(_candidate(), state=ST_PREP)
    snapshot = runtime.update_closed(_bar(20, open_=110.0, high=112.2, low=109.8, close=112.0))

    assert snapshot.state == ST_BREAK_CANDIDATE
    assert snapshot.break_direction == 1
    assert snapshot.break_candidate_bar == 20
    assert snapshot.candidate.quality_frozen
    assert snapshot.candidate.frozen_raw_quality == 72.0
    assert snapshot.candidate.frozen_upper_boundary_at_break == 110.0
    assert snapshot.candidate.frozen_break_buffer == pytest.approx(0.12)
    assert snapshot.candidate.frozen_retest_tolerance == pytest.approx(0.30)
    assert snapshot.candidate.frozen_atr_at_break == 2.0
    assert snapshot.candidate.break_snapshot_direction == 1
    assert snapshot.candidate.break_snapshot_price == 110.0
    assert snapshot.candidate.break_strength is not None


def test_weak_closed_break_starts_attempt_not_candidate() -> None:
    runtime = PatternLifecycleRuntime(_candidate(), state=ST_PREP)
    snapshot = runtime.update_closed(_bar(20, open_=110.14, high=110.20, low=109.90, close=110.15))

    assert snapshot.state == ST_BREAK_ATTEMPT
    assert snapshot.break_direction == 1
    assert snapshot.invalid_reason == "Sınır dışı kapanış var; kırılım gücü teyit bekliyor"


def test_candidate_confirms_on_next_strong_same_side_close() -> None:
    runtime = PatternLifecycleRuntime(_candidate(), state=ST_PREP)
    first = runtime.update_closed(_bar(20, open_=110.0, high=112.2, low=109.8, close=112.0))
    assert first.state == ST_BREAK_CANDIDATE

    second = runtime.update_closed(_bar(21, open_=111.0, high=112.5, low=110.8, close=112.2))
    assert second.state == ST_BREAK_CONFIRMED
    assert second.break_confirmed_bar == 21
    assert second.candidate.break_confirmation_strength is not None


def test_attempt_failure_preserves_attempt_specific_reason() -> None:
    runtime = PatternLifecycleRuntime(_candidate(), state=ST_PREP)
    first = runtime.update_closed(_bar(20, open_=110.14, high=110.20, low=109.90, close=110.15))
    assert first.state == ST_BREAK_ATTEMPT

    failed = runtime.update_closed(_bar(21, open_=110.0, high=110.1, low=109.0, close=109.5))
    assert failed.state == ST_BREAK_FAILED
    assert failed.invalid_reason == "Kırılım denemesi formasyon içine döndü"


def test_candidate_failure_uses_candidate_specific_reason() -> None:
    runtime = PatternLifecycleRuntime(_candidate(), state=ST_PREP)
    assert runtime.update_closed(_bar(20, open_=110.0, high=112.2, low=109.8, close=112.0)).state == ST_BREAK_CANDIDATE

    failed = runtime.update_closed(_bar(21, open_=110.0, high=110.1, low=109.0, close=109.5))
    assert failed.state == ST_BREAK_FAILED
    assert failed.invalid_reason == "Teyitsiz kırılım formasyon içine döndü"


def test_weak_attempt_times_out_after_confirm_window() -> None:
    runtime = PatternLifecycleRuntime(_candidate(), state=ST_PREP)
    assert runtime.update_closed(_bar(20, open_=110.14, high=110.20, low=109.90, close=110.15)).state == ST_BREAK_ATTEMPT
    assert runtime.update_closed(_bar(21, open_=110.02, high=110.10, low=110.00, close=110.02)).state == ST_BREAK_ATTEMPT
    assert runtime.update_closed(_bar(22, open_=110.02, high=110.10, low=110.00, close=110.02)).state == ST_BREAK_ATTEMPT

    timed_out = runtime.update_closed(_bar(23, open_=110.02, high=110.10, low=110.00, close=110.02))
    assert timed_out.state == ST_BREAK_TIMEOUT
    assert timed_out.invalid_reason == "Kırılım denemesi güçlenmedi"


def test_confirmed_break_retest_hold_completes_after_profile_hold_window() -> None:
    runtime = PatternLifecycleRuntime(_candidate(), state=ST_PREP)
    runtime.update_closed(_bar(20, open_=110.0, high=112.2, low=109.8, close=112.0))
    assert runtime.update_closed(_bar(21, open_=111.0, high=112.5, low=110.8, close=112.2)).state == ST_BREAK_CONFIRMED

    retest = runtime.update_closed(_bar(22, open_=110.5, high=110.8, low=109.9, close=110.5))
    assert retest.state == ST_RETEST_OK
    assert retest.retest_success_bar == 22

    assert runtime.update_closed(_bar(23, open_=110.4, high=110.8, low=110.3, close=110.5)).state == ST_RETEST_OK
    assert runtime.update_closed(_bar(24, open_=110.4, high=110.8, low=110.3, close=110.5)).state == ST_RETEST_OK
    completed = runtime.update_closed(_bar(25, open_=110.4, high=110.8, low=110.3, close=110.5))
    assert completed.state == ST_COMPLETED


def test_confirmed_break_without_retest_completes_after_retest_window() -> None:
    runtime = PatternLifecycleRuntime(_candidate(), state=ST_PREP)
    runtime.update_closed(_bar(20, open_=110.0, high=112.2, low=109.8, close=112.0))
    runtime.update_closed(_bar(21, open_=111.0, high=112.5, low=110.8, close=112.2))

    for index in range(22, 27):
        snapshot = runtime.update_closed(_bar(index, open_=112.0, high=112.4, low=111.8, close=112.1))
        assert snapshot.state == ST_RETEST_WAIT
    completed = runtime.update_closed(_bar(27, open_=112.0, high=112.4, low=111.8, close=112.1))
    assert completed.state == ST_COMPLETED


def test_retest_touch_without_hold_enters_retesting() -> None:
    runtime = PatternLifecycleRuntime(_candidate(), state=ST_PREP)
    runtime.update_closed(_bar(20, open_=110.0, high=112.2, low=109.8, close=112.0))
    runtime.update_closed(_bar(21, open_=111.0, high=112.5, low=110.8, close=112.2))

    snapshot = runtime.update_closed(_bar(22, open_=110.1, high=110.2, low=109.9, close=110.01))
    assert snapshot.state == ST_RETESTING


def test_counter_break_direction_is_independent_from_classic_direction() -> None:
    runtime = PatternLifecycleRuntime(_candidate(), state=ST_PREP)
    snapshot = runtime.update_closed(_bar(20, open_=106.0, high=106.2, low=103.0, close=103.2))

    assert snapshot.candidate.classic_dir == 1
    assert snapshot.break_direction == -1
    assert snapshot.state == ST_BREAK_CANDIDATE


def test_live_wick_break_only_previews_attempt_and_does_not_mutate_state() -> None:
    runtime = PatternLifecycleRuntime(_candidate(), state=ST_PREP)
    before = runtime.snapshot()
    preview = runtime.preview_state(_bar(20, open_=109.8, high=110.5, low=109.5, close=109.9))
    after = runtime.snapshot()

    assert preview == ST_BREAK_ATTEMPT
    assert before.state == after.state == ST_PREP
    assert not after.candidate.quality_frozen
