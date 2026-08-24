from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDomain
from financial_dashboard.context.volatility_environment_projection import (
    ExpansionCharacter,
    VolatilityRangeRegime,
    VolatilityTransitionStage,
    project_volatility_environment,
)
from financial_dashboard.engines.volatility_bands_fib_engine import (
    BandAgreement,
    BandState,
    VolatilityState,
)
from financial_dashboard.engines.volatility_direction_transition import EarlyDirectionTransition


NOW = datetime(2026, 8, 24, 18, 0, tzinfo=timezone.utc)


def _available_at(timestamp, timeframe):
    assert timeframe == "4h"
    return timestamp + timedelta(minutes=2)


def _replay(
    *,
    regime=VolatilityState.UP_CONFIRMED,
    band_state=BandState.UPPER_TREND,
    band_agreement=BandAgreement.UP,
    early_state=EarlyDirectionTransition.NONE,
    data_quality="OK",
):
    export = SimpleNamespace(
        regime=int(regime),
        direction=1.0,
        quality=82.0,
        band_state=int(band_state),
        band_agreement=int(band_agreement),
        fib_state=5,
        data_quality=data_quality,
        structure_state=3,
        structure_fib_alignment=3,
        direction_bias=1,
        coherence=1,
        regime_band_family_quality=84.0,
        structure_swing_family_quality=78.0,
        fib_retracement_ratio=0.42,
    )
    early = SimpleNamespace(
        state=early_state,
        episode_id=7,
        episode_started=early_state is not EarlyDirectionTransition.NONE,
        evidence_count=3,
    )
    latest = SimpleNamespace(timestamp=NOW, confirmed_export=export, early=early)
    tf_replay = SimpleNamespace(latest=latest)
    return SimpleNamespace(
        symbol="ASELS",
        timeframes=("4h",),
        for_timeframe=lambda timeframe: tf_replay,
    )


def test_environment_preserves_regime_band_and_transition_as_separate_dimensions() -> None:
    replay = _replay()
    projection = project_volatility_environment(replay, available_at=_available_at)
    assert projection is not None
    row = projection.for_timeframe("4h")

    assert row.ref.domain is ContextDomain.VOLATILITY
    assert row.ref.fact_type == "VOLATILITY_ENVIRONMENT"
    assert row.ref.available_at == NOW + timedelta(minutes=2)
    assert row.range_regime is VolatilityRangeRegime.EXPANDING
    assert row.expansion_character is ExpansionCharacter.BAND_TREND
    assert row.transition_stage is VolatilityTransitionStage.CONFIRMED
    assert row.expansion_direction == 1
    assert row.band_agreement_code == int(BandAgreement.UP)
    assert row.regime_band_family_quality == 84.0
    assert row.fib_retracement_ratio == 0.42


def test_false_excursion_is_not_called_directional_confirmation() -> None:
    replay = _replay(
        regime=VolatilityState.BALANCED,
        band_state=BandState.UPPER_FALSE_EXCURSION,
        band_agreement=BandAgreement.MEAN_REVERSION,
    )
    projection = project_volatility_environment(replay, available_at=_available_at)
    assert projection is not None
    row = projection.for_timeframe("4h")

    assert row.range_regime is VolatilityRangeRegime.BALANCED
    assert row.expansion_character is ExpansionCharacter.FALSE_EXCURSION
    assert row.transition_stage is VolatilityTransitionStage.NONE
    assert row.expansion_direction == 0


def test_early_episode_stays_early_until_canonical_candidate() -> None:
    replay = _replay(
        regime=VolatilityState.CONTRACTING,
        band_state=BandState.BASIS_BALANCE,
        band_agreement=BandAgreement.CONTRACTION,
        early_state=EarlyDirectionTransition.EARLY_UP,
    )
    projection = project_volatility_environment(replay, available_at=_available_at)
    assert projection is not None
    row = projection.for_timeframe("4h")

    assert row.range_regime is VolatilityRangeRegime.CONTRACTING
    assert row.transition_stage is VolatilityTransitionStage.EARLY_EPISODE
    assert row.expansion_direction == 0


def test_warmup_is_unavailable_not_balanced() -> None:
    replay = _replay(data_quality="WARMUP")
    projection = project_volatility_environment(replay, available_at=_available_at)
    assert projection is not None
    row = projection.for_timeframe("4h")

    assert row.range_regime is VolatilityRangeRegime.UNAVAILABLE
    assert row.expansion_character is ExpansionCharacter.UNAVAILABLE
    assert row.transition_stage is VolatilityTransitionStage.UNAVAILABLE
    assert row.expansion_direction == 0


def test_environment_projection_respects_knowledge_boundary() -> None:
    replay = _replay()
    projection = project_volatility_environment(replay, available_at=_available_at)
    assert projection is not None

    assert projection.available_at(NOW).timeframe_facts == ()
    assert len(projection.available_at(NOW + timedelta(minutes=2)).timeframe_facts) == 1
