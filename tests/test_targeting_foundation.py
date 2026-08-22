from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.engines.liquidity_models import LiquidityConfig
from financial_dashboard.target_evidence_replay import LiquidityMTFReplayRunner, OrderBlockMTFReplayRunner
from financial_dashboard.targeting.clustering import (
    TargetClusterConfig,
    build_targeting_snapshot,
    deduplicate_origin_events,
)
from financial_dashboard.targeting.models import (
    LiquidityScope,
    TargetClusterKind,
    TargetEvidence,
    TargetEvidenceFamily,
    TargetEvidenceType,
    TargetRole,
    TargetSide,
)


TZ = "Europe/Istanbul"
TARGET_AS_OF = pd.Timestamp("2026-08-20 13:00", tz=TZ)


def _evidence(
    uid: str,
    *,
    low: float,
    high: float | None = None,
    kind: TargetEvidenceType = TargetEvidenceType.LIQUIDITY,
    family: TargetEvidenceFamily = TargetEvidenceFamily.STRUCTURAL,
    roles: tuple[TargetRole, ...] = (TargetRole.MAGNET,),
    origin_index: int = 1,
    timeframe: str = "1h",
    origin_event_id: str | None = None,
    scope: LiquidityScope | None = LiquidityScope.UNCLASSIFIED,
) -> TargetEvidence:
    high = low if high is None else high
    stamp = pd.Timestamp("2026-08-20 10:00", tz=TZ)
    return TargetEvidence(
        uid=uid,
        symbol="TEST",
        timeframe=timeframe,
        evidence_type=kind,
        family=family,
        roles=roles,
        low=low,
        high=high,
        anchor_price=(low + high) / 2.0,
        origin_index=origin_index,
        origin_time=stamp,
        confirmed_at=stamp + pd.Timedelta(hours=1),
        available_at=stamp + pd.Timedelta(hours=2),
        source_state="ACTIVE",
        target_eligible=True,
        native_origin_id=f"native:{uid}",
        origin_event_id=origin_event_id or f"event:{uid}",
        source_identity=f"source:{uid}",
        formation_atr=1.0,
        liquidity_scope=scope if kind is TargetEvidenceType.LIQUIDITY else None,
    )


def test_cluster_uses_nearest_edge_distance_and_liquidity_anchor() -> None:
    liquidity = _evidence("liq", low=104.80)
    fvg = _evidence(
        "fvg",
        low=104.90,
        high=105.20,
        kind=TargetEvidenceType.FVG,
        family=TargetEvidenceFamily.IMBALANCE,
        roles=(TargetRole.IMBALANCE,),
        origin_index=2,
    )
    ob = _evidence(
        "ob",
        low=105.00,
        high=105.40,
        kind=TargetEvidenceType.ORDER_BLOCK,
        family=TargetEvidenceFamily.SUPPLY_DEMAND,
        roles=(TargetRole.SUPPLY, TargetRole.REACTION),
        origin_index=3,
    )
    snapshot = build_targeting_snapshot(
        symbol="TEST",
        as_of=TARGET_AS_OF,
        current_price=100.0,
        reference_timeframe="1h",
        reference_atr=2.0,
        evidence=(liquidity, fvg, ob),
        config=TargetClusterConfig(evidence_gap_atr=0.25, max_span_atr=0.50),
    )
    target = snapshot.nearest_upside_target
    assert target is not None
    assert target.side is TargetSide.ABOVE
    assert target.kind is TargetClusterKind.LIQUIDITY_TARGET
    assert target.envelope_low == pytest.approx(104.80)
    assert target.envelope_high == pytest.approx(105.40)
    assert target.liquidity_anchor == pytest.approx(104.80)
    assert target.distance_price == pytest.approx(4.80)
    assert target.distance_atr == pytest.approx(2.40)


def test_max_span_prevents_single_linkage_chaining() -> None:
    evidence = (
        _evidence("a", low=105.0),
        _evidence("b", low=105.8),
        _evidence("c", low=106.6),
    )
    snapshot = build_targeting_snapshot(
        symbol="TEST",
        as_of=TARGET_AS_OF,
        current_price=100.0,
        reference_timeframe="1h",
        reference_atr=1.0,
        evidence=evidence,
        config=TargetClusterConfig(evidence_gap_atr=1.0, max_span_atr=1.0),
    )
    upside = [cluster for cluster in snapshot.clusters if cluster.side is TargetSide.ABOVE]
    assert len(upside) == 2
    assert max(cluster.envelope_high - cluster.envelope_low for cluster in upside) <= 1.0


def test_same_origin_fvg_ob_engulfing_do_not_count_as_three_independent_events() -> None:
    common = {
        "timeframe": "1h",
        "origin_index": 10,
    }
    fvg = _evidence(
        "fvg",
        low=105.0,
        high=105.2,
        kind=TargetEvidenceType.FVG,
        family=TargetEvidenceFamily.IMBALANCE,
        roles=(TargetRole.IMBALANCE,),
        **common,
    )
    ob = _evidence(
        "ob",
        low=105.05,
        high=105.25,
        kind=TargetEvidenceType.ORDER_BLOCK,
        family=TargetEvidenceFamily.SUPPLY_DEMAND,
        roles=(TargetRole.SUPPLY,),
        **common,
    )
    engulf = _evidence(
        "engulf",
        low=105.02,
        high=105.22,
        kind=TargetEvidenceType.ENGULFING,
        family=TargetEvidenceFamily.REACTION,
        roles=(TargetRole.REACTION,),
        **common,
    )
    deduped = deduplicate_origin_events(
        (fvg, ob, engulf),
        reference_atr=1.0,
        config=TargetClusterConfig(origin_bar_tolerance=2, origin_price_tolerance_atr=0.25),
    )
    assert len({item.origin_event_id for item in deduped}) == 1
    assert len({item.uid for item in deduped}) == 3


def test_technical_cluster_without_liquidity_is_not_promoted_to_liquidity_target() -> None:
    fvg = _evidence(
        "fvg",
        low=102.0,
        high=102.2,
        kind=TargetEvidenceType.FVG,
        family=TargetEvidenceFamily.IMBALANCE,
        roles=(TargetRole.IMBALANCE,),
    )
    snapshot = build_targeting_snapshot(
        symbol="TEST",
        as_of=TARGET_AS_OF,
        current_price=100.0,
        reference_timeframe="1h",
        reference_atr=1.0,
        evidence=(fvg,),
    )
    assert len(snapshot.clusters) == 1
    assert snapshot.clusters[0].kind is TargetClusterKind.TECHNICAL_ZONE
    assert snapshot.nearest_upside_target is None


def test_internal_and_external_liquidity_resolvers_remain_separate() -> None:
    internal = _evidence("internal", low=102.0, scope=LiquidityScope.INTERNAL)
    external = _evidence("external", low=104.0, scope=LiquidityScope.EXTERNAL)
    snapshot = build_targeting_snapshot(
        symbol="TEST",
        as_of=TARGET_AS_OF,
        current_price=100.0,
        reference_timeframe="1h",
        reference_atr=1.0,
        evidence=(internal, external),
    )
    assert snapshot.nearest_internal_upside_liquidity == internal
    assert snapshot.nearest_external_upside_liquidity == external


def test_future_available_evidence_is_not_visible_in_current_target_snapshot() -> None:
    known = _evidence("known", low=102.0)
    future = replace(
        _evidence("future", low=101.0),
        available_at=TARGET_AS_OF + pd.Timedelta(hours=1),
    )
    snapshot = build_targeting_snapshot(
        symbol="TEST",
        as_of=TARGET_AS_OF,
        current_price=100.0,
        reference_timeframe="1h",
        reference_atr=1.0,
        evidence=(known, future),
    )
    assert snapshot.nearest_upside_target is not None
    assert snapshot.nearest_upside_target.liquidity_anchor == pytest.approx(102.0)
    assert all(item.uid != "future" for cluster in snapshot.clusters for item in cluster.evidence)


def _save(store: ParquetOHLCVStore, rows: list[dict], timeframe: str = "1h") -> None:
    frame = pd.DataFrame(rows)
    store.merge_and_save(frame, symbol="TEST", timeframe=timeframe, source="test")


def _bar(i: int, o: float, h: float, l: float, c: float) -> dict:
    return {
        "timestamp": pd.Timestamp("2026-08-20 10:00", tz=TZ) + pd.Timedelta(hours=i),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1000.0,
        "is_closed": True,
        "is_complete": True,
    }


def test_liquidity_evidence_confirmation_is_later_than_pivot_origin(tmp_path) -> None:
    store = ParquetOHLCVStore(tmp_path)
    _save(
        store,
        [
            _bar(0, 98.0, 99.0, 97.0, 98.5),
            _bar(1, 98.5, 100.0, 98.0, 99.0),
            _bar(2, 99.0, 99.0, 97.5, 98.5),
            _bar(3, 98.5, 100.0, 98.0, 99.0),
            _bar(4, 99.0, 99.0, 97.5, 98.5),
        ],
    )
    replay = LiquidityMTFReplayRunner(
        store,
        config=LiquidityConfig(pivot_span=1, atr_length=3, atr_tolerance=0.20),
    ).replay("TEST", timeframes=("1h",))
    active = [item for item in replay.evidence if item.target_eligible]
    assert active
    item = active[0]
    assert pd.Timestamp(item.origin_time) == pd.Timestamp("2026-08-20 11:00", tz=TZ)
    assert pd.Timestamp(item.confirmed_at) == pd.Timestamp("2026-08-20 14:00", tz=TZ)
    assert pd.Timestamp(item.available_at) == pd.Timestamp("2026-08-20 15:00", tz=TZ)


def test_order_block_evidence_waits_for_imbalance_confirmation(tmp_path) -> None:
    store = ParquetOHLCVStore(tmp_path)
    _save(
        store,
        [
            _bar(0, 102.0, 103.0, 98.0, 99.0),
            _bar(1, 99.0, 104.0, 98.5, 103.0),
            _bar(2, 104.0, 106.0, 103.02, 105.0),
        ],
    )
    replay = OrderBlockMTFReplayRunner(store).replay("TEST", timeframes=("1h",))
    active = [item for item in replay.evidence if item.target_eligible]
    assert active
    item = active[0]
    assert pd.Timestamp(item.origin_time) == pd.Timestamp("2026-08-20 10:00", tz=TZ)
    assert pd.Timestamp(item.confirmed_at) == pd.Timestamp("2026-08-20 12:00", tz=TZ)
    assert pd.Timestamp(item.available_at) == pd.Timestamp("2026-08-20 13:00", tz=TZ)
