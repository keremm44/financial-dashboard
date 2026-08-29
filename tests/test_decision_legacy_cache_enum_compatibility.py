from types import SimpleNamespace

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.context.projections import (
    StructuralScopeProjection,
    StructuralTimeframeProjection,
)
from financial_dashboard.decision.environment import (
    EnvironmentAlignment,
    EnvironmentRisk,
    assess_environment,
)
from financial_dashboard.decision.execution import (
    ExecutionTriggerState,
    assess_execution_trigger,
)
from financial_dashboard.decision.participation import ParticipationState, assess_participation
from financial_dashboard.decision.reaction import ReactionState, assess_reaction
from financial_dashboard.decision.structural import (
    StructuralDirection,
    assess_short_term_structure,
)
from financial_dashboard.context.volatility_environment_projection import (
    ExpansionCharacter,
    VolatilityRangeRegime,
)


def _legacy_ref(*, domain="VOLATILITY", quality="VALID", timeframe="1h") -> FactRef:
    return FactRef(
        domain=domain,
        fact_type="LEGACY_CACHE_TEST",
        symbol="ASELS",
        timeframe=timeframe,
        native_id=f"legacy:{domain}:{timeframe}",
        native_state="READY",
        origin_time=1,
        confirmed_at=1,
        available_at=1,
        lineage_id=f"legacy:{domain}:{timeframe}",
        causal_family="REGIME",
        source_family="PRICE_GEOMETRY",
        data_quality=quality,
    )


def test_legacy_fact_ref_string_enums_hydrate_on_access() -> None:
    ref = _legacy_ref()
    assert ref.domain is ContextDomain.VOLATILITY
    assert ref.causal_family is CausalFamily.REGIME
    assert ref.source_family is SourceFamily.PRICE_GEOMETRY
    assert ref.data_quality is ContextDataQuality.VALID
    assert ref.deterministic_key[0] == "VOLATILITY"


def test_environment_accepts_legacy_cached_string_enums() -> None:
    row = SimpleNamespace(
        ref=_legacy_ref(),
        range_regime="BALANCED",
        expansion_character="NEUTRAL",
        expansion_direction=1,
    )
    projection = SimpleNamespace(for_timeframe=lambda timeframe: row)

    result = assess_environment(
        StructuralDirection.LONG,
        projection,
        timeframe="1h",
    )

    assert result.regime is VolatilityRangeRegime.BALANCED
    assert result.character is ExpansionCharacter.NEUTRAL
    assert result.data_quality is ContextDataQuality.VALID
    assert result.alignment is EnvironmentAlignment.ALIGNED
    assert result.risk is EnvironmentRisk.NORMAL


def test_environment_unknown_legacy_token_fails_closed_instead_of_crashing() -> None:
    row = SimpleNamespace(
        ref=_legacy_ref(),
        range_regime="OLD_UNKNOWN_REGIME",
        expansion_character="OLD_UNKNOWN_CHARACTER",
        expansion_direction=1,
    )
    projection = SimpleNamespace(for_timeframe=lambda timeframe: row)

    result = assess_environment(StructuralDirection.LONG, projection, timeframe="1h")

    assert result.regime is VolatilityRangeRegime.UNAVAILABLE
    assert result.character is ExpansionCharacter.UNAVAILABLE
    assert result.risk is EnvironmentRisk.UNKNOWN


def test_participation_accepts_legacy_cached_string_enums() -> None:
    row = SimpleNamespace(
        ref=_legacy_ref(domain="VOLUME"),
        status="READY",
        participation_trend="CONFIRMED",
        effort_result="EFFICIENT",
        break_participation="SUPPORTED",
        participation_direction=1,
        break_direction=1,
        evidence_direction=1,
        heavy_conflict=False,
        heavy_conflict_bars=0,
    )
    projection = SimpleNamespace(for_timeframe=lambda timeframe: row)

    result = assess_participation(
        StructuralDirection.LONG,
        projection,
        timeframe="1h",
    )

    assert result.state is ParticipationState.SUPPORTIVE
    assert result.data_quality is ContextDataQuality.VALID


def test_execution_accepts_legacy_cached_quality_string() -> None:
    result = assess_execution_trigger(
        StructuralDirection.LONG,
        as_of=10,
        timeframe="30m",
        data_quality="VALID",
        event=None,
    )
    assert result.state is ExecutionTriggerState.ABSENT

    limited = assess_execution_trigger(
        StructuralDirection.LONG,
        as_of=10,
        timeframe="30m",
        data_quality="DATA_LIMITED",
        event=None,
    )
    assert limited.state is ExecutionTriggerState.UNAVAILABLE


def test_structure_accepts_legacy_cached_quality_string() -> None:
    external = StructuralScopeProjection(
        scope="EXTERNAL",
        state="BULLISH",
        direction=1,
        protected_high=110.0,
        protected_low=95.0,
        weak_high=112.0,
        weak_low=94.0,
        strong_high_identity=1,
        strong_low_identity=2,
        protected_high_identity=3,
        protected_low_identity=4,
        weak_high_identity=5,
        weak_low_identity=6,
    )
    row = StructuralTimeframeProjection(
        timeframe="1h",
        as_of=10,
        data_quality="VALID",
        external=external,
        internal=None,
        events=(),
    )
    projection = SimpleNamespace(for_timeframe=lambda timeframe: row)

    result = assess_short_term_structure(projection)

    assert result.direction is StructuralDirection.LONG
    assert result.data_quality is ContextDataQuality.VALID


def test_reaction_treats_legacy_valid_fact_ref_as_usable_evidence() -> None:
    ref = _legacy_ref(domain="ORDER_BLOCK", timeframe="1h")
    observation = SimpleNamespace(
        timeframe="1h",
        bullish=True,
        ref=ref,
        bottom=95.0,
        top=100.0,
        state="REACTION_CONFIRMED",
        interaction="REACTION_CONFIRMED",
        identity="ob:legacy",
        active=True,
        terminal_reason=None,
    )
    projection = SimpleNamespace(observations=(observation,))

    result = assess_reaction(
        StructuralDirection.LONG,
        order_blocks=projection,
        timeframes=("1h",),
    )

    assert result.state is ReactionState.CONFIRMED
    assert result.confirmation_present is True
    assert result.data_quality is ContextDataQuality.VALID
