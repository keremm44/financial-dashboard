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
from financial_dashboard.targeting.clustering import TargetClusterConfig, deduplicate_origin_events
from financial_dashboard.targeting.models import (
    TargetEvidence,
    TargetEvidenceFamily,
    TargetEvidenceType,
    TargetRole,
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


def _target_evidence(uid: str, evidence_type: TargetEvidenceType) -> TargetEvidence:
    family_by_type = {
        TargetEvidenceType.FVG: TargetEvidenceFamily.IMBALANCE,
        TargetEvidenceType.ORDER_BLOCK: TargetEvidenceFamily.SUPPLY_DEMAND,
        TargetEvidenceType.ENGULFING: TargetEvidenceFamily.REACTION,
    }
    role_by_type = {
        TargetEvidenceType.FVG: (TargetRole.IMBALANCE,),
        TargetEvidenceType.ORDER_BLOCK: (TargetRole.SUPPLY,),
        TargetEvidenceType.ENGULFING: (TargetRole.REACTION,),
    }
    return TargetEvidence(
        uid=uid,
        symbol="ASELS",
        timeframe="1h",
        evidence_type=evidence_type,
        family=family_by_type[evidence_type],
        roles=role_by_type[evidence_type],
        low=100.0,
        high=100.1,
        anchor_price=100.05,
        origin_index=10,
        origin_time=NOW,
        confirmed_at=NOW,
        available_at=NOW,
        source_state="ACTIVE",
        target_eligible=True,
        native_origin_id=f"native:{uid}",
        origin_event_id=f"native-event:{uid}",
        source_identity=f"source:{uid}",
        formation_atr=1.0,
    )


def test_origin_event_bridge_reads_existing_dedup_identity_only() -> None:
    item = SimpleNamespace(origin_event_id="EVT:IMPULSE:1h:native:10")
    missing = SimpleNamespace(origin_event_id="")

    assert lineage_id_from_origin_event(item) == "EVT:IMPULSE:1h:native:10"
    assert lineage_id_from_origin_event(missing) is None
    assert lineage_id_from_origin_event(SimpleNamespace()) is None


def test_bridge_reuses_targeting_deduplicate_origin_events_without_reimplementing_it() -> None:
    evidence = (
        _target_evidence("fvg", TargetEvidenceType.FVG),
        _target_evidence("ob", TargetEvidenceType.ORDER_BLOCK),
        _target_evidence("eng", TargetEvidenceType.ENGULFING),
    )
    deduped = deduplicate_origin_events(
        evidence,
        reference_atr=1.0,
        config=TargetClusterConfig(origin_bar_tolerance=2, origin_price_tolerance_atr=0.25),
    )

    lineage_ids = {lineage_id_from_origin_event(item) for item in deduped}
    assert len(lineage_ids) == 1
    assert None not in lineage_ids
    assert len({item.uid for item in deduped}) == 3


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


def test_same_lineage_can_be_reported_separately_when_causal_family_differs() -> None:
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
