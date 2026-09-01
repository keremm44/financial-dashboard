from __future__ import annotations

from types import SimpleNamespace

import pytest

import financial_dashboard.decision.engine as engine_module
from financial_dashboard import decision_input
from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.context.projections import (
    StructuralEventProjection,
    StructuralFactsProjection,
    StructuralTimeframeProjection,
)
from financial_dashboard.decision.environment import (
    EnvironmentAlignment,
    EnvironmentAssessment,
    EnvironmentRisk,
)
from financial_dashboard.decision.participation import ParticipationAssessment, ParticipationState
from financial_dashboard.decision.structural import (
    DecisionHorizon,
    HorizonRelation,
    StructuralAssessment,
    StructuralDirection,
    ThesisState,
)
from financial_dashboard.decision.structure_projection import normalize_decision_structure_projection
from financial_dashboard.context.volatility_environment_projection import (
    ExpansionCharacter,
    VolatilityRangeRegime,
)


def _limited_structure() -> StructuralFactsProjection:
    ref = FactRef(
        domain=ContextDomain.MARKET_STRUCTURE,
        fact_type="STRUCTURE_EVENT",
        symbol="ASELS",
        timeframe="1h",
        native_id="MS:1h:1",
        native_state="BOS_UP",
        origin_time=10,
        confirmed_at=10,
        available_at=10,
        lineage_id="MS:ROOT",
        causal_family=CausalFamily.STRUCTURAL_LEVEL,
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=ContextDataQuality.DATA_LIMITED,
    )
    event = StructuralEventProjection(
        ref=ref,
        scope="EXTERNAL",
        event_type="BOS",
        direction=1,
        broken_level=100.0,
        origin_price=95.0,
        confirmation_status="CONFIRMED",
        validity="VALID",
        relevance="CURRENT",
        outcome="ACTIVE",
        bos_maturity="CONFIRMED",
    )
    row = StructuralTimeframeProjection(
        timeframe="1h",
        as_of=10,
        data_quality=ContextDataQuality.DATA_LIMITED,
        external=None,
        internal=None,
        events=(event,),
    )
    return StructuralFactsProjection(
        symbol="ASELS",
        timeframes=("1h",),
        timeframe_facts=(row,),
    )


def _structural(horizon: DecisionHorizon) -> StructuralAssessment:
    return StructuralAssessment(
        horizon=horizon,
        authority_timeframe="1d" if horizon is DecisionHorizon.LONG_TERM else "1h",
        direction=StructuralDirection.LONG,
        thesis_state=ThesisState.INTACT,
        native_state="STATE_BULLISH",
        transition_target=None,
        data_quality=ContextDataQuality.VALID,
        authority_as_of=10,
        protected_high=None,
        protected_low=None,
        weak_high=None,
        weak_low=None,
        source_refs=(),
        reasons=("TEST",),
    )


def _environment() -> EnvironmentAssessment:
    return EnvironmentAssessment(
        VolatilityRangeRegime.BALANCED,
        ExpansionCharacter.NEUTRAL,
        EnvironmentAlignment.NEUTRAL,
        EnvironmentRisk.NORMAL,
        ContextDataQuality.VALID,
        ("NORMAL",),
        (),
    )


def _participation() -> ParticipationAssessment:
    return ParticipationAssessment(
        ParticipationState.SUPPORTIVE,
        False,
        False,
        ContextDataQuality.VALID,
        ("SUPPORTIVE",),
        (),
    )


def _cached_snapshot(*, as_of: int = 10):
    lt_structural = _structural(DecisionHorizon.LONG_TERM)
    st_structural = _structural(DecisionHorizon.SHORT_TERM)
    lt_participation = _participation()
    st_participation = _participation()
    lt_environment = _environment()
    st_environment = _environment()
    market = SimpleNamespace(
        as_of=10,
        long_term=SimpleNamespace(
            horizon=DecisionHorizon.LONG_TERM,
            structural=lt_structural,
            participation=(("4h", lt_participation),),
            environment=(("4h", lt_environment),),
        ),
        short_term=SimpleNamespace(
            horizon=DecisionHorizon.SHORT_TERM,
            structural=st_structural,
            participation=(("1h", st_participation),),
            environment=(("4h", st_environment),),
        ),
        horizon_relation=HorizonRelation.ALIGNED,
        reasons=("ALIGNED",),
    )
    snapshot = object.__new__(decision_input.DecisionInputSnapshot)
    object.__setattr__(snapshot, "as_of", as_of)
    object.__setattr__(snapshot, "_market_state", market)
    return snapshot, market, st_participation, st_environment


def test_structure_normalization_preserves_raw_projection_and_existing_decision_quality_rule() -> None:
    raw = _limited_structure()

    normalized = normalize_decision_structure_projection(raw)

    assert normalized is not raw
    assert raw.for_timeframe("1h").data_quality is ContextDataQuality.DATA_LIMITED
    assert raw.for_timeframe("1h").events[0].ref.data_quality is ContextDataQuality.DATA_LIMITED
    assert normalized.for_timeframe("1h").data_quality is ContextDataQuality.VALID
    assert normalized.for_timeframe("1h").events[0].ref.data_quality is ContextDataQuality.VALID


def test_decision_input_builds_market_state_from_same_normalized_structure(monkeypatch) -> None:
    raw = _limited_structure()
    snapshot = object.__new__(decision_input.DecisionInputSnapshot)
    object.__setattr__(snapshot, "as_of", 10)
    object.__setattr__(snapshot, "structure", raw)
    object.__setattr__(snapshot, "stabil_support", None)
    object.__setattr__(snapshot, "volatility_environment", None)
    object.__setattr__(snapshot, "participation_behavior", None)
    object.__setattr__(snapshot, "_market_state", None)
    captured = []
    sentinel = object()

    from financial_dashboard.decision import market_state as market_state_module

    def fake_build(structure, *, as_of=None, stabil=None, volatility=None, participation=None):
        captured.append(structure)
        return sentinel

    monkeypatch.setattr(market_state_module, "build_market_state", fake_build)

    assert snapshot.market_state is sentinel
    assert len(captured) == 1
    assert captured[0].for_timeframe("1h").data_quality is ContextDataQuality.VALID
    assert raw.for_timeframe("1h").data_quality is ContextDataQuality.DATA_LIMITED


def test_production_prepared_facts_reuse_cached_market_state_without_rebuilding(monkeypatch) -> None:
    snapshot, market, st_participation, st_environment = _cached_snapshot()

    monkeypatch.setattr(
        engine_module,
        "build_horizon_structural_snapshot",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must use cached MarketState")),
    )
    monkeypatch.setattr(
        engine_module,
        "assess_participation",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must use cached MarketState")),
    )
    monkeypatch.setattr(
        engine_module,
        "assess_environment",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must use cached MarketState")),
    )

    structural_snapshot, structural, participation, environment = engine_module._market_state_facts(
        snapshot,
        DecisionHorizon.SHORT_TERM,
        participation_timeframe="1h",
        environment_timeframe="4h",
    )

    assert structural is market.short_term.structural
    assert participation is st_participation
    assert environment is st_environment
    assert structural_snapshot.long_term is market.long_term.structural
    assert structural_snapshot.short_term is market.short_term.structural
    assert structural_snapshot.relation is market.horizon_relation


def test_production_prepared_facts_reject_market_state_as_of_mismatch() -> None:
    snapshot, _, _, _ = _cached_snapshot(as_of=11)

    with pytest.raises(ValueError, match="share prepared horizon as_of"):
        engine_module._market_state_facts(
            snapshot,
            DecisionHorizon.SHORT_TERM,
            participation_timeframe="1h",
            environment_timeframe="4h",
        )
