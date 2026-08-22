from __future__ import annotations

from dataclasses import replace

import pandas as pd

from financial_dashboard.targeting.arrival import build_semantic_targeting_snapshot
from financial_dashboard.targeting.models import (
    TargetEvidence,
    TargetEvidenceFamily,
    TargetEvidenceType,
    TargetRole,
)
from financial_dashboard.targeting.semantic_models import (
    ArrivalPosition,
    ArrivalState,
    BehaviorDirection,
    ObjectiveKind,
    ReactionKind,
    SemanticRole,
)
from financial_dashboard.targeting.semantic_roles import (
    semantic_roles,
    to_confirmation,
    to_objective,
    to_reaction_zone,
)


TS = pd.Timestamp("2026-08-21 14:00", tz="Europe/Istanbul")


def _evidence(
    evidence_type: TargetEvidenceType,
    *,
    uid: str,
    low: float,
    high: float,
    origin: str | None = None,
    roles: tuple[TargetRole, ...] | None = None,
    eligible: bool = True,
) -> TargetEvidence:
    role_map = {
        TargetEvidenceType.LIQUIDITY: (TargetRole.MAGNET,),
        TargetEvidenceType.ORDER_BLOCK: (TargetRole.SUPPLY, TargetRole.REACTION),
        TargetEvidenceType.FVG: (TargetRole.IMBALANCE, TargetRole.DEMAND, TargetRole.REACTION),
        TargetEvidenceType.ENGULFING: (TargetRole.REACTION, TargetRole.DEMAND),
        TargetEvidenceType.SUPPORT_RESISTANCE: (TargetRole.SUPPLY, TargetRole.REACTION),
    }
    family_map = {
        TargetEvidenceType.LIQUIDITY: TargetEvidenceFamily.STRUCTURAL,
        TargetEvidenceType.ORDER_BLOCK: TargetEvidenceFamily.SUPPLY_DEMAND,
        TargetEvidenceType.FVG: TargetEvidenceFamily.IMBALANCE,
        TargetEvidenceType.ENGULFING: TargetEvidenceFamily.REACTION,
        TargetEvidenceType.SUPPORT_RESISTANCE: TargetEvidenceFamily.STRUCTURAL,
    }
    return TargetEvidence(
        uid=uid,
        symbol="TEST",
        timeframe="1h",
        evidence_type=evidence_type,
        family=family_map[evidence_type],
        roles=roles or role_map[evidence_type],
        low=low,
        high=high,
        anchor_price=(low + high) / 2.0,
        origin_index=10,
        origin_time=TS,
        confirmed_at=TS,
        available_at=TS,
        source_state="ACTIVE",
        target_eligible=eligible,
        native_origin_id=origin or uid,
        origin_event_id=origin or uid,
        source_identity=uid,
    )


def test_only_liquidity_can_be_phase1_objective() -> None:
    liq = _evidence(TargetEvidenceType.LIQUIDITY, uid="liq", low=110.0, high=110.0)
    ob = _evidence(TargetEvidenceType.ORDER_BLOCK, uid="ob", low=109.0, high=111.0)
    fvg = _evidence(TargetEvidenceType.FVG, uid="fvg", low=108.5, high=109.5)
    engulf = _evidence(TargetEvidenceType.ENGULFING, uid="eng", low=108.0, high=109.0)
    sr = _evidence(TargetEvidenceType.SUPPORT_RESISTANCE, uid="sr", low=109.5, high=110.5)

    objective = to_objective(liq, current_price=100.0)
    assert objective is not None
    assert objective.kind is ObjectiveKind.LIQUIDITY
    assert to_objective(ob, current_price=100.0) is None
    assert to_objective(fvg, current_price=100.0) is None
    assert to_objective(engulf, current_price=100.0) is None
    assert to_objective(sr, current_price=100.0) is None


def test_ob_fvg_sr_are_reaction_zones_but_engulfing_is_confirmation_only() -> None:
    ob = _evidence(TargetEvidenceType.ORDER_BLOCK, uid="ob", low=109.0, high=111.0)
    fvg = _evidence(TargetEvidenceType.FVG, uid="fvg", low=109.2, high=110.2)
    sr = _evidence(TargetEvidenceType.SUPPORT_RESISTANCE, uid="sr", low=109.8, high=110.4)
    engulf = _evidence(TargetEvidenceType.ENGULFING, uid="eng", low=109.5, high=110.0)

    ob_zone = to_reaction_zone(ob, current_price=100.0)
    fvg_zone = to_reaction_zone(fvg, current_price=100.0)
    assert ob_zone.kind is ReactionKind.ORDER_BLOCK
    assert ob_zone.behavior is BehaviorDirection.BEARISH
    assert fvg_zone.kind is ReactionKind.FVG
    assert fvg_zone.behavior is BehaviorDirection.BULLISH
    assert to_reaction_zone(sr, current_price=100.0).kind is ReactionKind.SUPPORT_RESISTANCE
    assert to_reaction_zone(engulf, current_price=100.0) is None
    assert semantic_roles(engulf) == (SemanticRole.CONFIRMATION,)
    assert to_confirmation(engulf, current_price=100.0).behavior is BehaviorDirection.BULLISH


def test_reaction_only_snapshot_has_no_fake_objective() -> None:
    ob = _evidence(TargetEvidenceType.ORDER_BLOCK, uid="ob", low=109.0, high=111.0)
    fvg = _evidence(TargetEvidenceType.FVG, uid="fvg", low=109.2, high=110.2)
    snapshot = build_semantic_targeting_snapshot(
        symbol="TEST",
        as_of=TS,
        current_price=100.0,
        reference_atr=4.0,
        evidence=(ob, fvg),
    )
    assert snapshot.objectives == ()
    assert len(snapshot.reaction_zones) == 2
    assert snapshot.state is ArrivalState.REACTION_ZONE_ONLY
    assert snapshot.nearest_upside_objective is None


def test_same_origin_keeps_roles_without_counting_two_independent_reaction_origins() -> None:
    liq = _evidence(TargetEvidenceType.LIQUIDITY, uid="liq", low=110.0, high=110.0, origin="event-liq")
    ob = _evidence(TargetEvidenceType.ORDER_BLOCK, uid="ob", low=109.7, high=110.4, origin="event-A")
    fvg = _evidence(
        TargetEvidenceType.FVG,
        uid="fvg",
        low=109.8,
        high=110.3,
        origin="event-A",
        roles=(TargetRole.IMBALANCE, TargetRole.SUPPLY, TargetRole.REACTION),
    )
    snapshot = build_semantic_targeting_snapshot(
        symbol="TEST",
        as_of=TS,
        current_price=100.0,
        reference_atr=4.0,
        evidence=(liq, ob, fvg),
    )
    context = snapshot.upside_arrival
    assert context is not None
    assert {item.zone.kind for item in context.reactions_at} == {ReactionKind.ORDER_BLOCK, ReactionKind.FVG}
    assert context.independent_reaction_origins == 1
    assert context.state is ArrivalState.OBJECTIVE_WITH_REACTION


def test_arrival_positions_ahead_at_and_beyond_are_separate() -> None:
    liq = _evidence(TargetEvidenceType.LIQUIDITY, uid="liq", low=110.0, high=110.0)
    ahead = _evidence(TargetEvidenceType.ORDER_BLOCK, uid="ahead", low=105.0, high=106.0)
    at = _evidence(
        TargetEvidenceType.FVG,
        uid="at",
        low=109.6,
        high=110.3,
        roles=(TargetRole.IMBALANCE, TargetRole.SUPPLY, TargetRole.REACTION),
    )
    beyond = _evidence(TargetEvidenceType.SUPPORT_RESISTANCE, uid="beyond", low=112.0, high=113.0)
    snapshot = build_semantic_targeting_snapshot(
        symbol="TEST",
        as_of=TS,
        current_price=100.0,
        reference_atr=4.0,
        evidence=(liq, ahead, at, beyond),
    )
    context = snapshot.upside_arrival
    assert context is not None
    assert [item.position for item in context.reactions_ahead] == [ArrivalPosition.AHEAD]
    assert [item.position for item in context.reactions_at] == [ArrivalPosition.AT_OBJECTIVE]
    assert [item.position for item in context.reactions_beyond] == [ArrivalPosition.BEYOND]


def test_ahead_reaction_without_arrival_zone_is_obstacle_state() -> None:
    liq = _evidence(TargetEvidenceType.LIQUIDITY, uid="liq", low=110.0, high=110.0)
    ahead = _evidence(TargetEvidenceType.ORDER_BLOCK, uid="ahead", low=105.0, high=106.0)
    snapshot = build_semantic_targeting_snapshot(
        symbol="TEST",
        as_of=TS,
        current_price=100.0,
        reference_atr=4.0,
        evidence=(liq, ahead),
    )
    assert snapshot.upside_arrival is not None
    assert snapshot.upside_arrival.state is ArrivalState.OBJECTIVE_WITH_OBSTACLE
    assert snapshot.state is ArrivalState.OBJECTIVE_WITH_OBSTACLE


def test_price_inside_ob_is_in_reaction_zone_while_liquidity_remains_objective() -> None:
    liq = _evidence(TargetEvidenceType.LIQUIDITY, uid="liq", low=110.0, high=110.0)
    ob = _evidence(TargetEvidenceType.ORDER_BLOCK, uid="ob", low=99.0, high=101.0)
    snapshot = build_semantic_targeting_snapshot(
        symbol="TEST",
        as_of=TS,
        current_price=100.0,
        reference_atr=4.0,
        evidence=(liq, ob),
    )
    assert snapshot.nearest_upside_objective is not None
    assert snapshot.state is ArrivalState.IN_REACTION_ZONE
    assert snapshot.upside_arrival is not None
    assert len(snapshot.upside_arrival.current_reactions) == 1


def test_future_available_evidence_is_filtered_inside_semantic_builder() -> None:
    known = _evidence(TargetEvidenceType.LIQUIDITY, uid="known", low=110.0, high=110.0)
    future = replace(
        _evidence(TargetEvidenceType.ORDER_BLOCK, uid="future", low=105.0, high=106.0),
        available_at=TS + pd.Timedelta(hours=1),
    )
    snapshot = build_semantic_targeting_snapshot(
        symbol="TEST",
        as_of=TS,
        current_price=100.0,
        reference_atr=4.0,
        evidence=(known, future),
    )
    assert len(snapshot.objectives) == 1
    assert snapshot.reaction_zones == ()
    assert snapshot.state is ArrivalState.OBJECTIVE_ONLY


def test_remote_engulfing_does_not_attach_to_unrelated_objective() -> None:
    liq = _evidence(TargetEvidenceType.LIQUIDITY, uid="liq", low=110.0, high=110.0)
    remote = _evidence(TargetEvidenceType.ENGULFING, uid="eng", low=120.0, high=121.0)
    snapshot = build_semantic_targeting_snapshot(
        symbol="TEST",
        as_of=TS,
        current_price=100.0,
        reference_atr=4.0,
        evidence=(liq, remote),
    )
    assert len(snapshot.confirmations) == 1
    assert snapshot.upside_arrival is not None
    assert snapshot.upside_arrival.confirmations == ()
