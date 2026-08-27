from __future__ import annotations

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.context.projections import (
    LiquidityBehaviorObservation,
    LiquidityProjection,
    ReactionEvidenceProjection,
    ReactionObservation,
)
from financial_dashboard.context.structural_levels import (
    StructuralLevelKind,
    StructuralLevelObservation,
    StructuralLevelRole,
    StructuralLevelSide,
    StructuralLevelView,
)
from financial_dashboard.context.support_resistance_projection import (
    SupportResistanceProjection,
    SupportResistanceTimeframeProjection,
    SupportResistanceZoneProjection,
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
from financial_dashboard.decision.structural import StructuralDirection
from financial_dashboard.decision.target_path import (
    TargetPathNodeState,
    TargetPathRole,
    TargetPathSource,
    TargetPathStatus,
    build_target_path,
)
from financial_dashboard.targeting.models import (
    LiquidityScope,
    TargetCluster,
    TargetClusterKind,
    TargetClusterQuality,
    TargetEvidence,
    TargetEvidenceFamily,
    TargetEvidenceType,
    TargetRole,
    TargetSide,
    TargetingSnapshot,
)


NOW = 100


def _ref(
    domain: ContextDomain,
    timeframe: str,
    native_id: str,
    *,
    state: str = "ACTIVE",
    available_at: int = NOW,
    lineage: str | None = None,
) -> FactRef:
    family = CausalFamily.STRUCTURAL_LEVEL if domain is not ContextDomain.VOLUME else CausalFamily.PARTICIPATION
    source = SourceFamily.PRICE_GEOMETRY if domain is not ContextDomain.VOLUME else SourceFamily.VOLUME_SERIES
    return FactRef(
        domain=domain,
        fact_type="TEST",
        symbol="ASELS",
        timeframe=timeframe,
        native_id=native_id,
        native_state=state,
        origin_time=1,
        confirmed_at=2,
        available_at=available_at,
        lineage_id=lineage,
        causal_family=family,
        source_family=source,
        data_quality=ContextDataQuality.VALID,
    )


def _levels() -> StructuralLevelView:
    return StructuralLevelView(
        symbol="ASELS",
        current_price=100.0,
        levels=(
            StructuralLevelObservation(
                timeframe="1d",
                scope="EXTERNAL",
                kind=StructuralLevelKind.WEAK_HIGH,
                role=StructuralLevelRole.STRUCTURAL_OBJECTIVE,
                price=110.0,
                identity=11,
                side=StructuralLevelSide.ABOVE,
                data_quality=ContextDataQuality.VALID,
            ),
            StructuralLevelObservation(
                timeframe="1d",
                scope="EXTERNAL",
                kind=StructuralLevelKind.WEAK_LOW,
                role=StructuralLevelRole.STRUCTURAL_OBJECTIVE,
                price=90.0,
                identity=12,
                side=StructuralLevelSide.BELOW,
                data_quality=ContextDataQuality.VALID,
            ),
            StructuralLevelObservation(
                timeframe="1d",
                scope="EXTERNAL",
                kind=StructuralLevelKind.PROTECTED_LOW,
                role=StructuralLevelRole.THESIS_BOUNDARY,
                price=92.0,
                identity=13,
                side=StructuralLevelSide.BELOW,
                data_quality=ContextDataQuality.VALID,
            ),
            StructuralLevelObservation(
                timeframe="1d",
                scope="EXTERNAL",
                kind=StructuralLevelKind.PROTECTED_HIGH,
                role=StructuralLevelRole.THESIS_BOUNDARY,
                price=108.0,
                identity=14,
                side=StructuralLevelSide.ABOVE,
                data_quality=ContextDataQuality.VALID,
            ),
        ),
    )


def _liquidity_evidence(
    *,
    identity: str,
    level: float,
    timeframe: str = "1h",
    available_at: int = NOW,
) -> TargetEvidence:
    return TargetEvidence(
        uid=f"TE:{identity}",
        symbol="ASELS",
        timeframe=timeframe,
        evidence_type=TargetEvidenceType.LIQUIDITY,
        family=TargetEvidenceFamily.STRUCTURAL,
        roles=(TargetRole.MAGNET,),
        low=level,
        high=level,
        anchor_price=level,
        origin_index=1,
        origin_time=1,
        confirmed_at=2,
        available_at=available_at,
        source_state="ACTIVE",
        target_eligible=True,
        native_origin_id=f"LIQ:{timeframe}:{identity}",
        origin_event_id=f"LIQ:{timeframe}:{identity}",
        source_identity=identity,
        liquidity_scope=LiquidityScope.EXTERNAL,
    )


def _targeting(*evidence: TargetEvidence) -> TargetingSnapshot:
    clusters = []
    for item in evidence:
        side = TargetSide.ABOVE if item.low > 100.0 else TargetSide.BELOW
        clusters.append(
            TargetCluster(
                identity=f"CL:{item.source_identity}",
                side=side,
                kind=TargetClusterKind.LIQUIDITY_TARGET,
                envelope_low=item.low,
                envelope_high=item.high,
                core_low=item.low,
                core_high=item.high,
                liquidity_anchor=item.anchor_price,
                distance_price=abs(item.low - 100.0),
                distance_percent=abs(item.low - 100.0),
                distance_atr=abs(item.low - 100.0) / 2.0,
                evidence=(item,),
                raw_source_count=1,
                independent_origin_count=1,
                independent_family_count=1,
                timeframes_present=(item.timeframe,),
                roles_present=(TargetRole.MAGNET,),
                quality=TargetClusterQuality.SINGLE,
            )
        )
    upside = min((row for row in clusters if row.side is TargetSide.ABOVE), key=lambda row: row.distance_price, default=None)
    downside = min((row for row in clusters if row.side is TargetSide.BELOW), key=lambda row: row.distance_price, default=None)
    return TargetingSnapshot(
        symbol="ASELS",
        as_of=NOW,
        current_price=100.0,
        reference_timeframe="1h",
        reference_atr=2.0,
        clusters=tuple(clusters),
        nearest_upside_target=upside,
        nearest_downside_target=downside,
        highest_confluence_upside=upside,
        highest_confluence_downside=downside,
    )


def _liquidity(identity: str, level: float, removal: str) -> LiquidityProjection:
    timeframe = "1h"
    ref = _ref(ContextDomain.LIQUIDITY, timeframe, f"LIQ:{timeframe}:{identity}", lineage=f"LIQ:{timeframe}:{identity}")
    behavior_ref = _ref(ContextDomain.LIQUIDITY, timeframe, f"LIQ-B:{identity}")
    observation = type("LiquidityObservationLike", (), {})
    # Use the real projection's observation tuple only for FactRef lookup. The path
    # builder does not reinterpret the pool from this object.
    from financial_dashboard.context.projections import LiquidityObservation

    return LiquidityProjection(
        symbol="ASELS",
        timeframes=(timeframe,),
        observations=(
            LiquidityObservation(
                ref=ref,
                low=level,
                high=level,
                anchor_price=level,
                liquidity_scope="EXTERNAL",
                roles=("MAGNET",),
                target_eligible=True,
            ),
        ),
        behavior_observations=(
            LiquidityBehaviorObservation(
                ref=behavior_ref,
                pool_identity=identity,
                side="BSL" if level > 100.0 else "SSL",
                level=level,
                maturity="MATURE",
                relation="AT_POOL",
                removal=removal,
                age_bars=10,
                bars_since_touch=0,
                touch_count=2,
                distance_atr=abs(level - 100.0) / 2.0,
                distance_delta_atr=-0.2,
            ),
        ),
    )


def _empty_zones() -> ZoneIntelligenceSnapshot:
    return ZoneIntelligenceSnapshot(
        symbol="ASELS",
        as_of=NOW,
        current_price=100.0,
        zones=(),
        nearest_qualified_support=None,
        nearest_qualified_resistance=None,
        strongest_relevant_support=None,
        strongest_relevant_resistance=None,
        htf_primary_support=None,
        htf_primary_resistance=None,
    )


def _qualified_zone(*, objective_ref: FactRef) -> ZoneIntelligenceSnapshot:
    zone = QualifiedZone(
        zone_id="SR-Z1",
        side=QualifiedZoneSide.RESISTANCE,
        anchor_kind=ZoneAnchorKind.SUPPORT_RESISTANCE,
        anchor_timeframe="1h",
        low=105.0,
        high=105.4,
        center=105.2,
        native_lifecycle="ACTIVE",
        intrinsic_sr_quality=70.0,
        intrinsic_sr_touches=3,
        boundary_stability=75.0,
        structural_refs=(),
        stabil_refs=(),
        reaction_refs=(),
        confirmation_refs=(),
        objective_refs=(objective_ref,),
        anchor_refs=("SR-Z1",),
        freshness=ZoneFreshness.CURRENT,
        relevance=ZoneRelevance.RELEVANT,
        distance_atr=2.5,
        interaction=ZoneInteractionState.UNTOUCHED,
        qualification=ZoneQualification.HIGH,
        qualification_basis=("CURRENT_NATIVE_ANCHOR",),
        data_quality=ContextDataQuality.VALID,
        reference_atr=2.0,
    )
    return ZoneIntelligenceSnapshot(
        symbol="ASELS",
        as_of=NOW,
        current_price=100.0,
        zones=(zone,),
        nearest_qualified_support=None,
        nearest_qualified_resistance=zone,
        strongest_relevant_support=None,
        strongest_relevant_resistance=zone,
        htf_primary_support=None,
        htf_primary_resistance=zone,
    )


def _sr(lifecycle: str, *, low: float = 104.0, high: float = 105.0) -> SupportResistanceProjection:
    ref = _ref(ContextDomain.SUPPORT_RESISTANCE, "1h", "SR:RANGE")
    zone = SupportResistanceZoneProjection(
        zone_id="SR-Z2",
        side="RESISTANCE",
        low=low,
        high=high,
        center=(low + high) / 2.0,
        lifecycle=lifecycle,
        quality=80.0,
        touches=3,
        boundary_stability=78.0,
        reference_atr=2.0,
        created_at=1,
        updated_at=NOW,
    )
    row = SupportResistanceTimeframeProjection(
        timeframe="1h",
        ref=ref,
        state="RANGE_ACTIVE",
        range_identity=1,
        upper_center=high,
        upper_top=high,
        upper_bottom=low,
        lower_center=None,
        lower_top=None,
        lower_bottom=None,
        mid_price=None,
        quality=80.0,
        boundary_stability=78.0,
        identity_score=0.9,
        upper_touches=3,
        lower_touches=2,
        upper_close_violations=0,
        lower_close_violations=0,
        break_direction=0,
        break_candidate_index=None,
        break_confirmed_index=None,
        break_boundary=None,
        break_buffer=None,
        price_location="INSIDE_RANGE",
        nearest_support_low=None,
        nearest_support_high=None,
        nearest_resistance_low=low,
        nearest_resistance_high=high,
        role_reversal_support_low=None,
        role_reversal_support_high=None,
        role_reversal_resistance_low=None,
        role_reversal_resistance_high=None,
        reference_atr=2.0,
        zones=(zone,),
    )
    return SupportResistanceProjection(
        symbol="ASELS",
        timeframes=("1h",),
        timeframe_facts=(row,),
    )


def _reaction_with_engulfing_confirmation() -> ReactionEvidenceProjection:
    ref = _ref(ContextDomain.ENGULFING, "2h", "ENG:1")
    confirmation = ReactionObservation(
        ref=ref,
        evidence_type="ENGULFING",
        low=106.0,
        high=107.0,
        anchor_price=106.5,
        roles=("SUPPLY",),
        semantic_role="CONFIRMATION",
    )
    return ReactionEvidenceProjection(
        symbol="ASELS",
        timeframes=("2h",),
        reaction_zones=(),
        confirmations=(confirmation,),
    )


def test_long_path_is_nearest_first_and_protected_low_is_boundary_not_target() -> None:
    liq = _liquidity_evidence(identity="L1", level=105.0)
    path = build_target_path(
        symbol="ASELS",
        as_of=NOW,
        direction=StructuralDirection.LONG,
        current_price=100.0,
        structural_levels=_levels(),
        targeting=_targeting(liq),
        reference_atr=2.0,
    )

    assert path.status is TargetPathStatus.READY
    assert [node.anchor_price for node in path.nodes] == [105.0, 110.0]
    assert path.nodes[0].state is TargetPathNodeState.ACTIVE
    assert path.nodes[1].state is TargetPathNodeState.LOCKED
    assert all(boundary.kind is StructuralLevelKind.PROTECTED_LOW for boundary in path.thesis_boundaries)
    assert all(node.anchor_price != 92.0 for node in path.nodes)


def test_short_path_is_reciprocal_and_uses_weak_low_objective() -> None:
    liq = _liquidity_evidence(identity="S1", level=95.0)
    path = build_target_path(
        symbol="ASELS",
        as_of=NOW,
        direction=StructuralDirection.SHORT,
        current_price=100.0,
        structural_levels=_levels(),
        targeting=_targeting(liq),
        reference_atr=2.0,
    )

    assert [node.anchor_price for node in path.nodes] == [95.0, 90.0]
    assert path.nodes[0].state is TargetPathNodeState.ACTIVE
    assert all(boundary.kind is StructuralLevelKind.PROTECTED_HIGH for boundary in path.thesis_boundaries)


def test_engulfing_confirmation_never_becomes_target_path_node() -> None:
    path = build_target_path(
        symbol="ASELS",
        as_of=NOW,
        direction=StructuralDirection.LONG,
        current_price=100.0,
        structural_levels=StructuralLevelView("ASELS", 100.0, ()),
        reaction=_reaction_with_engulfing_confirmation(),
    )

    assert path.status is TargetPathStatus.NO_OBSERVED_PATH
    assert path.nodes == ()


def test_same_liquidity_seen_in_cluster_and_qualified_zone_is_deduplicated() -> None:
    evidence = _liquidity_evidence(identity="L1", level=105.0)
    liquidity = _liquidity("L1", 105.0, "UNTOUCHED")
    objective_ref = liquidity.observations[0].ref
    path = build_target_path(
        symbol="ASELS",
        as_of=NOW,
        direction=StructuralDirection.LONG,
        current_price=100.0,
        structural_levels=StructuralLevelView("ASELS", 100.0, ()),
        targeting=_targeting(evidence),
        liquidity=liquidity,
        qualified_zones=_qualified_zone(objective_ref=objective_ref),
        reference_atr=2.0,
    )

    assert len(path.nodes) == 1
    node = path.nodes[0]
    assert TargetPathRole.OBJECTIVE in node.roles
    assert TargetPathRole.BARRIER in node.roles
    assert TargetPathSource.LIQUIDITY in node.sources
    assert TargetPathSource.QUALIFIED_ZONE in node.sources


def test_sweep_reclaim_defends_t1_and_does_not_unlock_t2() -> None:
    evidence = _liquidity_evidence(identity="L1", level=105.0)
    path = build_target_path(
        symbol="ASELS",
        as_of=NOW,
        direction=StructuralDirection.LONG,
        current_price=100.0,
        structural_levels=_levels(),
        targeting=_targeting(evidence),
        liquidity=_liquidity("L1", 105.0, "SWEEP_RECLAIMED"),
        reference_atr=2.0,
    )

    assert path.nodes[0].state is TargetPathNodeState.DEFENDED
    assert path.nodes[1].state is TargetPathNodeState.LOCKED
    assert path.active_node == path.nodes[0]


def test_accepted_beyond_clears_t1_and_unlocks_next_node() -> None:
    evidence = _liquidity_evidence(identity="L1", level=105.0)
    path = build_target_path(
        symbol="ASELS",
        as_of=NOW,
        direction=StructuralDirection.LONG,
        current_price=100.0,
        structural_levels=_levels(),
        targeting=_targeting(evidence),
        liquidity=_liquidity("L1", 105.0, "ACCEPTED_BEYOND"),
        reference_atr=2.0,
    )

    assert path.nodes[0].state is TargetPathNodeState.CLEARED
    assert path.nodes[1].state is TargetPathNodeState.ACTIVE


def test_failed_sr_break_is_defended_and_later_objective_remains_locked() -> None:
    path = build_target_path(
        symbol="ASELS",
        as_of=NOW,
        direction=StructuralDirection.LONG,
        current_price=100.0,
        structural_levels=_levels(),
        support_resistance=_sr("BREAK_FAILED"),
        reference_atr=2.0,
    )

    assert path.nodes[0].state is TargetPathNodeState.DEFENDED
    assert TargetPathRole.BARRIER in path.nodes[0].roles
    assert path.nodes[1].state is TargetPathNodeState.LOCKED


def test_broken_sr_is_cleared_and_next_objective_activates() -> None:
    path = build_target_path(
        symbol="ASELS",
        as_of=NOW,
        direction=StructuralDirection.LONG,
        current_price=100.0,
        structural_levels=_levels(),
        support_resistance=_sr("BROKEN"),
        reference_atr=2.0,
    )

    assert path.nodes[0].state is TargetPathNodeState.CLEARED
    assert path.nodes[1].state is TargetPathNodeState.ACTIVE


def test_future_unavailable_liquidity_is_not_promoted_into_path() -> None:
    future = _liquidity_evidence(identity="FUT", level=105.0, available_at=NOW + 1)
    path = build_target_path(
        symbol="ASELS",
        as_of=NOW,
        direction=StructuralDirection.LONG,
        current_price=100.0,
        structural_levels=StructuralLevelView("ASELS", 100.0, ()),
        targeting=_targeting(future),
    )

    assert path.status is TargetPathStatus.NO_OBSERVED_PATH
    assert path.nodes == ()


def test_no_observed_path_is_explicitly_not_clear_path() -> None:
    path = build_target_path(
        symbol="ASELS",
        as_of=NOW,
        direction=StructuralDirection.LONG,
        current_price=100.0,
        structural_levels=StructuralLevelView("ASELS", 100.0, ()),
    )

    assert path.status is TargetPathStatus.NO_OBSERVED_PATH
    assert "NO_OBSERVED_PATH_IS_NOT_CLEAR_PATH" in path.reasons


def test_target_path_is_deterministic_for_same_inputs() -> None:
    evidence = _liquidity_evidence(identity="L1", level=105.0)
    kwargs = dict(
        symbol="ASELS",
        as_of=NOW,
        direction=StructuralDirection.LONG,
        current_price=100.0,
        structural_levels=_levels(),
        targeting=_targeting(evidence),
        liquidity=_liquidity("L1", 105.0, "UNTOUCHED"),
        reference_atr=2.0,
    )
    assert build_target_path(**kwargs) == build_target_path(**kwargs)
