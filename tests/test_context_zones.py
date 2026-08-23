from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from financial_dashboard.context.envelope import (
    ContextDataQuality,
    ContextDomain,
    FactRef,
)
from financial_dashboard.context.lineage import families_for
from financial_dashboard.context.projections import (
    LiquidityObservation,
    LiquidityProjection,
    ReactionEvidenceProjection,
    ReactionObservation,
    StructuralFactsProjection,
    StructuralScopeProjection,
    StructuralTimeframeProjection,
)
from financial_dashboard.context.zones import (
    QualifiedZoneSide,
    ZoneQualification,
    ZoneRelevance,
    build_zone_intelligence,
)


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _ref(
    domain: ContextDomain,
    native_id: str,
    *,
    timeframe: str,
    fact_type: str,
    lineage_id: str | None = None,
) -> FactRef:
    causal, source = families_for(domain, fact_type=fact_type)
    return FactRef(
        domain=domain,
        fact_type=fact_type,
        symbol="ASELS",
        timeframe=timeframe,
        native_id=native_id,
        native_state="ACTIVE",
        origin_time=NOW,
        confirmed_at=NOW,
        available_at=NOW,
        lineage_id=lineage_id,
        causal_family=causal,
        source_family=source,
        data_quality=ContextDataQuality.VALID,
    )


def _scope(
    *,
    scope: str = "external",
    protected_low: float | None = None,
    protected_low_identity: int = 0,
    protected_high: float | None = None,
    protected_high_identity: int = 0,
) -> StructuralScopeProjection:
    return StructuralScopeProjection(
        scope=scope,
        state="BULLISH" if protected_low is not None else "BEARISH",
        direction=1 if protected_low is not None else -1,
        protected_high=protected_high,
        protected_low=protected_low,
        weak_high=None,
        weak_low=None,
        strong_high_identity=0,
        strong_low_identity=0,
        protected_high_identity=protected_high_identity,
        protected_low_identity=protected_low_identity,
        weak_high_identity=0,
        weak_low_identity=0,
    )


def _structural() -> StructuralFactsProjection:
    return StructuralFactsProjection(
        symbol="ASELS",
        timeframes=("1h", "4h"),
        timeframe_facts=(
            StructuralTimeframeProjection(
                timeframe="1h",
                as_of=NOW,
                data_quality=ContextDataQuality.VALID,
                external=_scope(),
                internal=None,
                events=(),
            ),
            StructuralTimeframeProjection(
                timeframe="4h",
                as_of=NOW,
                data_quality=ContextDataQuality.VALID,
                external=_scope(protected_low=99.45, protected_low_identity=44),
                internal=None,
                events=(),
            ),
        ),
    )


def _zone(uid: str, timeframe: str, low: float, high: float, *, lifecycle: str = "ACTIVE"):
    return SimpleNamespace(
        zone_uid=f"ASELS:{timeframe}:{uid}",
        side="SUPPORT",
        low=low,
        high=high,
        lifecycle=lifecycle,
        quality=62.0,
        touches=2,
        boundary_stability=70.0,
        reference_atr=1.0,
        created_at=NOW,
        last_updated_at=NOW,
    )


class _StructureLocation:
    def __init__(self, zones_by_tf):
        self.timeframes = tuple(zones_by_tf)
        self._rows = {
            timeframe: SimpleNamespace(
                support_resistance=SimpleNamespace(
                    available_at=NOW,
                    zones=tuple(zones),
                )
            )
            for timeframe, zones in zones_by_tf.items()
        }

    def replay_for(self, timeframe: str):
        return self._rows[timeframe]


def _reaction() -> ReactionEvidenceProjection:
    ob = ReactionObservation(
        ref=_ref(
            ContextDomain.ORDER_BLOCK,
            "OB:4h:10:1",
            timeframe="4h",
            fact_type="ORDER_BLOCK",
            lineage_id="EVT:IMPULSE:4h:10",
        ),
        evidence_type="ORDER_BLOCK",
        low=99.35,
        high=99.60,
        anchor_price=99.48,
        roles=("DEMAND", "REACTION"),
        semantic_role="REACTION_ZONE",
    )
    engulf = ReactionObservation(
        ref=_ref(
            ContextDomain.ENGULFING,
            "ENG:4h:10:1",
            timeframe="4h",
            fact_type="ENGULFING",
            lineage_id="EVT:IMPULSE:4h:10",
        ),
        evidence_type="ENGULFING",
        low=99.40,
        high=99.55,
        anchor_price=99.48,
        roles=("DEMAND", "REACTION"),
        semantic_role="CONFIRMATION",
    )
    return ReactionEvidenceProjection(
        symbol="ASELS",
        timeframes=("4h",),
        reaction_zones=(ob,),
        confirmations=(engulf,),
    )


def _liquidity() -> LiquidityProjection:
    item = LiquidityObservation(
        ref=_ref(
            ContextDomain.LIQUIDITY,
            "LIQ:4h:20",
            timeframe="4h",
            fact_type="POOL",
            lineage_id="EVT:STRUCTURAL:4h:20",
        ),
        low=99.42,
        high=99.42,
        anchor_price=99.42,
        liquidity_scope="EXTERNAL",
        roles=("MAGNET",),
        target_eligible=True,
    )
    return LiquidityProjection(symbol="ASELS", timeframes=("4h",), observations=(item,))


def test_nearest_and_strongest_relevant_are_distinct_queries() -> None:
    structure_location = _StructureLocation(
        {
            "1h": (_zone("near", "1h", 100.30, 100.50),),
            "4h": (_zone("strong", "4h", 99.30, 99.60),),
        }
    )
    snapshot = build_zone_intelligence(
        symbol="ASELS",
        as_of=NOW,
        current_price=100.80,
        structure_location=structure_location,
        structural=_structural(),
        reaction=_reaction(),
        liquidity=_liquidity(),
    )

    assert snapshot.nearest_qualified_support is not None
    assert snapshot.strongest_relevant_support is not None
    assert snapshot.nearest_qualified_support.anchor_timeframe == "1h"
    assert snapshot.strongest_relevant_support.anchor_timeframe == "4h"
    assert snapshot.strongest_relevant_support.qualification is ZoneQualification.VERY_HIGH


def test_engulfing_is_confirmation_and_liquidity_is_overlay_not_reaction_vote() -> None:
    snapshot = build_zone_intelligence(
        symbol="ASELS",
        as_of=NOW,
        current_price=100.0,
        structure_location=_StructureLocation({"4h": (_zone("z", "4h", 99.30, 99.60),)}),
        structural=_structural(),
        reaction=_reaction(),
        liquidity=_liquidity(),
    )
    zone = next(item for item in snapshot.zones if item.anchor_timeframe == "4h" and item.low == 99.30)

    assert len(zone.reaction_refs) == 1
    assert zone.reaction_refs[0].domain is ContextDomain.ORDER_BLOCK
    assert len(zone.confirmation_refs) == 1
    assert zone.confirmation_refs[0].domain is ContextDomain.ENGULFING
    assert len(zone.objective_refs) == 1
    assert zone.objective_refs[0].domain is ContextDomain.LIQUIDITY
    assert "OBJECTIVE_OVERLAY_PRESENT" in zone.qualification_basis


def test_same_impulse_ob_and_engulfing_are_preserved_as_separate_refs() -> None:
    snapshot = build_zone_intelligence(
        symbol="ASELS",
        as_of=NOW,
        current_price=100.0,
        structure_location=_StructureLocation({"4h": (_zone("z", "4h", 99.30, 99.60),)}),
        structural=_structural(),
        reaction=_reaction(),
    )
    zone = next(item for item in snapshot.zones if item.anchor_timeframe == "4h" and item.low == 99.30)
    refs = (*zone.reaction_refs, *zone.confirmation_refs)

    assert len(refs) == 2
    assert {ref.native_id for ref in refs} == {"OB:4h:10:1", "ENG:4h:10:1"}
    assert {ref.lineage_id for ref in refs} == {"EVT:IMPULSE:4h:10"}


def test_broken_native_support_is_historical_and_unqualified() -> None:
    snapshot = build_zone_intelligence(
        symbol="ASELS",
        as_of=NOW,
        current_price=98.0,
        structure_location=_StructureLocation({"4h": (_zone("broken", "4h", 99.30, 99.60, lifecycle="BROKEN"),)}),
        structural=StructuralFactsProjection(
            symbol="ASELS",
            timeframes=("4h",),
            timeframe_facts=(
                StructuralTimeframeProjection(
                    timeframe="4h",
                    as_of=NOW,
                    data_quality=ContextDataQuality.VALID,
                    external=None,
                    internal=None,
                    events=(),
                ),
            ),
        ),
    )
    zone = snapshot.zones[0]

    assert zone.qualification is ZoneQualification.UNQUALIFIED
    assert zone.relevance is ZoneRelevance.HISTORICAL
    assert snapshot.nearest_qualified_support is None


def test_overlapping_lower_timeframe_zone_gets_htf_parent_without_vote_counting() -> None:
    snapshot = build_zone_intelligence(
        symbol="ASELS",
        as_of=NOW,
        current_price=100.0,
        structure_location=_StructureLocation(
            {
                "1h": (_zone("child", "1h", 99.40, 99.55),),
                "4h": (_zone("parent", "4h", 99.30, 99.60),),
            }
        ),
        structural=StructuralFactsProjection(
            symbol="ASELS",
            timeframes=("1h", "4h"),
            timeframe_facts=(
                StructuralTimeframeProjection("1h", NOW, ContextDataQuality.VALID, None, None, ()),
                StructuralTimeframeProjection("4h", NOW, ContextDataQuality.VALID, None, None, ()),
            ),
        ),
    )
    child = next(zone for zone in snapshot.zones if zone.anchor_timeframe == "1h")
    parent = next(zone for zone in snapshot.zones if zone.anchor_timeframe == "4h")

    assert child.htf_parent_zone_id == parent.zone_id
    assert child.zone_id in parent.child_zone_ids
    assert child.side is QualifiedZoneSide.SUPPORT
