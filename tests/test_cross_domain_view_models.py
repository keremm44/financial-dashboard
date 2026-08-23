from __future__ import annotations

from dataclasses import replace

from financial_dashboard.context.axes import (
    ConflictState,
    ContextAxes,
    ContextDirection,
    ContinuationContext,
    HamReadinessContext,
    MTFContext,
    ObjectiveContext,
    ParticipationContext,
    PatternReadiness,
    ReactionContext,
    ReversalContext,
    StructuralThesis,
    VolatilityContext,
)
from financial_dashboard.context.builder import CrossDomainBuildResult
from financial_dashboard.context.permissions import resolve_permission
from financial_dashboard.context.snapshot import build_context_snapshot
from financial_dashboard.ui.cross_domain_view_models import (
    cross_domain_context_frame,
    cross_domain_knowledge_frame,
    cross_domain_permission_frame,
    cross_domain_summary_values,
    cross_domain_zones_frame,
)
from _context_step4_test_data import ref, reaction_zone, zone_snapshot
from financial_dashboard.context.zone_interaction import ZoneInteractionState
from financial_dashboard.context.zones import QualifiedZoneSide


def _build() -> CrossDomainBuildResult:
    axes = ContextAxes(
        anchor_timeframe="4h",
        structural_thesis=StructuralThesis.DOWN,
        structural_direction=ContextDirection.DOWN,
        continuation=ContinuationContext.ABSENT,
        reaction=ReactionContext.ACTIVE,
        reaction_direction=ContextDirection.UP,
        reversal=ReversalContext.NOT_PRESENT,
        reversal_direction=ContextDirection.NONE,
        objective=ObjectiveContext.UPSIDE,
        participation=ParticipationContext.WEAK,
        volatility=VolatilityContext.BALANCED,
        pattern_readiness=PatternReadiness.PATTERN_PRESENT,
        mtf=MTFContext.COUNTER_REACTION,
        ham_readiness=HamReadinessContext.AVAILABLE,
        conflict=ConflictState.MATERIAL,
        reasons=(),
    )
    support = reaction_zone(side=QualifiedZoneSide.SUPPORT, interaction=ZoneInteractionState.DEFENDED)
    zones = replace(zone_snapshot(support, price=100.0), as_of=10)
    context = build_context_snapshot(
        symbol="ASELS",
        as_of=10,
        anchor_timeframe="4h",
        axes=axes,
        zones=zones,
        all_fact_refs=(ref("known", available_at=10), ref("future", available_at=11)),
    )
    return CrossDomainBuildResult(context=context, permission=resolve_permission(context))


def test_cross_domain_summary_preserves_reaction_vs_reversal() -> None:
    values = cross_domain_summary_values(_build())
    assert values["Structural thesis"] == "DOWN"
    assert values["Reaction"] == "ACTIVE / UP"
    assert values["Reversal"] == "NOT_PRESENT / NONE"
    assert values["Permission scope"] == "REACTION_ONLY"
    assert values["Permitted side"] == "LONG"


def test_cross_domain_frames_are_descriptive_not_action_outputs() -> None:
    result = _build()
    assert "Structural thesis" in set(cross_domain_context_frame(result)["Axis"])
    zones = cross_domain_zones_frame(result)
    assert "Nearest support" in set(zones["View"])
    permission = cross_domain_permission_frame(result)
    assert "BUY" not in " ".join(permission["Value"].astype(str))
    knowledge = cross_domain_knowledge_frame(result)
    future = knowledge.loc[knowledge["Boundary"] == "future facts excluded", "Value"].iloc[0]
    assert future == "1"
