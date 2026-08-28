from __future__ import annotations

import random
from datetime import timedelta
from types import SimpleNamespace

import pandas as pd

from financial_dashboard.context.fvg_engulfing_projection import (
    PROJECTION_MAX_TERMINAL_AGE_BARS as FVG_MAX_AGE,
    project_fvg_engulfing_lifecycle,
)
from financial_dashboard.context.order_block_behavior_projection import (
    PROJECTION_MAX_TERMINAL_AGE_BARS as OB_MAX_AGE,
    PROJECTION_MAX_DISTANCE_ATR as OB_MAX_DIST,
    project_order_block_behavior,
)
from financial_dashboard.context.projections import (
    PROJECTION_MAX_DISTANCE_ATR as LIQ_MAX_DIST,
    PROJECTION_MAX_TERMINAL_AGE_BARS as LIQ_MAX_AGE,
    project_liquidity,
)
from financial_dashboard.engines.liquidity_behavior import (
    LiquidityBehaviorSnapshot,
    LiquidityLandscapeState,
    LiquidityPoolBehaviorSnapshot,
    LiquidityPoolMaturity,
    LiquidityPriceRelation,
    LiquidityRemovalState,
)
from financial_dashboard.engines.liquidity_models import LiquiditySide
from financial_dashboard.targeting.models import (
    LiquidityScope,
    TargetEvidence,
    TargetEvidenceFamily,
    TargetEvidenceType,
    TargetRole,
)
from financial_dashboard.decision.reaction import (
    ReactionRelevancePolicy,
    select_relevant_zones,
)

from tests.test_decision_reaction_relevance import _fvg_projection, _ob, _ob_projection

NOW = pd.Timestamp("2026-01-01 00:00:00")
AVAILABLE = NOW + pd.Timedelta(hours=1)
PRICE = 100.0


# --------------------------------------------------------------------------- #
# OB behavior projection bounds                                               #
# --------------------------------------------------------------------------- #

def _behavior_row(*, identity: str, active: bool, age_bars: int, distance_atr):
    base = _ob(
        native_id=identity,
        state="FRESH" if active else "CONSUMED",
        interaction="APPROACHING" if active else "FAILED",
        active=active,
        age_bars=age_bars,
        distance_atr=distance_atr,
    )
    # project_order_block_behavior consumes the tracker's native row shape.
    return SimpleNamespace(
        identity=identity,
        bullish=True,
        top=base.top,
        bottom=base.bottom,
        state=base.state,
        interaction=base.interaction,
        active=active,
        age_bars=age_bars,
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
        terminal_reason=None if active else "GAP_THROUGH",
    )


def _ob_replay(rows):
    return SimpleNamespace(
        symbol="THYAO",
        timeframes=("1h",),
        snapshots={"1h": SimpleNamespace(as_of=NOW, available_at=AVAILABLE)},
        order_block_behavior={"1h": rows},
    )


def test_ob_projection_drops_old_terminal_keeps_live_and_young():
    rows = [
        _behavior_row(identity="OB:live-old:LONG", active=True, age_bars=900, distance_atr=0.2),
        _behavior_row(identity="OB:dead-young:LONG", active=False, age_bars=40, distance_atr=1.0),
        _behavior_row(identity="OB:dead-old:LONG", active=False, age_bars=OB_MAX_AGE + 1, distance_atr=1.0),
        _behavior_row(identity="OB:dead-far:LONG", active=False, age_bars=10, distance_atr=OB_MAX_DIST + 1),
        _behavior_row(identity="OB:dead-no-dist:LONG", active=False, age_bars=10, distance_atr=None),
    ]
    projection = project_order_block_behavior(
        _ob_replay(rows), data_quality_by_timeframe={"1h": "VALID"}
    )
    kept = {item.identity for item in projection.observations}
    assert kept == {"OB:live-old:LONG", "OB:dead-young:LONG", "OB:dead-no-dist:LONG"}


# --------------------------------------------------------------------------- #
# FVG / engulfing projection bounds                                           #
# --------------------------------------------------------------------------- #

def _fvg_replay(fvg_items, engulfing_items=()):
    return SimpleNamespace(
        symbol="THYAO",
        timeframes=("1h",),
        snapshots={"1h": SimpleNamespace(as_of=NOW, available_at=AVAILABLE)},
        fvg_lifecycle={"1h": fvg_items},
        engulfing_lifecycle={"1h": engulfing_items},
    )


def _native_fvg(*, identity: str, invalid: bool, full_fill: bool, age_hours: float):
    return SimpleNamespace(
        identity=identity,
        direction=1,
        state="ACTIVE",
        lower_boundary=99.0,
        upper_boundary=100.0,
        quality=80.0,
        gap_atr=0.5,
        formation_atr=1.0,
        formation_time=NOW - pd.Timedelta(hours=age_hours),
        formation_index=5,
        first_test_index=10,
        wick_fill_ratio=0.2,
        close_fill_ratio=0.1,
        maximum_fill_ratio=0.2,
        reaction_evidence_count=0,
        reaction_confirmed=False,
        failed_reaction=False,
        full_fill=full_fill,
        invalid=invalid,
        invalid_reason="",
        invalid_close_count=0,
    )


def test_fvg_projection_drops_old_terminal_keeps_live_and_young():
    items = [
        _native_fvg(identity="FVG:live-old", invalid=False, full_fill=False, age_hours=5000),
        _native_fvg(identity="FVG:dead-young", invalid=True, full_fill=False, age_hours=40),
        _native_fvg(
            identity="FVG:dead-old",
            invalid=False,
            full_fill=True,
            age_hours=(FVG_MAX_AGE + 1),
        ),
    ]
    projection = project_fvg_engulfing_lifecycle(
        _fvg_replay(items), data_quality_by_timeframe={"1h": "VALID"}
    )
    kept = {row.identity for row in projection.fvg}
    assert kept == {"FVG:live-old", "FVG:dead-young"}


# --------------------------------------------------------------------------- #
# Decision equivalence: bounded history is a superset of the decision scope    #
# --------------------------------------------------------------------------- #

def _bounded(ob_projection, fvg_projection):
    """Apply the projection bound to already-built decision projections."""

    kept_ob = tuple(
        item
        for item in ob_projection.observations
        if item.active
        or (
            item.age_bars <= OB_MAX_AGE
            and (item.distance_atr is None or item.distance_atr <= OB_MAX_DIST)
        )
    )
    kept_fvg = tuple(
        row
        for row in fvg_projection.fvg
        if not (row.invalid or row.full_fill)
        or _native_age_hours(row) <= FVG_MAX_AGE
    )
    from dataclasses import replace

    return (
        replace(ob_projection, observations=kept_ob),
        replace(fvg_projection, fvg=kept_fvg),
    )


def _native_age_hours(row) -> float:
    delta = pd.Timestamp(row.ref.confirmed_at) - pd.Timestamp(row.ref.origin_time)
    return delta / pd.Timedelta(hours=1)


def test_projection_bound_is_decision_superset_randomized():
    rng = random.Random(20260828)
    policy = ReactionRelevancePolicy()
    for _ in range(300):
        zones = []
        for index in range(12):
            terminal = rng.random() < 0.5
            active = not terminal
            age = rng.choice([0, 5, 49, 50, 51, 99, 100, 101, 300, 2000])
            distance = rng.choice([None, 0.0, 3.0, 4.9, 5.0, 5.1, 9.9, 10.0, 12.0])
            zones.append(
                _ob(
                    native_id=f"OB:{index}",
                    state="CONSUMED" if terminal else "FRESH",
                    interaction="FAILED" if terminal else "APPROACHING",
                    active=active,
                    age_bars=age,
                    distance_atr=distance,
                    terminal_reason="GAP_THROUGH" if terminal else None,
                )
            )
        full_ob = _ob_projection(*zones)
        full_fvg = _fvg_projection(())
        bounded_ob, bounded_fvg = _bounded(full_ob, full_fvg)

        selected_full = select_relevant_zones(
            full_ob, full_fvg, current_price=PRICE, policy=policy
        )
        selected_bounded = select_relevant_zones(
            bounded_ob, bounded_fvg, current_price=PRICE, policy=policy
        )
        assert [item.identity for item in selected_full[0].observations] == [
            item.identity for item in selected_bounded[0].observations
        ]


# --------------------------------------------------------------------------- #
# Liquidity projection bounds                                                 #
# --------------------------------------------------------------------------- #


def _liq_pool(
    *,
    identity: str,
    removal: LiquidityRemovalState,
    age_bars: int,
    distance_atr,
    level: float = 105.0,
) -> LiquidityPoolBehaviorSnapshot:
    return LiquidityPoolBehaviorSnapshot(
        identity=identity,
        side=LiquiditySide.BSL,
        level=level,
        maturity=LiquidityPoolMaturity.STALE
        if removal in {LiquidityRemovalState.CONSUMED, LiquidityRemovalState.INVALIDATED}
        else LiquidityPoolMaturity.ESTABLISHED,
        relation=LiquidityPriceRelation.DISTANT,
        removal=removal,
        age_bars=age_bars,
        bars_since_touch=age_bars,
        touch_count=2,
        distance_atr=distance_atr,
        distance_delta_atr=None,
    )


def _liq_evidence(
    *,
    identity: str,
    source_state: str,
    target_eligible: bool,
    age_hours: float,
    level: float,
) -> TargetEvidence:
    origin = NOW - pd.Timedelta(hours=age_hours)
    return TargetEvidence(
        uid=f"TE-{identity}",
        symbol="THYAO",
        timeframe="1h",
        evidence_type=TargetEvidenceType.LIQUIDITY,
        family=TargetEvidenceFamily.STRUCTURAL,
        roles=(TargetRole.MAGNET,),
        low=level,
        high=level,
        anchor_price=level,
        origin_index=0,
        origin_time=origin,
        confirmed_at=origin,
        available_at=origin + pd.Timedelta(hours=1),
        source_state=source_state,
        target_eligible=target_eligible,
        native_origin_id=f"LIQ:1h:{identity}",
        origin_event_id=f"LIQ:1h:{identity}",
        source_identity=identity,
        formation_atr=None,
        source_quality=None,
        liquidity_scope=LiquidityScope.EXTERNAL,
    )


def _liq_replay(evidence, pools):
    return SimpleNamespace(
        symbol="THYAO",
        timeframes=("1h",),
        evidence=tuple(evidence),
        liquidity_behavior={
            "1h": LiquidityBehaviorSnapshot(
                as_of=NOW,
                landscape=LiquidityLandscapeState.NO_NEARBY_OBJECTIVE,
                pools=tuple(pools),
            )
        },
        snapshots={
            "1h": SimpleNamespace(
                as_of=NOW,
                available_at=AVAILABLE,
                current_price=PRICE,
                atr=1.0,
            )
        },
    )


def test_liquidity_projection_drops_old_terminal_keeps_live_and_young():
    evidence = [
        _liq_evidence(
            identity="live-old",
            source_state="ACTIVE",
            target_eligible=True,
            age_hours=5000,
            level=100.2,
        ),
        _liq_evidence(
            identity="dead-young",
            source_state="CONSUMED",
            target_eligible=False,
            age_hours=40,
            level=101.0,
        ),
        _liq_evidence(
            identity="dead-old",
            source_state="CONSUMED",
            target_eligible=False,
            age_hours=LIQ_MAX_AGE + 1,
            level=101.0,
        ),
        _liq_evidence(
            identity="dead-far",
            source_state="INVALIDATED",
            target_eligible=False,
            age_hours=10,
            level=PRICE + (LIQ_MAX_DIST + 1),
        ),
        _liq_evidence(
            identity="swept-old",
            source_state="SWEPT",
            target_eligible=False,
            age_hours=5000,
            level=PRICE + 50,
        ),
    ]
    pools = [
        _liq_pool(
            identity="live-old",
            removal=LiquidityRemovalState.UNTOUCHED,
            age_bars=900,
            distance_atr=0.2,
        ),
        _liq_pool(
            identity="dead-young",
            removal=LiquidityRemovalState.CONSUMED,
            age_bars=40,
            distance_atr=1.0,
        ),
        _liq_pool(
            identity="dead-old",
            removal=LiquidityRemovalState.CONSUMED,
            age_bars=LIQ_MAX_AGE + 1,
            distance_atr=1.0,
        ),
        _liq_pool(
            identity="dead-far",
            removal=LiquidityRemovalState.INVALIDATED,
            age_bars=10,
            distance_atr=LIQ_MAX_DIST + 1,
        ),
        _liq_pool(
            identity="dead-no-dist",
            removal=LiquidityRemovalState.CONSUMED,
            age_bars=10,
            distance_atr=None,
        ),
        _liq_pool(
            identity="accepted-old",
            removal=LiquidityRemovalState.ACCEPTED_BEYOND,
            age_bars=900,
            distance_atr=20.0,
        ),
    ]
    projection = project_liquidity(
        _liq_replay(evidence, pools), data_quality_by_timeframe={"1h": "VALID"}
    )
    kept_evidence = {item.ref.native_id.split(":")[-1] for item in projection.observations}
    kept_behavior = {item.pool_identity for item in projection.behavior_observations}
    assert kept_evidence == {"live-old", "dead-young", "swept-old"}
    assert kept_behavior == {"live-old", "dead-young", "dead-no-dist", "accepted-old"}
