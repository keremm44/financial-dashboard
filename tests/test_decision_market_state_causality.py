from __future__ import annotations

import pytest

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
    StructuralScopeProjection,
    StructuralTimeframeProjection,
)
from financial_dashboard.decision.market_state import build_market_state


def _scope(scope: str, *, seed: int) -> StructuralScopeProjection:
    return StructuralScopeProjection(
        scope=scope,
        state="STATE_BULLISH",
        direction=1,
        protected_high=120.0 + seed,
        protected_low=80.0 + seed,
        weak_high=125.0 + seed,
        weak_low=75.0 + seed,
        strong_high_identity=seed * 10 + 1,
        strong_low_identity=seed * 10 + 2,
        protected_high_identity=seed * 10 + 3,
        protected_low_identity=seed * 10 + 4,
        weak_high_identity=seed * 10 + 5,
        weak_low_identity=seed * 10 + 6,
    )


def _ref(timeframe: str, *, available_at: int, lineage_id: str | None) -> FactRef:
    return FactRef(
        domain=ContextDomain.MARKET_STRUCTURE,
        fact_type="STRUCTURE_EVENT",
        symbol="ASELS",
        timeframe=timeframe,
        native_id=f"MS:{timeframe}",
        native_state="BOS_UP",
        origin_time=9,
        confirmed_at=9,
        available_at=available_at,
        lineage_id=lineage_id,
        causal_family=CausalFamily.STRUCTURAL_LEVEL,
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=ContextDataQuality.VALID,
    )


def _event(timeframe: str, *, available_at: int, lineage_id: str | None) -> StructuralEventProjection:
    return StructuralEventProjection(
        ref=_ref(timeframe, available_at=available_at, lineage_id=lineage_id),
        scope="EXTERNAL",
        event_type="BOS",
        direction=1,
        broken_level=110.0,
        origin_price=100.0,
        confirmation_status="CONFIRMED",
        validity="VALID",
        relevance="CURRENT",
        outcome="ACTIVE",
        bos_maturity="CONFIRMED",
    )


def _structure(*, future_ref: bool = False, future_row: bool = False) -> StructuralFactsProjection:
    rows = []
    for seed, timeframe in enumerate(("1d", "4h", "2h", "1h", "30m"), start=1):
        events = ()
        if timeframe == "1d":
            events = (_event("1d", available_at=10, lineage_id="STRUCTURE:ROOT"),)
        elif timeframe == "1h":
            events = (_event("1h", available_at=11 if future_ref else 10, lineage_id=None),)
        rows.append(
            StructuralTimeframeProjection(
                timeframe=timeframe,
                as_of=11 if future_row and timeframe == "4h" else 10,
                data_quality=ContextDataQuality.VALID,
                external=_scope("EXTERNAL", seed=seed),
                internal=_scope("INTERNAL", seed=seed + 20),
                events=events,
            )
        )
    return StructuralFactsProjection(
        symbol="ASELS",
        timeframes=("1d", "4h", "2h", "1h", "30m"),
        timeframe_facts=tuple(rows),
    )


def test_market_state_exposes_only_real_deterministic_refs_and_known_lineage() -> None:
    state = build_market_state(_structure(), as_of=10)

    assert state.as_of == 10
    assert tuple(ref.deterministic_key for ref in state.source_refs) == tuple(
        sorted(ref.deterministic_key for ref in state.source_refs)
    )
    assert {ref.timeframe for ref in state.source_refs} == {"1d", "1h"}
    assert state.source_lineage == ("STRUCTURE:ROOT",)
    assert all(ref.is_available_at(state.as_of) for ref in state.source_refs)


def test_market_state_rejects_future_unavailable_traceable_ref() -> None:
    with pytest.raises(ValueError, match="future-unavailable"):
        build_market_state(_structure(future_ref=True), as_of=10)


def test_market_state_rejects_future_structural_state_even_without_fabricated_ref() -> None:
    with pytest.raises(ValueError, match="future Structure state:4h"):
        build_market_state(_structure(future_row=True), as_of=10)
