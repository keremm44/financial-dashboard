from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.context.lineage import (
    build_lineage_groups,
    families_for,
    known_independent_origin_count,
    lineage_id_from_origin_event,
    unknown_lineage_refs,
)


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _fact(
    native_id: str,
    *,
    domain: ContextDomain = ContextDomain.ORDER_BLOCK,
    fact_type: str = "OB",
    lineage_id: str | None = "EVT:IMPULSE:1h:10",
    causal_family: CausalFamily = CausalFamily.IMPULSE,
    source_family: SourceFamily = SourceFamily.PRICE_GEOMETRY,
) -> FactRef:
    return FactRef(
        domain=domain,
        fact_type=fact_type,
        symbol="ASELS",
        timeframe="1h",
        native_id=native_id,
        native_state="ACTIVE",
        origin_time=NOW,
        confirmed_at=NOW,
        available_at=NOW,
        lineage_id=lineage_id,
        causal_family=causal_family,
        source_family=source_family,
        data_quality=ContextDataQuality.VALID,
    )


def test_origin_event_bridge_reads_existing_dedup_identity_only() -> None:
    item = SimpleNamespace(origin_event_id="EVT:IMPULSE:1h:native:10")
    missing = SimpleNamespace(origin_event_id="")

    assert lineage_id_from_origin_event(item) == "EVT:IMPULSE:1h:native:10"
    assert lineage_id_from_origin_event(missing) is None
    assert lineage_id_from_origin_event(SimpleNamespace()) is None


def test_shared_lineage_preserves_all_facts_in_one_group() -> None:
    ob = _fact("ob:10", domain=ContextDomain.ORDER_BLOCK, fact_type="OB")
    fvg = _fact("fvg:10", domain=ContextDomain.FVG, fact_type="FVG")
    engulf = _fact("eng:10", domain=ContextDomain.ENGULFING, fact_type="ENGULFING")

    groups = build_lineage_groups((fvg, engulf, ob))

    assert len(groups) == 1
    assert groups[0].lineage_id == "EVT:IMPULSE:1h:10"
    assert tuple(member.native_id for member in groups[0].members) == ("eng:10", "fvg:10", "ob:10")
    assert known_independent_origin_count((ob, fvg, engulf)) == 1


def test_unknown_lineage_is_not_assumed_independent() -> None:
    known = _fact("ob:known", lineage_id="EVT:KNOWN")
    unknown_a = _fact("ob:unknown:a", lineage_id=None)
    unknown_b = _fact("ob:unknown:b", lineage_id=None)

    groups = build_lineage_groups((known, unknown_b, unknown_a))
    unknown = unknown_lineage_refs((known, unknown_b, unknown_a))

    assert len(groups) == 1
    assert tuple(item.native_id for item in unknown) == ("ob:unknown:a", "ob:unknown:b")
    assert known_independent_origin_count((known, unknown_a, unknown_b)) == 1


def test_lineage_group_rejects_cross_causal_family_merge() -> None:
    shared = "EVT:SHARED"
    impulse = _fact("ob", lineage_id=shared)
    structural = _fact(
        "liq",
        domain=ContextDomain.LIQUIDITY,
        fact_type="POOL",
        lineage_id=shared,
        causal_family=CausalFamily.STRUCTURAL_LEVEL,
    )

    groups = build_lineage_groups((impulse, structural))
    assert len(groups) == 2
    assert {group.causal_family for group in groups} == {
        CausalFamily.IMPULSE,
        CausalFamily.STRUCTURAL_LEVEL,
    }


@pytest.mark.parametrize(
    ("domain", "fact_type", "expected"),
    [
        (
            ContextDomain.MARKET_STRUCTURE,
            "BOS",
            (CausalFamily.STRUCTURAL_LEVEL, SourceFamily.PRICE_GEOMETRY),
        ),
        (
            ContextDomain.LIQUIDITY,
            "POOL",
            (CausalFamily.STRUCTURAL_LEVEL, SourceFamily.PRICE_GEOMETRY),
        ),
        (
            ContextDomain.ORDER_BLOCK,
            "OB",
            (CausalFamily.IMPULSE, SourceFamily.PRICE_GEOMETRY),
        ),
        (
            ContextDomain.VOLUME,
            "PARTICIPATION",
            (CausalFamily.PARTICIPATION, SourceFamily.VOLUME_SERIES),
        ),
        (
            ContextDomain.PATTERN,
            "COMPRESSION",
            (CausalFamily.REGIME, SourceFamily.PRICE_GEOMETRY),
        ),
        (
            ContextDomain.VOLATILITY,
            "ATR_REGIME",
            (CausalFamily.REGIME, SourceFamily.PRICE_DERIVED_INDICATOR),
        ),
        (
            ContextDomain.HAM,
            "MOMENTUM",
            (CausalFamily.INDICATOR, SourceFamily.PRICE_DERIVED_INDICATOR),
        ),
        (
            ContextDomain.HAM,
            "FLOW_BALANCE",
            (CausalFamily.PARTICIPATION, SourceFamily.VOLUME_SERIES),
        ),
    ],
)
def test_domain_family_mapping_is_semantic_not_vote_based(
    domain: ContextDomain,
    fact_type: str,
    expected: tuple[CausalFamily, SourceFamily],
) -> None:
    assert families_for(domain, fact_type=fact_type) == expected
