from __future__ import annotations

import pandas as pd

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
from financial_dashboard.decision.reaction import (
    ReactionRelevancePolicy,
    ReactionState,
    assess_reaction,
    select_relevant_zones,
)
from financial_dashboard.decision.structural import StructuralDirection

_PRICE = 100.0
_T0 = pd.Timestamp("2024-01-01 00:00:00")


def _ref(
    domain: ContextDomain,
    native_id: str,
    *,
    timeframe: str = "1h",
    age_hours: float = 3.0,
    quality: ContextDataQuality = ContextDataQuality.VALID,
) -> FactRef:
    confirmed = _T0 + pd.Timedelta(hours=100)
    return FactRef(
        domain=domain,
        fact_type="TEST",
        symbol="THYAO",
        timeframe=timeframe,
        native_id=native_id,
        native_state="TEST",
        origin_time=confirmed - pd.Timedelta(hours=age_hours),
        confirmed_at=confirmed,
        available_at=confirmed,
        lineage_id=native_id,
        causal_family=CausalFamily.IMPULSE,
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=quality,
    )


def _ob(
    *,
    native_id: str = "OB:1:LONG",
    state: str = "FRESH",
    interaction: str = "APPROACHING",
    active: bool = True,
    age_bars: int | None = 3,
    distance_atr: float | None = 0.3,
    top: float = 101.0,
    bottom: float = 99.0,
    timeframe: str = "1h",
) -> OrderBlockBehaviorObservation:
    return OrderBlockBehaviorObservation(
        timeframe=timeframe,
        ref=_ref(ContextDomain.ORDER_BLOCK, native_id, timeframe=timeframe),
        identity=native_id,
        bullish=True,
        top=top,
        bottom=bottom,
        state=state,
        interaction=interaction,
        active=active,
        age_bars=age_bars if age_bars is not None else 0,
        bars_since_confirmation=2,
        mitigation_count=0,
        visit_count=0,
        deepest_fill_ratio=0.0,
        distance_atr=distance_atr,
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


def _fvg(
    *,
    native_id: str = "FVG:1",
    confirmed: bool = False,
    failed_reaction: bool = False,
    invalid: bool = False,
    full_fill: bool = False,
    age_hours: float = 3.0,
    lower: float = 99.0,
    upper: float = 100.0,
    formation_atr: float = 1.0,
    timeframe: str = "1h",
) -> FvgLifecycleProjection:
    return FvgLifecycleProjection(
        ref=_ref(ContextDomain.FVG, native_id, timeframe=timeframe, age_hours=age_hours),
        identity=native_id,
        direction=1,
        state="ACTIVE",
        lower_boundary=lower,
        upper_boundary=upper,
        quality=80.0,
        gap_atr=0.5,
        formation_atr=formation_atr,
        formation_index=5,
        first_test_index=10,
        wick_fill_ratio=0.2,
        close_fill_ratio=0.1,
        maximum_fill_ratio=0.2,
        reaction_evidence_count=1 if confirmed else 0,
        reaction_confirmed=confirmed,
        failed_reaction=failed_reaction,
        full_fill=full_fill,
        invalid=invalid,
        invalid_reason="",
        invalid_close_count=0,
    )


def _fvg_projection(
    fvg: tuple[FvgLifecycleProjection, ...] = (),
    engulfing: tuple[EngulfingLifecycleProjection, ...] = (),
) -> FvgEngulfingLifecycleProjection:
    return FvgEngulfingLifecycleProjection(
        symbol="THYAO",
        timeframes=("1h",),
        fvg=fvg,
        engulfing=engulfing,
    )


def _scoped(
    ob: OrderBlockBehaviorProjection | None,
    fvg: FvgEngulfingLifecycleProjection | None,
    policy: ReactionRelevancePolicy | None,
):
    if policy is None:
        return ob, fvg
    return select_relevant_zones(ob, fvg, current_price=_PRICE, policy=policy)


_DEFAULT = ReactionRelevancePolicy()
_LEGACY = None


def test_old_terminal_ob_failure_is_out_of_scope_with_default_policy():
    ob = _ob_projection(_ob(state="CONSUMED", active=False, age_bars=900, interaction="FAILED"))

    scoped_ob, _ = _scoped(ob, None, _DEFAULT)
    assessment = assess_reaction(
        StructuralDirection.LONG, order_blocks=scoped_ob, timeframes=("1h",)
    )
    assert assessment.failure_present is False
    assert assessment.state is not ReactionState.FAILED

    legacy_ob, _ = _scoped(ob, None, _LEGACY)
    legacy = assess_reaction(
        StructuralDirection.LONG, order_blocks=legacy_ob, timeframes=("1h",)
    )
    assert legacy.failure_present is True


def test_old_but_active_ob_stays_in_scope():
    ob = _ob_projection(_ob(state="FRESH", active=True, age_bars=900, interaction="APPROACHING"))

    scoped_ob, _ = _scoped(ob, None, _DEFAULT)
    assessment = assess_reaction(
        StructuralDirection.LONG, order_blocks=scoped_ob, timeframes=("1h",)
    )
    assert assessment.developing_present is True


def test_far_terminal_zone_is_released_by_distance():
    ob = _ob_projection(
        _ob(state="CONSUMED", active=False, age_bars=5, interaction="FAILED", distance_atr=12.0)
    )

    scoped_ob, _ = _scoped(ob, None, _DEFAULT)
    assert len(scoped_ob.observations) == 0


def test_far_live_zone_is_also_out_of_scope_when_distance_known():
    ob = _ob_projection(_ob(active=True, age_bars=2, distance_atr=9.0))

    scoped_ob, _ = _scoped(ob, None, _DEFAULT)
    assert len(scoped_ob.observations) == 0


def test_fvg_terminal_age_rule_uses_derived_timestamps():
    old_full_fill = _fvg(full_fill=True, age_hours=200.0)  # 200 x 1h bars
    young_invalid = _fvg(invalid=True, age_hours=4.0)  # 4 x 1h bars
    projection = _fvg_projection((old_full_fill, young_invalid))

    scoped_ob, scoped_fvg = _scoped(None, projection, _DEFAULT)
    ages = [pd.Timestamp(row.ref.confirmed_at) - pd.Timestamp(row.ref.origin_time) for row in scoped_fvg.fvg]
    assert len(scoped_fvg.fvg) == 1
    assert ages[0] == pd.Timedelta(hours=4)
    assessment = assess_reaction(
        StructuralDirection.LONG,
        order_blocks=scoped_ob,
        fvg_engulfing=scoped_fvg,
        timeframes=("1h",),
    )
    assert assessment.failure_present is True  # young invalid FVG still votes


def test_supersession_releases_failure_when_confirmed_zone_overlaps():
    failed = _ob(
        native_id="OB:1:LONG",
        state="CONSUMED",
        active=False,
        age_bars=4,
        interaction="FAILED",
        top=101.0,
        bottom=99.0,
    )
    confirmed = _ob(
        native_id="OB:2:LONG",
        state="REACTION_CONFIRMED",
        interaction="REACTION_CONFIRMED",
        active=True,
        age_bars=2,
        top=102.0,
        bottom=100.0,
    )
    ob = _ob_projection(failed, confirmed)

    assessment = assess_reaction(
        StructuralDirection.LONG,
        order_blocks=ob,
        timeframes=("1h",),
        relevance=_DEFAULT,
    )
    assert assessment.failure_present is False
    assert assessment.confirmation_present is True
    assert any("OB_FAILED_SUPERSEDED" in reason for reason in assessment.reasons)

    without_supersession = assess_reaction(
        StructuralDirection.LONG,
        order_blocks=ob,
        timeframes=("1h",),
        relevance=ReactionRelevancePolicy(supersession=False),
    )
    assert without_supersession.failure_present is True


def test_young_live_failed_reaction_fvg_still_votes():
    projection = _fvg_projection((_fvg(failed_reaction=True, age_hours=5.0),))

    assessment = assess_reaction(
        StructuralDirection.LONG,
        fvg_engulfing=projection,
        timeframes=("1h",),
        relevance=_DEFAULT,
    )
    assert assessment.failure_present is True


def test_live_ob_with_unknown_distance_stays_in_scope():
    ob = _ob_projection(_ob(active=True, distance_atr=None))

    scoped_ob, _ = _scoped(ob, None, _DEFAULT)
    assert len(scoped_ob.observations) == 1


def test_terminal_fvg_with_underivable_age_fails_closed():
    # OB rows always carry a native int age; only FVG/engulfing ages are derived.
    # A terminal FVG whose age cannot be derived must fail closed (out of scope).
    row = _fvg(full_fill=True)
    broken_ref = FactRef(
        domain=row.ref.domain,
        fact_type=row.ref.fact_type,
        symbol=row.ref.symbol,
        timeframe=row.ref.timeframe,
        native_id="FVG:broken",
        native_state=row.ref.native_state,
        origin_time="not-a-timestamp",
        confirmed_at=row.ref.confirmed_at,
        available_at=row.ref.available_at,
        lineage_id="FVG:broken",
        causal_family=row.ref.causal_family,
        source_family=row.ref.source_family,
        data_quality=row.ref.data_quality,
    )
    projection = _fvg_projection(
        (
            FvgLifecycleProjection(
                ref=broken_ref,
                identity="FVG:broken",
                direction=1,
                state="ACTIVE",
                lower_boundary=99.0,
                upper_boundary=100.0,
                quality=80.0,
                gap_atr=0.5,
                formation_atr=1.0,
                formation_index=5,
                first_test_index=10,
                wick_fill_ratio=0.2,
                close_fill_ratio=0.1,
                maximum_fill_ratio=0.2,
                reaction_evidence_count=0,
                reaction_confirmed=False,
                failed_reaction=False,
                full_fill=True,
                invalid=False,
                invalid_reason="",
                invalid_close_count=0,
            ),
        )
    )

    _, scoped_fvg = select_relevant_zones(
        None, projection, current_price=_PRICE, policy=_DEFAULT
    )
    assert len(scoped_fvg.fvg) == 0


def test_select_relevant_zones_is_pure_and_handles_none():
    ob = _ob_projection(_ob(state="CONSUMED", active=False, age_bars=900, interaction="FAILED"))
    fvg = _fvg_projection((_fvg(full_fill=True, age_hours=900.0),))

    scoped_ob, scoped_fvg = select_relevant_zones(ob, fvg, current_price=_PRICE, policy=_DEFAULT)
    assert len(scoped_ob.observations) == 0
    assert len(scoped_fvg.fvg) == 0
    # Source projections are untouched.
    assert len(ob.observations) == 1
    assert len(fvg.fvg) == 1

    none_ob, none_fvg = select_relevant_zones(None, None, current_price=_PRICE, policy=_DEFAULT)
    assert none_ob is None
    assert none_fvg is None


def test_policy_validation_rejects_negative_bounds():
    import pytest

    with pytest.raises(ValueError):
        ReactionRelevancePolicy(max_age_bars=-1)
    with pytest.raises(ValueError):
        ReactionRelevancePolicy(max_distance_atr=-0.5)
