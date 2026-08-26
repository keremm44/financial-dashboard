from __future__ import annotations

import pandas as pd

from financial_dashboard.decision.incremental_targeting import (
    build_targeting_from_deduped_evidence,
    deduplicate_origin_events_indexed,
)
from financial_dashboard.targeting.clustering import (
    build_targeting_snapshot,
    deduplicate_origin_events,
)
from financial_dashboard.targeting.models import (
    TargetEvidence,
    TargetEvidenceFamily,
    TargetEvidenceType,
    TargetRole,
)


def _evidence(
    uid: str,
    *,
    timeframe: str,
    evidence_type: TargetEvidenceType,
    origin_index: int,
    low: float,
    high: float,
    minute: int,
) -> TargetEvidence:
    timestamp = pd.Timestamp("2026-01-01 10:00", tz="Europe/Istanbul") + pd.Timedelta(minutes=minute)
    family = {
        TargetEvidenceType.LIQUIDITY: TargetEvidenceFamily.STRUCTURAL,
        TargetEvidenceType.SUPPORT_RESISTANCE: TargetEvidenceFamily.STRUCTURAL,
        TargetEvidenceType.ORDER_BLOCK: TargetEvidenceFamily.SUPPLY_DEMAND,
        TargetEvidenceType.FVG: TargetEvidenceFamily.IMBALANCE,
        TargetEvidenceType.ENGULFING: TargetEvidenceFamily.REACTION,
    }[evidence_type]
    role = {
        TargetEvidenceType.LIQUIDITY: TargetRole.MAGNET,
        TargetEvidenceType.SUPPORT_RESISTANCE: TargetRole.DEMAND,
        TargetEvidenceType.ORDER_BLOCK: TargetRole.DEMAND,
        TargetEvidenceType.FVG: TargetRole.IMBALANCE,
        TargetEvidenceType.ENGULFING: TargetRole.REACTION,
    }[evidence_type]
    return TargetEvidence(
        uid=uid,
        symbol="TEST",
        timeframe=timeframe,
        evidence_type=evidence_type,
        family=family,
        roles=(role,),
        low=low,
        high=high,
        anchor_price=(low + high) / 2.0,
        origin_index=origin_index,
        origin_time=timestamp,
        confirmed_at=timestamp,
        available_at=timestamp,
        source_state="ACTIVE",
        target_eligible=True,
        native_origin_id=f"NATIVE:{uid}",
        origin_event_id=f"ORIGIN:{uid}",
        source_identity=f"SOURCE:{uid}",
    )


def _rows() -> tuple[TargetEvidence, ...]:
    return (
        _evidence("ob1", timeframe="1h", evidence_type=TargetEvidenceType.ORDER_BLOCK, origin_index=10, low=100.0, high=100.2, minute=1),
        _evidence("fvg1", timeframe="1h", evidence_type=TargetEvidenceType.FVG, origin_index=11, low=100.1, high=100.3, minute=2),
        _evidence("eng1", timeframe="1h", evidence_type=TargetEvidenceType.ENGULFING, origin_index=12, low=100.2, high=100.4, minute=3),
        _evidence("liq1", timeframe="1h", evidence_type=TargetEvidenceType.LIQUIDITY, origin_index=20, low=110.0, high=110.1, minute=4),
        _evidence("sr1", timeframe="1h", evidence_type=TargetEvidenceType.SUPPORT_RESISTANCE, origin_index=21, low=110.05, high=110.2, minute=5),
        _evidence("far", timeframe="1h", evidence_type=TargetEvidenceType.FVG, origin_index=50, low=140.0, high=141.0, minute=6),
        _evidence("other_tf", timeframe="30m", evidence_type=TargetEvidenceType.ORDER_BLOCK, origin_index=11, low=100.1, high=100.2, minute=2),
    )


def test_indexed_dedup_matches_canonical_for_mixed_origin_families() -> None:
    rows = _rows()
    canonical = deduplicate_origin_events(rows, reference_atr=2.0)
    indexed = deduplicate_origin_events_indexed(rows, reference_atr=2.0)
    assert indexed == canonical


def test_indexed_dedup_preserves_greedy_order_when_confirmations_are_not_origin_sorted() -> None:
    rows = (
        _evidence("late-origin", timeframe="1h", evidence_type=TargetEvidenceType.ORDER_BLOCK, origin_index=12, low=100.0, high=100.2, minute=1),
        _evidence("early-origin", timeframe="1h", evidence_type=TargetEvidenceType.FVG, origin_index=10, low=100.1, high=100.3, minute=2),
        _evidence("middle-origin", timeframe="1h", evidence_type=TargetEvidenceType.ENGULFING, origin_index=11, low=100.15, high=100.25, minute=3),
    )
    assert deduplicate_origin_events_indexed(rows, reference_atr=1.0) == deduplicate_origin_events(
        rows,
        reference_atr=1.0,
    )


def test_targeting_from_pre_deduped_evidence_matches_canonical_double_dedup_path() -> None:
    rows = _rows()
    as_of = pd.Timestamp("2026-01-01 12:00", tz="Europe/Istanbul")
    deduped = deduplicate_origin_events_indexed(rows, reference_atr=2.0)

    expected = build_targeting_snapshot(
        symbol="TEST",
        as_of=as_of,
        current_price=105.0,
        reference_timeframe="1h",
        reference_atr=2.0,
        evidence=deduped,
    )
    actual = build_targeting_from_deduped_evidence(
        symbol="TEST",
        as_of=as_of,
        current_price=105.0,
        reference_timeframe="1h",
        reference_atr=2.0,
        evidence=deduped,
    )

    assert actual == expected
