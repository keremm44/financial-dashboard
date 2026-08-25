from __future__ import annotations

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.context.projections import (
    StabilSupportBehaviorProjection,
    StabilSupportProjection,
)
from financial_dashboard.decision.durability import DurabilityState, assess_durability


def _ref(state: str = "ACTIVE") -> FactRef:
    return FactRef(
        domain=ContextDomain.STABIL_SUPPORT,
        fact_type="DAILY_STRUCTURAL_SUPPORT",
        symbol="THYAO",
        timeframe="1d",
        native_id="STABIL:1",
        native_state=state,
        origin_time=1,
        confirmed_at=1,
        available_at=1,
        lineage_id="STABIL:1",
        causal_family=CausalFamily.STRUCTURAL_LEVEL,
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=ContextDataQuality.VALID,
    )


def _projection(
    *,
    validity: str = "ACTIVE",
    progression: str = "SAME",
    interaction: str = "HOLDING_ABOVE",
    motion: str = "FLAT",
    reclaim_count: int = 0,
    quality: ContextDataQuality = ContextDataQuality.VALID,
    with_support: bool = True,
) -> StabilSupportProjection:
    return StabilSupportProjection(
        symbol="THYAO",
        timeframe="1d",
        as_of=1,
        data_quality=quality,
        support_ref=_ref(validity) if with_support else None,
        support_level=100.0 if with_support else None,
        support_floor=99.0 if with_support else None,
        validity=validity,
        dynamics="FLAT",
        progression=progression,
        distance_pct=1.0,
        distance_atr=0.5,
        bars_above_support=5,
        bars_below_support=0,
        reclaim_count=reclaim_count,
        events=(),
        behavior=StabilSupportBehaviorProjection(
            motion=motion,
            relation="ABOVE_NEAR",
            interaction=interaction,
            bars_since_rebase=3,
            cross_count=0,
            last_rebase_step_atr=None,
            reclaim_active=False,
        ),
    )


def test_active_clean_stabil_is_healthy() -> None:
    result = assess_durability(_projection())
    assert result.state is DurabilityState.HEALTHY


def test_breach_is_fractured_not_structural_invalidation() -> None:
    result = assess_durability(_projection(validity="BREACHED", interaction="BREAKDOWN_ACCEPTED"))
    assert result.state is DurabilityState.FRACTURED
    assert "INVALIDATED" not in " ".join(result.reasons)


def test_below_floor_is_broken() -> None:
    result = assess_durability(_projection(validity="BELOW_FLOOR", interaction="DOWNSIDE_CONTINUATION"))
    assert result.state is DurabilityState.BROKEN


def test_native_softening_context_does_not_require_magic_thresholds() -> None:
    result = assess_durability(_projection(progression="REBASED_LOWER", motion="FALLING"))
    assert result.state is DurabilityState.SOFTENING


def test_missing_or_degraded_stabil_is_unknown_not_neutral() -> None:
    assert assess_durability(None).state is DurabilityState.UNKNOWN
    result = assess_durability(_projection(quality=ContextDataQuality.DATA_LIMITED))
    assert result.state is DurabilityState.UNKNOWN


def test_no_native_support_is_unknown() -> None:
    result = assess_durability(_projection(validity="NO_SUPPORT", with_support=False))
    assert result.state is DurabilityState.UNKNOWN
