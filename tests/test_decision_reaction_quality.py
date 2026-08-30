from __future__ import annotations

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.context.fvg_engulfing_projection import (
    FvgEngulfingLifecycleProjection,
    FvgLifecycleProjection,
)
from financial_dashboard.context.order_block_behavior_projection import (
    OrderBlockBehaviorObservation,
    OrderBlockBehaviorProjection,
)
from financial_dashboard.decision.evidence_quality import normalize_decision_reaction_projections
from financial_dashboard.decision.reaction import ReactionState, assess_reaction
from financial_dashboard.decision.structural import StructuralDirection


def _ref(domain: ContextDomain, native_id: str, quality: ContextDataQuality) -> FactRef:
    return FactRef(
        domain=domain,
        fact_type="TEST",
        symbol="ASELS",
        timeframe="1h",
        native_id=native_id,
        native_state="TEST",
        origin_time=1,
        confirmed_at=1,
        available_at=1,
        lineage_id=native_id,
        causal_family=CausalFamily.IMPULSE,
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=quality,
    )


def _fvg(quality: ContextDataQuality, *, confirmed: bool) -> FvgLifecycleProjection:
    return FvgLifecycleProjection(
        ref=_ref(ContextDomain.FVG, f"FVG:{quality}:{confirmed}", quality),
        identity="FVG:1h:1:1",
        direction=1,
        state="ACTIVE",
        lower_boundary=99.0,
        upper_boundary=100.0,
        quality=80.0,
        gap_atr=0.5,
        formation_atr=1.0,
        formation_index=5,
        first_test_index=8 if confirmed else None,
        wick_fill_ratio=0.2,
        close_fill_ratio=0.1,
        maximum_fill_ratio=0.2,
        reaction_evidence_count=3 if confirmed else 0,
        reaction_confirmed=confirmed,
        failed_reaction=False,
        full_fill=False,
        invalid=False,
        invalid_reason="",
        invalid_close_count=0,
    )


def _ob(
    quality: ContextDataQuality,
    *,
    interaction: str,
) -> OrderBlockBehaviorObservation:
    return OrderBlockBehaviorObservation(
        timeframe="1h",
        ref=_ref(ContextDomain.ORDER_BLOCK, f"OB:{quality}:{interaction}", quality),
        identity="OB:1:1",
        bullish=True,
        top=101.0,
        bottom=99.0,
        state="REACTION_CONFIRMED" if interaction == "REACTION_CONFIRMED" else "FRESH",
        interaction=interaction,
        active=True,
        age_bars=3,
        bars_since_confirmation=2,
        mitigation_count=0,
        visit_count=1 if interaction == "REACTION_CONFIRMED" else 0,
        deepest_fill_ratio=0.2,
        distance_atr=0.2,
        total_inside_bars=1,
        inside_close_bars=0,
        current_visit_bars=0,
        close_inside=False,
        range_intersects=False,
        first_entry_index=5 if interaction == "REACTION_CONFIRMED" else None,
        last_entry_index=5 if interaction == "REACTION_CONFIRMED" else None,
        favorable_exit_index=6 if interaction == "REACTION_CONFIRMED" else None,
        bars_held_favorable=1 if interaction == "REACTION_CONFIRMED" else 0,
        max_favorable_move_atr=1.2 if interaction == "REACTION_CONFIRMED" else 0.0,
        terminal_reason=None,
    )


def test_data_limited_confirmed_fvg_is_valid_for_decision_reaction() -> None:
    lifecycle = FvgEngulfingLifecycleProjection(
        symbol="ASELS",
        timeframes=("1h",),
        fvg=(_fvg(ContextDataQuality.DATA_LIMITED, confirmed=True),),
        engulfing=(),
    )

    _, normalized = normalize_decision_reaction_projections(None, lifecycle)

    assert normalized is not None
    assert normalized.fvg[0].ref.data_quality is ContextDataQuality.VALID
    result = assess_reaction(StructuralDirection.LONG, fvg_engulfing=normalized)
    assert result.state is ReactionState.CONFIRMED
    assert result.confirmation_present


def test_data_limited_confirmed_ob_is_valid_for_decision_reaction() -> None:
    projection = OrderBlockBehaviorProjection(
        symbol="ASELS",
        timeframes=("1h",),
        observations=(_ob(ContextDataQuality.DATA_LIMITED, interaction="REACTION_CONFIRMED"),),
    )

    normalized, _ = normalize_decision_reaction_projections(projection, None)

    assert normalized is not None
    assert normalized.observations[0].ref.data_quality is ContextDataQuality.VALID
    result = assess_reaction(StructuralDirection.LONG, order_blocks=normalized)
    assert result.state is ReactionState.CONFIRMED
    assert result.confirmation_present


def test_unavailable_confirmation_is_never_promoted() -> None:
    lifecycle = FvgEngulfingLifecycleProjection(
        symbol="ASELS",
        timeframes=("1h",),
        fvg=(_fvg(ContextDataQuality.UNAVAILABLE, confirmed=True),),
        engulfing=(),
    )

    _, normalized = normalize_decision_reaction_projections(None, lifecycle)

    assert normalized is not None
    assert normalized.fvg[0].ref.data_quality is ContextDataQuality.UNAVAILABLE
    result = assess_reaction(StructuralDirection.LONG, fvg_engulfing=normalized)
    assert result.state is ReactionState.UNKNOWN
    assert not result.confirmation_present


def test_data_limited_zone_without_observed_interaction_is_not_promoted() -> None:
    projection = OrderBlockBehaviorProjection(
        symbol="ASELS",
        timeframes=("1h",),
        observations=(_ob(ContextDataQuality.DATA_LIMITED, interaction="OUTSIDE"),),
    )

    normalized, _ = normalize_decision_reaction_projections(projection, None)

    assert normalized is not None
    assert normalized.observations[0].ref.data_quality is ContextDataQuality.DATA_LIMITED
    result = assess_reaction(StructuralDirection.LONG, order_blocks=normalized)
    assert result.state is ReactionState.UNKNOWN
    assert not result.confirmation_present
