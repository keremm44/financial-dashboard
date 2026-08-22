from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from financial_dashboard.targeting.arrival import build_semantic_targeting_snapshot
from financial_dashboard.targeting.models import (
    LiquidityScope,
    TargetEvidence,
    TargetEvidenceFamily,
    TargetEvidenceType,
    TargetRole,
)
from financial_dashboard.targeting_replay_diagnostics import (
    SemanticReplayTransitionKind,
    liquidity_scope_diagnostics,
    semantic_replay_transition_ledger,
)


TS = pd.Timestamp("2026-08-21 14:00", tz="Europe/Istanbul")


def _liq(uid: str, level: float, *, tf: str = "1h", scope: LiquidityScope = LiquidityScope.UNCLASSIFIED) -> TargetEvidence:
    return TargetEvidence(
        uid=uid,
        symbol="TEST",
        timeframe=tf,
        evidence_type=TargetEvidenceType.LIQUIDITY,
        family=TargetEvidenceFamily.STRUCTURAL,
        roles=(TargetRole.MAGNET,),
        low=level,
        high=level,
        anchor_price=level,
        origin_index=1,
        origin_time=TS,
        confirmed_at=TS,
        available_at=TS,
        source_state="ACTIVE",
        target_eligible=True,
        native_origin_id=uid,
        origin_event_id=uid,
        source_identity=uid,
        liquidity_scope=scope,
    )


def _replay(*snapshots):
    points = tuple(
        SimpleNamespace(available_at=TS + pd.Timedelta(hours=index), semantic_snapshot=snapshot)
        for index, snapshot in enumerate(snapshots)
    )
    return SimpleNamespace(points=points)


def test_semantic_transition_tracks_objective_replacement_and_state_change() -> None:
    first = build_semantic_targeting_snapshot(
        symbol="TEST", as_of=TS, current_price=100.0, reference_atr=4.0,
        evidence=(_liq("up-a", 110.0),),
    )
    second = build_semantic_targeting_snapshot(
        symbol="TEST", as_of=TS, current_price=111.0, reference_atr=4.0,
        evidence=(_liq("up-a", 110.0), _liq("up-b", 120.0)),
    )
    ledger = semantic_replay_transition_ledger(_replay(first, second))
    kinds = {(item.side, item.kind) for item in ledger}
    assert ("upside", SemanticReplayTransitionKind.OBJECTIVE_REPLACED) in kinds
    assert ("upside", SemanticReplayTransitionKind.ARRIVAL_STATE_CHANGED) in kinds


def test_scope_diagnostics_are_timeframe_specific_and_observation_based() -> None:
    first = build_semantic_targeting_snapshot(
        symbol="TEST", as_of=TS, current_price=100.0, reference_atr=4.0,
        evidence=(
            _liq("i", 110.0, tf="1h", scope=LiquidityScope.INTERNAL),
            _liq("e", 120.0, tf="4h", scope=LiquidityScope.EXTERNAL),
        ),
    )
    second = build_semantic_targeting_snapshot(
        symbol="TEST", as_of=TS, current_price=101.0, reference_atr=4.0,
        evidence=(
            _liq("i", 110.0, tf="1h", scope=LiquidityScope.INTERNAL),
            _liq("u", 90.0, tf="1h", scope=LiquidityScope.UNCLASSIFIED),
            _liq("e", 120.0, tf="4h", scope=LiquidityScope.EXTERNAL),
        ),
    )
    rows = {row.timeframe: row for row in liquidity_scope_diagnostics(_replay(first, second))}
    assert rows["4h"].external_pct == 100.0
    assert rows["4h"].unique_objectives == 1
    assert rows["1h"].observations == 3
    assert rows["1h"].internal == 2
    assert rows["1h"].unclassified == 1
    assert round(rows["1h"].unclassified_pct, 1) == 33.3
