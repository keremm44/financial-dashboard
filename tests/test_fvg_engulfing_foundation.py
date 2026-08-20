from __future__ import annotations

import pytest

from financial_dashboard.engines import (
    EngulfingFormation,
    FormationSnapshot,
    FvgEngulfingEngine,
    FvgFormation,
)
from financial_dashboard.engines.fvg_engulfing_models import (
    ATR_LENGTH,
    ENGULFING_DEEP_RETRACE_THRESHOLD,
    ENGULFING_PARTIAL_RETRACE_THRESHOLD,
    FLOW_LENGTH,
    FVG_CANDIDATE_QUALITY_OFFSET,
    FVG_CANDIDATE_SIZE_FACTOR,
    FVG_TAKEOVER_DISTANCE_MARGIN,
    FVG_TAKEOVER_QUALITY_MARGIN,
    FvgDirection,
    FvgEngulfingConfig,
    FvgState,
    EngulfingDirection,
    EngulfingState,
    LOCAL_CONTEXT_LENGTH,
    MINIMUM_HISTORY_BARS,
    SensitivityProfile,
    SUPPORTED_TIMEFRAMES,
)


def test_source_state_codes_are_frozen() -> None:
    assert [state.value for state in FvgState] == list(range(1, 12))
    assert FvgDirection.NONE == 0
    assert FvgDirection.BULLISH == 1
    assert FvgDirection.BEARISH == -1

    assert [state.value for state in EngulfingState] == list(range(0, 8))
    assert EngulfingDirection.NONE == 0
    assert EngulfingDirection.BULLISH == 1
    assert EngulfingDirection.BEARISH == -1


def test_supported_timeframes_match_pine_guard() -> None:
    assert SUPPORTED_TIMEFRAMES == frozenset({"2h", "4h", "1d"})
    assert FvgEngulfingConfig(timeframe="2H").timeframe == "2h"
    assert FvgEngulfingConfig(timeframe="4h").timeframe == "4h"
    assert FvgEngulfingConfig(timeframe="1D").timeframe == "1d"
    with pytest.raises(ValueError):
        FvgEngulfingConfig(timeframe="1h")


def test_sensitivity_profiles_match_source_labels() -> None:
    assert [profile.value for profile in SensitivityProfile] == ["Hassas", "Dengeli", "Seçici"]
    assert FvgEngulfingConfig().sensitivity is SensitivityProfile.BALANCED


def test_central_source_constants_are_frozen() -> None:
    assert ATR_LENGTH == 14
    assert FLOW_LENGTH == 4
    assert LOCAL_CONTEXT_LENGTH == 4
    assert MINIMUM_HISTORY_BARS == 100
    assert FVG_CANDIDATE_SIZE_FACTOR == pytest.approx(0.70)
    assert FVG_CANDIDATE_QUALITY_OFFSET == pytest.approx(12.0)
    assert FVG_TAKEOVER_QUALITY_MARGIN == pytest.approx(4.0)
    assert FVG_TAKEOVER_DISTANCE_MARGIN == pytest.approx(0.20)
    assert ENGULFING_PARTIAL_RETRACE_THRESHOLD == pytest.approx(0.25)
    assert ENGULFING_DEEP_RETRACE_THRESHOLD == pytest.approx(0.50)


def test_minimum_tick_must_be_positive() -> None:
    with pytest.raises(ValueError):
        FvgEngulfingConfig(minimum_tick=0.0)


def test_final_public_surface_keeps_tur1_formation_contract_and_adds_lifecycle_facade() -> None:
    assert FvgEngulfingEngine.__module__ == "financial_dashboard.engines.fvg_engulfing_final"
    assert FvgFormation.__name__ == "FvgFormation"
    assert EngulfingFormation.__name__ == "EngulfingFormation"
    assert FormationSnapshot.__name__ == "FormationSnapshot"
