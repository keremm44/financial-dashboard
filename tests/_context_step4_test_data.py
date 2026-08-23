from __future__ import annotations

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
from financial_dashboard.context.zone_interaction import ZoneInteractionState
from financial_dashboard.context.zones import (
    QualifiedZone,
    QualifiedZoneSide,
    ZoneAnchorKind,
    ZoneFreshness,
    ZoneIntelligenceSnapshot,
    ZoneQualification,
    ZoneRelevance,
)


def ref(
    native_id: str,
    *,
    domain: ContextDomain = ContextDomain.MARKET_STRUCTURE,
    timeframe: str = "4h",
    available_at: int = 10,
    confirmed_at: int | None = 10,
    fact_type: str = "FACT",
) -> FactRef:
    return FactRef(
        domain=domain,
        fact_type=fact_type,
        symbol="ASELS",
        timeframe=timeframe,
        native_id=native_id,
        native_state="CURRENT",
        origin_time=1,
        confirmed_at=confirmed_at,
        available_at=available_at,
        lineage_id=None,
        causal_family=(
            CausalFamily.STRUCTURAL_LEVEL
            if domain in {ContextDomain.MARKET_STRUCTURE, ContextDomain.LIQUIDITY}
            else CausalFamily.IMPULSE
        ),
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=ContextDataQuality.VALID,
    )


def scope(
    *,
    state: str,
    direction: int,
    name: str = "EXTERNAL",
) -> StructuralScopeProjection:
    return StructuralScopeProjection(
        scope=name,
        state=state,
        direction=direction,
        protected_high=None,
        protected_low=None,
        weak_high=None,
        weak_low=None,
        strong_high_identity=0,
        strong_low_identity=0,
        protected_high_identity=0,
        protected_low_identity=0,
        weak_high_identity=0,
        weak_low_identity=0,
    )


def event(
    native_id: str,
    *,
    timeframe: str = "4h",
    event_type: str = "BOS",
    direction: int = -1,
    validity: str = "VALID",
    relevance: str = "CURRENT",
    outcome: str = "OBSERVED",
) -> StructuralEventProjection:
    return StructuralEventProjection(
        ref=ref(native_id, timeframe=timeframe, fact_type=event_type),
        scope="EXTERNAL",
        event_type=event_type,
        direction=direction,
        broken_level=100.0,
        origin_price=101.0,
        confirmation_status="CONFIRMED",
        validity=validity,
        relevance=relevance,
        outcome=outcome,
        bos_maturity="MATURE",
    )


def structural_projection(
    *,
    anchor_state: str = "BEARISH",
    anchor_direction: int = -1,
    anchor_events: tuple[StructuralEventProjection, ...] = (),
    ltf_direction: int | None = None,
) -> StructuralFactsProjection:
    rows = [
        StructuralTimeframeProjection(
            timeframe="4h",
            as_of=10,
            data_quality=ContextDataQuality.VALID,
            external=scope(state=anchor_state, direction=anchor_direction),
            internal=None,
            events=anchor_events,
        )
    ]
    timeframes = ["4h"]
    if ltf_direction is not None:
        rows.append(
            StructuralTimeframeProjection(
                timeframe="1h",
                as_of=10,
                data_quality=ContextDataQuality.VALID,
                external=scope(
                    state="BULLISH" if ltf_direction > 0 else "BEARISH",
                    direction=ltf_direction,
                ),
                internal=None,
                events=(),
            )
        )
        timeframes.append("1h")
    return StructuralFactsProjection(
        symbol="ASELS",
        timeframes=tuple(timeframes),
        timeframe_facts=tuple(rows),
    )


def reaction_zone(
    *,
    side: QualifiedZoneSide,
    interaction: ZoneInteractionState,
    distance_atr: float = 0.0,
) -> QualifiedZone:
    reaction_ref = ref(
        f"OB-{side.value}",
        domain=ContextDomain.ORDER_BLOCK,
        timeframe="1h",
        fact_type="ORDER_BLOCK",
    )
    low, high = (99.0, 101.0) if side is QualifiedZoneSide.SUPPORT else (109.0, 111.0)
    return QualifiedZone(
        zone_id=f"QZ-{side.value}",
        side=side,
        anchor_kind=ZoneAnchorKind.SUPPORT_RESISTANCE,
        anchor_timeframe="4h",
        low=low,
        high=high,
        center=(low + high) / 2.0,
        native_lifecycle="ACTIVE",
        intrinsic_sr_quality=70.0,
        intrinsic_sr_touches=2,
        boundary_stability=80.0,
        structural_refs=(),
        stabil_refs=(),
        reaction_refs=(reaction_ref,),
        confirmation_refs=(),
        objective_refs=(),
        anchor_refs=(f"SR-{side.value}",),
        freshness=ZoneFreshness.CURRENT,
        relevance=ZoneRelevance.AT_PRICE if distance_atr == 0 else ZoneRelevance.NEAR,
        distance_atr=distance_atr,
        interaction=interaction,
        qualification=ZoneQualification.HIGH,
        qualification_basis=("REACTION_CONTRIBUTOR",),
        data_quality=ContextDataQuality.VALID,
        reference_atr=2.0,
    )


def zone_snapshot(*zones: QualifiedZone, price: float = 100.0) -> ZoneIntelligenceSnapshot:
    return ZoneIntelligenceSnapshot(
        symbol="ASELS",
        as_of=10,
        current_price=price,
        zones=tuple(zones),
        nearest_qualified_support=next((z for z in zones if z.side is QualifiedZoneSide.SUPPORT), None),
        nearest_qualified_resistance=next((z for z in zones if z.side is QualifiedZoneSide.RESISTANCE), None),
        strongest_relevant_support=next((z for z in zones if z.side is QualifiedZoneSide.SUPPORT), None),
        strongest_relevant_resistance=next((z for z in zones if z.side is QualifiedZoneSide.RESISTANCE), None),
        htf_primary_support=next((z for z in zones if z.side is QualifiedZoneSide.SUPPORT), None),
        htf_primary_resistance=next((z for z in zones if z.side is QualifiedZoneSide.RESISTANCE), None),
    )
