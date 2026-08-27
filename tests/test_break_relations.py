from __future__ import annotations

from financial_dashboard.context.break_relations import (
    BreakEvidence,
    BreakRelationKind,
    classify_break_pair,
)
from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)


def _ref(
    domain: ContextDomain,
    *,
    timeframe: str = "1h",
    lineage_id: str | None = None,
    native_id: str | None = None,
) -> FactRef:
    return FactRef(
        domain=domain,
        fact_type="BREAK",
        symbol="ASELS",
        timeframe=timeframe,
        native_id=native_id or f"{domain.value}:{timeframe}",
        native_state="CONFIRMED",
        origin_time=1,
        confirmed_at=2,
        available_at=2,
        lineage_id=lineage_id,
        causal_family=CausalFamily.STRUCTURAL_LEVEL,
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=ContextDataQuality.VALID,
    )


def _evidence(
    domain: ContextDomain,
    *,
    timeframe: str = "1h",
    direction: int = 1,
    level: float | None = 100.0,
    tolerance: float | None = None,
    lineage_id: str | None = None,
) -> BreakEvidence:
    return BreakEvidence(
        domain=domain,
        timeframe=timeframe,
        direction=direction,
        level=level,
        tolerance=tolerance,
        state="CONFIRMED",
        ref=_ref(domain, timeframe=timeframe, lineage_id=lineage_id),
    )


def test_explicit_lineage_is_the_only_direct_same_event_route() -> None:
    left = _evidence(ContextDomain.MARKET_STRUCTURE, lineage_id="BREAK:42")
    right = _evidence(ContextDomain.PATTERN, tolerance=0.5, lineage_id="BREAK:42")

    relation = classify_break_pair(left, right)

    assert relation.relation is BreakRelationKind.SAME_EVENT
    assert relation.independently_countable is False


def test_same_timeframe_spatial_overlap_without_lineage_stays_potentially_shared() -> None:
    structure = _evidence(ContextDomain.MARKET_STRUCTURE, level=100.0)
    pattern = _evidence(ContextDomain.PATTERN, level=100.2, tolerance=0.5)

    relation = classify_break_pair(structure, pattern)

    assert relation.relation is BreakRelationKind.POTENTIALLY_SHARED
    assert relation.independently_countable is False


def test_cross_timeframe_overlap_is_nested_not_an_independent_vote() -> None:
    structure = _evidence(ContextDomain.MARKET_STRUCTURE, timeframe="4h", level=100.0)
    sr = _evidence(
        ContextDomain.SUPPORT_RESISTANCE,
        timeframe="1h",
        level=100.1,
        tolerance=0.4,
    )

    relation = classify_break_pair(structure, sr)

    assert relation.relation is BreakRelationKind.NESTED
    assert relation.independently_countable is False


def test_separated_break_levels_can_be_independent_when_native_tolerance_exists() -> None:
    structure = _evidence(ContextDomain.MARKET_STRUCTURE, level=100.0)
    sr = _evidence(ContextDomain.SUPPORT_RESISTANCE, level=103.0, tolerance=0.5)

    relation = classify_break_pair(structure, sr)

    assert relation.relation is BreakRelationKind.INDEPENDENT
    assert relation.independently_countable is True


def test_missing_native_tolerance_fails_closed_to_unresolved() -> None:
    structure = _evidence(ContextDomain.MARKET_STRUCTURE, level=100.0)
    pattern = _evidence(ContextDomain.PATTERN, level=100.0, tolerance=None)

    relation = classify_break_pair(structure, pattern)

    assert relation.relation is BreakRelationKind.UNRESOLVED
    assert relation.independently_countable is False
