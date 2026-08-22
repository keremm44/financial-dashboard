from __future__ import annotations

import pandas as pd

from financial_dashboard.targeting.arrival import build_semantic_targeting_snapshot
from financial_dashboard.targeting.models import TargetEvidence, TargetEvidenceFamily, TargetEvidenceType, TargetRole
from financial_dashboard.targeting.semantic_models import ArrivalConflict, ArrivalState


TS = pd.Timestamp("2026-08-21 14:00", tz="Europe/Istanbul")


def _e(uid: str, typ: TargetEvidenceType, low: float, high: float, roles: tuple[TargetRole, ...]) -> TargetEvidence:
    family = {
        TargetEvidenceType.LIQUIDITY: TargetEvidenceFamily.STRUCTURAL,
        TargetEvidenceType.ORDER_BLOCK: TargetEvidenceFamily.SUPPLY_DEMAND,
        TargetEvidenceType.FVG: TargetEvidenceFamily.IMBALANCE,
        TargetEvidenceType.SUPPORT_RESISTANCE: TargetEvidenceFamily.STRUCTURAL,
    }[typ]
    return TargetEvidence(
        uid=uid, symbol="TEST", timeframe="1h", evidence_type=typ, family=family,
        roles=roles, low=low, high=high, anchor_price=(low + high) / 2,
        origin_index=1, origin_time=TS, confirmed_at=TS, available_at=TS,
        source_state="ACTIVE", target_eligible=True, native_origin_id=uid,
        origin_event_id=uid, source_identity=uid,
    )


def test_ahead_conflict_is_path_fact_not_conflicting_arrival() -> None:
    liq = _e("liq", TargetEvidenceType.LIQUIDITY, 110, 110, (TargetRole.MAGNET,))
    bearish = _e("bear", TargetEvidenceType.ORDER_BLOCK, 104, 105, (TargetRole.SUPPLY, TargetRole.REACTION))
    bullish = _e("bull", TargetEvidenceType.FVG, 106, 107, (TargetRole.IMBALANCE, TargetRole.DEMAND, TargetRole.REACTION))
    snap = build_semantic_targeting_snapshot(symbol="TEST", as_of=TS, current_price=100, reference_atr=4, evidence=(liq, bearish, bullish))
    ctx = snap.upside_arrival
    assert ctx is not None
    assert ctx.conflicts == (ArrivalConflict.AHEAD,)
    assert ctx.state is ArrivalState.OBJECTIVE_WITH_OBSTACLE


def test_at_objective_conflict_is_conflicting_arrival() -> None:
    liq = _e("liq", TargetEvidenceType.LIQUIDITY, 110, 110, (TargetRole.MAGNET,))
    bearish = _e("bear", TargetEvidenceType.ORDER_BLOCK, 109.6, 110.2, (TargetRole.SUPPLY, TargetRole.REACTION))
    bullish = _e("bull", TargetEvidenceType.FVG, 109.8, 110.4, (TargetRole.IMBALANCE, TargetRole.DEMAND, TargetRole.REACTION))
    snap = build_semantic_targeting_snapshot(symbol="TEST", as_of=TS, current_price=100, reference_atr=4, evidence=(liq, bearish, bullish))
    ctx = snap.upside_arrival
    assert ctx is not None
    assert ctx.conflicts == (ArrivalConflict.AT_OBJECTIVE,)
    assert ctx.state is ArrivalState.CONFLICTING_ARRIVAL


def test_current_conflict_keeps_in_reaction_zone_state() -> None:
    liq = _e("liq", TargetEvidenceType.LIQUIDITY, 110, 110, (TargetRole.MAGNET,))
    bearish = _e("bear", TargetEvidenceType.ORDER_BLOCK, 99, 101, (TargetRole.SUPPLY, TargetRole.REACTION))
    bullish = _e("bull", TargetEvidenceType.FVG, 99.5, 100.5, (TargetRole.IMBALANCE, TargetRole.DEMAND, TargetRole.REACTION))
    snap = build_semantic_targeting_snapshot(symbol="TEST", as_of=TS, current_price=100, reference_atr=4, evidence=(liq, bearish, bullish))
    ctx = snap.upside_arrival
    assert ctx is not None
    assert ctx.conflicts == (ArrivalConflict.CURRENT,)
    assert ctx.state is ArrivalState.IN_REACTION_ZONE
