from __future__ import annotations

import pytest

from financial_dashboard.data.quality import DataQualityStatus
from financial_dashboard.engines.market_structure_evidence import HANDSHAKE, MarketStructureExport
from financial_dashboard.engines.models import Direction, EngineResult
from financial_dashboard.engines.mtf_story_models import RawTimeframeEvidence, TimeframeRole
from financial_dashboard.engines.mtf_story_normalizer import (
    MTFStoryNormalizationError,
    normalize_timeframe_evidence,
)
from financial_dashboard.engines.pattern_compression_engine import PatternExport


def _ms_result(*, state="STATE_BEARISH", direction=Direction.DOWN, score=87, quality=96):
    return EngineResult(
        engine="market_structure",
        state=state,
        timestamp="2026-08-19T17:00:00+03:00",
        direction=direction,
        score=score,
        quality=quality,
    )


def _pattern_result(*, state="KIRILIM_TEYITLI", direction=Direction.UP, quality=72.0):
    return EngineResult(
        engine="pattern_compression",
        state=state,
        timestamp="2026-08-19T17:00:00+03:00",
        direction=direction,
        score=quality,
        quality=quality,
    )


def _ms_export(code=-2.0, score=87, handshake=HANDSHAKE):
    return MarketStructureExport(
        external_state=code,
        internal_state=code,
        evidence_score=score,
        external_protected_low=None,
        external_protected_high=318.25 if code < 0 else None,
        external_weak_low=295.25 if code < 0 else None,
        external_weak_high=None,
        internal_protected_low=None,
        internal_protected_high=303.25 if code < 0 else None,
        internal_weak_low=298.5 if code < 0 else None,
        internal_weak_high=None,
        handshake=handshake,
    )


def _raw(**overrides):
    values = dict(
        timeframe="1h",
        role=TimeframeRole.TACTICAL_STRUCTURE,
        data_quality=DataQualityStatus.OK,
        market_structure=_ms_result(),
        market_structure_export=_ms_export(),
        pattern_compression=_pattern_result(),
        pattern_export=PatternExport(
            state=9,
            pattern_type=3,
            quality=72.0,
            classic_direction=0,
            break_state=3,
            break_level=300.75,
            break_strength=35.9,
            retest_state=1,
            retest_tolerance=0.26,
            identity=5.0,
        ),
    )
    values.update(overrides)
    return RawTimeframeEvidence(**values)


def test_normalizer_preserves_bearish_structure_and_bullish_actual_breakout_separately() -> None:
    state = normalize_timeframe_evidence(_raw())

    assert state.structural_direction is Direction.DOWN
    assert state.structural_state == "STATE_BEARISH"
    assert state.pattern_direction is Direction.UP
    assert state.pattern_classic_direction is Direction.NEUTRAL
    assert state.breakout_direction is Direction.UP
    assert state.pattern_state == "KIRILIM_TEYITLI"
    assert state.pattern_type == "Simetrik Üçgen"
    assert state.pattern_quality == 72.0


def test_normalizer_preserves_bullish_structure_and_bearish_breakout_separately() -> None:
    evidence = _raw(
        market_structure=_ms_result(state="STATE_BULLISH", direction=Direction.UP, score=85, quality=95),
        market_structure_export=_ms_export(code=2.0, score=85),
        pattern_compression=_pattern_result(state="FORMASYON_TAMAMLANDI", direction=Direction.DOWN, quality=75.8),
        pattern_export=PatternExport(
            state=13,
            pattern_type=4,
            quality=75.8,
            classic_direction=-1,
            break_state=-4,
            break_level=399.36,
            break_strength=25.8,
            retest_state=4,
            retest_tolerance=0.72,
            identity=4.0,
        ),
    )
    state = normalize_timeframe_evidence(evidence)

    assert state.structural_direction is Direction.UP
    assert state.pattern_classic_direction is Direction.DOWN
    assert state.breakout_direction is Direction.DOWN
    assert state.pattern_direction is Direction.DOWN
    assert state.pattern_type == "Yükselen Kama"


def test_normalizer_uses_market_export_when_engine_result_is_missing() -> None:
    state = normalize_timeframe_evidence(
        _raw(
            market_structure=None,
            market_structure_export=_ms_export(code=1.0, score=61),
            pattern_compression=None,
            pattern_export=None,
        )
    )

    assert state.structural_state == "STATE_TRANSITION_UP"
    assert state.structural_direction is Direction.UP
    assert state.structural_score == 61
    assert state.pattern_state is None


def test_normalizer_uses_pattern_export_when_pattern_result_is_missing() -> None:
    state = normalize_timeframe_evidence(
        _raw(
            pattern_compression=None,
            pattern_export=PatternExport(
                state=6,
                pattern_type=1,
                quality=68.0,
                classic_direction=1,
                break_state=0,
                break_level=None,
                break_strength=None,
                retest_state=0,
                retest_tolerance=0.2,
                identity=2.0,
            ),
        )
    )

    assert state.pattern_state == "KIRILIM_HAZIRLIGI"
    assert state.pattern_type == "Yükselen Üçgen"
    assert state.pattern_classic_direction is Direction.UP
    assert state.breakout_direction is Direction.NEUTRAL
    assert state.pattern_direction is Direction.UP


def test_classic_direction_does_not_become_actual_breakout_direction_before_break() -> None:
    state = normalize_timeframe_evidence(
        _raw(
            pattern_compression=_pattern_result(state="KIRILIM_HAZIRLIGI", direction=Direction.UP, quality=68.0),
            pattern_export=PatternExport(
                state=6,
                pattern_type=1,
                quality=68.0,
                classic_direction=1,
                break_state=0,
                break_level=None,
                break_strength=None,
                retest_state=0,
                retest_tolerance=0.2,
                identity=2.0,
            ),
        )
    )

    assert state.pattern_classic_direction is Direction.UP
    assert state.pattern_direction is Direction.UP
    assert state.breakout_direction is Direction.NEUTRAL


def test_pattern_break_state_sign_decodes_actual_direction_for_all_lifecycle_magnitudes() -> None:
    for break_state in (1, 2, 3, 4, 5, 6):
        up = normalize_timeframe_evidence(
            _raw(pattern_export=PatternExport(state=9, pattern_type=3, quality=70, classic_direction=0, break_state=break_state))
        )
        down = normalize_timeframe_evidence(
            _raw(
                pattern_compression=_pattern_result(direction=Direction.DOWN),
                pattern_export=PatternExport(state=9, pattern_type=3, quality=70, classic_direction=0, break_state=-break_state),
            )
        )
        assert up.breakout_direction is Direction.UP
        assert down.breakout_direction is Direction.DOWN


def test_limited_and_invalid_quality_are_carried_without_reinterpretation() -> None:
    limited = normalize_timeframe_evidence(_raw(data_quality=DataQualityStatus.LIMITED))
    invalid = normalize_timeframe_evidence(_raw(data_quality=DataQualityStatus.INVALID))

    assert limited.usable is True
    assert limited.reasons[-1] == "DATA_LIMITED"
    assert invalid.usable is False
    assert invalid.reasons[-1] == "DATA_INVALID"


def test_missing_both_engines_produces_neutral_empty_state_not_fabricated_signal() -> None:
    state = normalize_timeframe_evidence(
        _raw(
            market_structure=None,
            market_structure_export=None,
            pattern_compression=None,
            pattern_export=None,
        )
    )

    assert state.structural_direction is Direction.NEUTRAL
    assert state.structural_state is None
    assert state.pattern_direction is Direction.NEUTRAL
    assert state.pattern_state is None
    assert state.pattern_type is None
    assert state.breakout_direction is Direction.NEUTRAL


def test_market_state_export_mismatch_is_reported_without_overwriting_engine_result() -> None:
    state = normalize_timeframe_evidence(
        _raw(market_structure_export=_ms_export(code=2.0, score=87))
    )

    assert state.structural_state == "STATE_BEARISH"
    assert state.structural_direction is Direction.DOWN
    assert "MARKET_STATE_EXPORT_MISMATCH:STATE_BEARISH!=STATE_BULLISH" in state.reasons


def test_pattern_state_export_mismatch_is_reported_without_overwriting_engine_result() -> None:
    state = normalize_timeframe_evidence(
        _raw(pattern_export=PatternExport(state=6, pattern_type=3, quality=72, classic_direction=0, break_state=0))
    )

    assert state.pattern_state == "KIRILIM_TEYITLI"
    assert "PATTERN_STATE_EXPORT_MISMATCH:KIRILIM_TEYITLI!=KIRILIM_HAZIRLIGI" in state.reasons


def test_wrong_engine_result_type_is_rejected() -> None:
    wrong = EngineResult(engine="pattern_compression", state="x", timestamp=None)
    with pytest.raises(MTFStoryNormalizationError, match="expected market_structure"):
        normalize_timeframe_evidence(_raw(market_structure=wrong))


def test_wrong_export_type_is_rejected() -> None:
    with pytest.raises(MTFStoryNormalizationError, match="expected PatternExport"):
        normalize_timeframe_evidence(_raw(pattern_export=object()))


def test_invalid_market_structure_handshake_is_rejected() -> None:
    with pytest.raises(MTFStoryNormalizationError, match="invalid Market Structure export handshake"):
        normalize_timeframe_evidence(_raw(market_structure_export=_ms_export(handshake=123.0)))


@pytest.mark.parametrize("code", [3.0, -3.0, 1.5])
def test_unknown_market_state_code_is_rejected(code) -> None:
    with pytest.raises(MTFStoryNormalizationError, match="unsupported Market Structure state code"):
        normalize_timeframe_evidence(_raw(market_structure=None, market_structure_export=_ms_export(code=code)))


@pytest.mark.parametrize("state_code", [18, -1, 99])
def test_unknown_pattern_state_code_is_rejected(state_code) -> None:
    with pytest.raises(MTFStoryNormalizationError, match="unsupported Pattern state code"):
        normalize_timeframe_evidence(
            _raw(pattern_compression=None, pattern_export=PatternExport(state=state_code, pattern_type=1, quality=50, classic_direction=1, break_state=0))
        )


def test_unknown_pattern_type_code_is_rejected() -> None:
    with pytest.raises(MTFStoryNormalizationError, match="unsupported Pattern type code"):
        normalize_timeframe_evidence(
            _raw(pattern_compression=None, pattern_export=PatternExport(state=3, pattern_type=99, quality=50, classic_direction=0, break_state=0))
        )
