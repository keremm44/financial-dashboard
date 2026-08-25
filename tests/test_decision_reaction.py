from __future__ import annotations

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.context.fvg_engulfing_projection import (
    EngulfingLifecycleProjection,
    FvgEngulfingLifecycleProjection,
    FvgLifecycleProjection,
)
from financial_dashboard.context.order_block_behavior_projection import (
    OrderBlockBehaviorObservation,
    OrderBlockBehaviorProjection,
)
from financial_dashboard.decision.reaction import ReactionState, assess_reaction
from financial_dashboard.decision.structural import StructuralDirection


def _ref(domain: ContextDomain, native_id: str, *, quality: ContextDataQuality = ContextDataQuality.VALID) -> FactRef:
    return FactRef(
        domain=domain,
        fact_type="TEST",
        symbol="THYAO",
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


def _ob(*, bullish: bool = True, state: str = "FRESH", interaction: str = "APPROACHING", active: bool = True) -> OrderBlockBehaviorObservation:
    return OrderBlockBehaviorObservation(
        timeframe="1h",
        ref=_ref(ContextDomain.ORDER_BLOCK, f"OB:{state}:{interaction}"),
        identity="OB:1:LONG",
        bullish=bullish,
        top=101.0,
        bottom=99.0,
        state=state,
        interaction=interaction,
        active=active,
        age_bars=3,
        bars_since_confirmation=2,
        mitigation_count=0,
        visit_count=0,
        deepest_fill_ratio=0.0,
        distance_atr=0.3,
        total_inside_bars=0,
        inside_close_bars=0,
        current_visit_bars=0,
        close_inside=False,
        range_intersects=False,
        first_entry_index=None,
        last_entry_index=None,
        favorable_exit_index=None,
        bars_held_favorable=0,
        max_favorable_move_atr=0.0,
        terminal_reason=None,
    )


def _ob_projection(*items: OrderBlockBehaviorObservation) -> OrderBlockBehaviorProjection:
    return OrderBlockBehaviorProjection(symbol="THYAO", timeframes=("1h",), observations=items)


def _fvg(*, confirmed: bool = False, failed: bool = False, first_test: int | None = 10) -> FvgLifecycleProjection:
    return FvgLifecycleProjection(
        ref=_ref(ContextDomain.FVG, f"FVG:{confirmed}:{failed}"),
        identity="FVG:1",
        direction=1,
        state="ACTIVE",
        lower_boundary=99.0,
        upper_boundary=100.0,
        quality=80.0,
        gap_atr=0.5,
        formation_atr=1.0,
        formation_index=5,
        first_test_index=first_test,
        wick_fill_ratio=0.2,
        close_fill_ratio=0.1,
        maximum_fill_ratio=0.2,
        reaction_evidence_count=1 if confirmed else 0,
        reaction_confirmed=confirmed,
        failed_reaction=failed,
        full_fill=False,
        invalid=False,
        invalid_reason="",
        invalid_close_count=0,
    )


def _engulf(*, confirmed: bool) -> EngulfingLifecycleProjection:
    return EngulfingLifecycleProjection(
        ref=_ref(ContextDomain.ENGULFING, f"ENG:{confirmed}"),
        identity="ENG:1",
        direction=1,
        state="ACTIVE",
        lower_boundary=99.0,
        upper_boundary=101.0,
        quality=80.0,
        body_atr=0.8,
        formation_index=5,
        first_test_index=6,
        maximum_retrace_ratio=0.2,
        continuation_evidence_count=1 if confirmed else 0,
        continuation_confirmed=confirmed,
        weakened=False,
        weakened_index=None,
        invalid=False,
        completion_reason="",
    )


def test_ob_reaction_confirmed() -> None:
    result = assess_reaction(
        StructuralDirection.LONG,
        order_blocks=_ob_projection(_ob(state="REACTION_CONFIRMED", interaction="REACTION_CONFIRMED")),
    )
    assert result.state is ReactionState.CONFIRMED


def test_failed_reaction_does_not_change_structural_side() -> None:
    side = StructuralDirection.LONG
    result = assess_reaction(
        side,
        order_blocks=_ob_projection(_ob(state="CONSUMED", interaction="FAILED", active=False)),
    )
    assert result.state is ReactionState.FAILED
    assert side is StructuralDirection.LONG


def test_tested_fvg_is_developing_until_native_confirmation() -> None:
    lifecycle = FvgEngulfingLifecycleProjection(
        symbol="THYAO",
        timeframes=("1h",),
        fvg=(_fvg(),),
        engulfing=(),
    )
    result = assess_reaction(StructuralDirection.LONG, fvg_engulfing=lifecycle)
    assert result.state is ReactionState.DEVELOPING


def test_engulfing_cannot_manufacture_reaction_without_zone() -> None:
    lifecycle = FvgEngulfingLifecycleProjection(
        symbol="THYAO",
        timeframes=("1h",),
        fvg=(),
        engulfing=(_engulf(confirmed=True),),
    )
    result = assess_reaction(StructuralDirection.LONG, fvg_engulfing=lifecycle)
    assert result.state is ReactionState.UNKNOWN
    assert not result.confirmation_present


def test_engulfing_can_confirm_existing_reaction_path() -> None:
    lifecycle = FvgEngulfingLifecycleProjection(
        symbol="THYAO",
        timeframes=("1h",),
        fvg=(_fvg(first_test=10),),
        engulfing=(_engulf(confirmed=True),),
    )
    result = assess_reaction(StructuralDirection.LONG, fvg_engulfing=lifecycle)
    assert result.state is ReactionState.CONFIRMED


def test_mixed_confirmed_and_failed_lineages_are_not_averaged_away() -> None:
    lifecycle = FvgEngulfingLifecycleProjection(
        symbol="THYAO",
        timeframes=("1h",),
        fvg=(_fvg(confirmed=True), _fvg(failed=True)),
        engulfing=(),
    )
    result = assess_reaction(StructuralDirection.LONG, fvg_engulfing=lifecycle)
    assert result.state is ReactionState.CONFIRMED
    assert result.failure_present
    assert "REACTION_MIXED_CONFIRMED_AND_FAILED_LINEAGES" in result.reasons
