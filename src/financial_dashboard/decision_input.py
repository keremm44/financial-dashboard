from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from financial_dashboard.context.break_relations import (
    CrossDomainBreakRelations,
    build_cross_domain_break_relations,
)
from financial_dashboard.context.builder import CrossDomainBuildResult
from financial_dashboard.context.envelope import ContextDataQuality, FactRef
from financial_dashboard.context.fvg_engulfing_projection import FvgEngulfingLifecycleProjection
from financial_dashboard.context.lineage import LineageGroup
from financial_dashboard.context.liquidity_landscape_projection import LiquidityLandscapeProjection
from financial_dashboard.context.order_block_behavior_projection import OrderBlockBehaviorProjection
from financial_dashboard.context.participation_behavior_projection import ParticipationBehaviorProjection
from financial_dashboard.context.pattern_behavior_projection import PatternBehaviorProjection
from financial_dashboard.context.permissions import PermissionEnvelope
from financial_dashboard.context.projections import (
    HamProjection,
    LiquidityProjection,
    ParticipationProjection,
    PatternProjection,
    ReactionEvidenceProjection,
    StabilSupportProjection,
    StructuralFactsProjection,
    VolatilityProjection,
)
from financial_dashboard.context.snapshot import CrossDomainContextSnapshot, KnowledgeBoundary
from financial_dashboard.context.structural_levels import (
    StructuralLevelView,
    build_structural_level_view,
)
from financial_dashboard.context.support_resistance_projection import SupportResistanceProjection
from financial_dashboard.context.volatility_environment_projection import VolatilityEnvironmentProjection
from financial_dashboard.context.zones import ZoneIntelligenceSnapshot
from financial_dashboard.targeting.models import TargetingSnapshot
from financial_dashboard.targeting.semantic_models import SemanticTargetingSnapshot


@dataclass(frozen=True, slots=True)
class DecisionInputSnapshot:
    """Single-as-of immutable input contract for the future decision engine.

    This object contains no BUY/SELL authority. It only exposes target-bounded read
    models that were already known at ``as_of``. Context and Permission are tagged
    conceptually as derived summaries and must not be re-counted as fresh evidence.
    """

    symbol: str
    as_of: Any
    current_price: float
    timeframes: tuple[str, ...]
    trigger_timeframes: tuple[str, ...]

    structure: StructuralFactsProjection
    support_resistance: SupportResistanceProjection | None
    liquidity: LiquidityProjection | None
    liquidity_landscape: LiquidityLandscapeProjection | None
    reaction: ReactionEvidenceProjection | None
    order_block_behavior: OrderBlockBehaviorProjection | None
    fvg_engulfing_lifecycle: FvgEngulfingLifecycleProjection | None
    stabil_support: StabilSupportProjection | None
    participation: ParticipationProjection | None
    participation_behavior: ParticipationBehaviorProjection | None
    volatility: VolatilityProjection | None
    volatility_environment: VolatilityEnvironmentProjection | None
    pattern: PatternProjection | None
    pattern_behavior: PatternBehaviorProjection | None
    ham: HamProjection | None
    qualified_zones: ZoneIntelligenceSnapshot
    targeting: TargetingSnapshot | None
    semantic_targeting: SemanticTargetingSnapshot | None

    context: CrossDomainContextSnapshot
    permission: PermissionEnvelope
    source_refs: tuple[FactRef, ...]
    lineage_groups: tuple[LineageGroup, ...]
    knowledge_boundary: KnowledgeBoundary
    data_quality_by_timeframe: tuple[tuple[str, ContextDataQuality], ...]
    derived_summaries: tuple[str, ...] = ("CONTEXT", "PERMISSION")

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("decision input symbol must be non-empty")
        if self.as_of is None:
            raise ValueError("decision input as_of must be known")
        if self.context.symbol != self.symbol or self.context.as_of != self.as_of:
            raise ValueError("decision input context must share symbol/as_of")
        if self.qualified_zones.symbol != self.symbol or self.qualified_zones.as_of != self.as_of:
            raise ValueError("decision input qualified zones must share symbol/as_of")
        if self.targeting is not None and self.targeting.as_of != self.as_of:
            raise ValueError("decision input targeting must share as_of")
        if self.semantic_targeting is not None and self.semantic_targeting.as_of != self.as_of:
            raise ValueError("decision input semantic targeting must share as_of")
        if self.permission.is_actionable_signal:
            raise ValueError("permission envelope must remain non-actionable")
        if any(ref.symbol != self.symbol for ref in self.source_refs):
            raise ValueError("decision input refs must match symbol")
        if any(not ref.is_available_at(self.as_of) for ref in self.source_refs):
            raise ValueError("decision input cannot contain future-unavailable refs")
        if self.knowledge_boundary.as_of != self.as_of:
            raise ValueError("decision input knowledge boundary must share as_of")

    @property
    def structural_levels(self) -> StructuralLevelView:
        """Derived weak-objective/protected-boundary view; never independent evidence."""

        return build_structural_level_view(
            self.structure,
            current_price=self.current_price,
        )

    @property
    def break_relations(self) -> CrossDomainBreakRelations:
        """Derived break-overlap view used to prevent cross-domain vote stacking."""

        return build_cross_domain_break_relations(
            structure=self.structure,
            pattern=self.pattern_behavior,
            support_resistance=self.support_resistance,
        )

    @property
    def market_state(self):
        """Derived horizon-aware MTF state; never a fresh vote or action signal."""

        from financial_dashboard.decision.market_state import build_market_state

        return build_market_state(
            self.structure,
            volatility=self.volatility_environment,
            participation=self.participation_behavior,
        )

    def target_path(self, direction):
        """Build the causal target path for one already-resolved structural side."""

        from financial_dashboard.decision.target_path import build_target_path_from_snapshot

        return build_target_path_from_snapshot(self, direction)

    def quality_for_timeframe(self, timeframe: str) -> ContextDataQuality:
        normalized = timeframe.strip().lower()
        for key, quality in self.data_quality_by_timeframe:
            if key == normalized:
                return quality
        return ContextDataQuality.UNAVAILABLE


def build_decision_input_snapshot(workspace: Any) -> DecisionInputSnapshot:
    """Assemble the causal decision input from one completed workspace.

    The workspace's cross-domain builder already used the target-bounded Structure
    replay. Reusing those filtered projections here avoids accidentally reading the
    observer's later full-history state when a timeframe had to be clipped.
    """

    result: CrossDomainBuildResult | None = workspace.cross_domain_result
    if result is None:
        raise ValueError("decision input requires a READY cross-domain result")
    if result.structural is None or result.zones is None:
        raise ValueError("cross-domain result is missing required structural read models")

    context = result.context
    timeframes = tuple(str(item).strip().lower() for item in workspace.timeframes)
    trigger_timeframes = tuple(tf for tf in ("1h", "30m") if tf in timeframes)
    quality_by_timeframe = tuple(
        sorted(
            (
                item.timeframe,
                item.data_quality,
            )
            for item in result.structural.timeframe_facts
        )
    )

    return DecisionInputSnapshot(
        symbol=context.symbol,
        as_of=context.as_of,
        current_price=float(context.current_price),
        timeframes=timeframes,
        trigger_timeframes=trigger_timeframes,
        structure=result.structural,
        support_resistance=result.support_resistance,
        liquidity=result.liquidity,
        liquidity_landscape=result.liquidity_landscape,
        reaction=result.reaction,
        order_block_behavior=result.order_block_behavior,
        fvg_engulfing_lifecycle=result.fvg_engulfing_lifecycle,
        stabil_support=result.stabil_support,
        participation=result.participation,
        participation_behavior=result.participation_behavior,
        volatility=result.volatility,
        volatility_environment=result.volatility_environment,
        pattern=result.pattern,
        pattern_behavior=result.pattern_behavior,
        ham=result.ham,
        qualified_zones=result.zones,
        targeting=workspace.targeting_result,
        semantic_targeting=workspace.semantic_targeting_result,
        context=context,
        permission=result.permission,
        source_refs=context.source_refs,
        lineage_groups=context.lineage_groups,
        knowledge_boundary=context.knowledge_boundary,
        data_quality_by_timeframe=quality_by_timeframe,
    )


__all__ = ["DecisionInputSnapshot", "build_decision_input_snapshot"]
