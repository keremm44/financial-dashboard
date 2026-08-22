from __future__ import annotations

import pandas as pd

from financial_dashboard.targeting.models import (
    TargetCluster,
    TargetClusterKind,
    TargetClusterQuality,
    TargetEvidence,
    TargetEvidenceFamily,
    TargetEvidenceType,
    TargetRole,
    TargetSide,
    TargetingSnapshot,
)
from financial_dashboard.targeting_historical_replay import (
    TargetingHistoricalReplay,
    TargetingReplayPoint,
)
from financial_dashboard.targeting_replay_diagnostics import (
    SemanticTransitionKind,
    cluster_stability,
    semantic_transition_ledger,
)
from financial_dashboard.ui.targeting_replay_view_models import (
    cluster_anatomy_frame,
    distance_bands_frame,
    semantic_transitions_frame,
)


TZ = "Europe/Istanbul"


def _evidence(uid: str, level: float) -> TargetEvidence:
    timestamp = pd.Timestamp("2026-08-21 10:00", tz=TZ)
    return TargetEvidence(
        uid=uid,
        symbol="TEST",
        timeframe="1h",
        evidence_type=TargetEvidenceType.LIQUIDITY,
        family=TargetEvidenceFamily.STRUCTURAL,
        roles=(TargetRole.MAGNET,),
        low=level,
        high=level,
        anchor_price=level,
        origin_index=1,
        origin_time=timestamp,
        confirmed_at=timestamp,
        available_at=timestamp,
        source_state="ACTIVE",
        target_eligible=True,
        native_origin_id=f"native:{uid}",
        origin_event_id=f"event:{uid}",
        source_identity=f"source:{uid}",
    )


def _cluster(identity: str, low: float, high: float, *, distance: float) -> TargetCluster:
    evidence = (_evidence(f"E-{identity}", (low + high) / 2.0),)
    return TargetCluster(
        identity=identity,
        side=TargetSide.ABOVE,
        kind=TargetClusterKind.LIQUIDITY_TARGET,
        envelope_low=low,
        envelope_high=high,
        core_low=low,
        core_high=high,
        liquidity_anchor=(low + high) / 2.0,
        distance_price=max(low - 100.0, 0.0),
        distance_percent=max(low - 100.0, 0.0),
        distance_atr=distance,
        evidence=evidence,
        raw_source_count=1,
        independent_origin_count=1,
        independent_family_count=1,
        timeframes_present=("1h",),
        roles_present=(TargetRole.MAGNET,),
        quality=TargetClusterQuality.SINGLE,
    )


def _snapshot(timestamp: str, cluster: TargetCluster | None) -> TargetingSnapshot:
    as_of = pd.Timestamp(timestamp, tz=TZ)
    clusters = () if cluster is None else (cluster,)
    return TargetingSnapshot(
        symbol="TEST",
        as_of=as_of,
        current_price=100.0,
        reference_timeframe="1h",
        reference_atr=4.0,
        clusters=clusters,
        nearest_upside_target=cluster,
        nearest_downside_target=None,
        highest_confluence_upside=cluster,
        highest_confluence_downside=None,
    )


def _point(index: int, timestamp: str, cluster: TargetCluster | None) -> TargetingReplayPoint:
    snapshot = _snapshot(timestamp, cluster)
    return TargetingReplayPoint(
        reference_index=index,
        reference_timestamp=snapshot.as_of,
        available_at=snapshot.as_of,
        snapshot=snapshot,
    )


def _replay(points: tuple[TargetingReplayPoint, ...]) -> TargetingHistoricalReplay:
    return TargetingHistoricalReplay(
        symbol="TEST",
        timeframes=("1h",),
        reference_timeframe="1h",
        points=points,
        transitions=(),
    )


def test_semantic_transition_marks_overlapping_region_expansion_not_replacement() -> None:
    first = _cluster("A", 407.5, 408.125, distance=0.6)
    expanded = _cluster("B", 407.4167, 408.125, distance=0.25)
    replay = _replay(
        (
            _point(10, "2026-08-21 16:00", first),
            _point(11, "2026-08-21 17:00", expanded),
        )
    )

    transitions = semantic_transition_ledger(replay)
    nearest = [item for item in transitions if item.field == "nearest_upside_target"]
    assert len(nearest) == 1
    assert nearest[0].kind is SemanticTransitionKind.EXPANDED
    assert nearest[0].previous_identity == "A"
    assert nearest[0].new_identity == "B"


def test_semantic_transition_marks_disjoint_region_as_replaced() -> None:
    first = _cluster("A", 407.5, 408.125, distance=0.6)
    replacement = _cluster("B", 412.0, 413.0, distance=3.0)
    replay = _replay(
        (
            _point(10, "2026-08-21 16:00", first),
            _point(11, "2026-08-21 17:00", replacement),
        )
    )

    transitions = semantic_transition_ledger(replay)
    nearest = [item for item in transitions if item.field == "nearest_upside_target"]
    assert len(nearest) == 1
    assert nearest[0].kind is SemanticTransitionKind.REPLACED


def test_cluster_stability_follows_overlapping_lineage_across_identity_changes() -> None:
    replay = _replay(
        (
            _point(20, "2026-08-21 14:00", _cluster("A", 408.125, 408.125, distance=1.3)),
            _point(21, "2026-08-21 15:00", _cluster("A", 408.125, 408.125, distance=0.5)),
            _point(22, "2026-08-21 16:00", _cluster("B", 407.5, 408.125, distance=0.6)),
            _point(23, "2026-08-21 17:00", _cluster("C", 407.4167, 408.125, distance=0.25)),
        )
    )
    stability = cluster_stability(
        replay,
        point_index=3,
        cluster=replay.points[3].snapshot.nearest_upside_target,
    )

    assert stability.consecutive_snapshots == 4
    assert stability.age_reference_bars == 4
    assert pd.Timestamp(stability.first_seen_at) == pd.Timestamp("2026-08-21 14:00", tz=TZ)


def test_replay_view_models_expose_distance_bands_anatomy_and_semantic_kind() -> None:
    first = _cluster("A", 407.5, 408.125, distance=0.4)
    expanded = _cluster("B", 407.4167, 408.125, distance=0.3)
    replay = _replay(
        (
            _point(10, "2026-08-21 16:00", first),
            _point(11, "2026-08-21 17:00", expanded),
        )
    )

    bands = distance_bands_frame(replay.points[-1].snapshot.clusters)
    assert int(bands.loc[bands["Distance band"] == "0–0.5 ATR", "All"].iloc[0]) == 1
    anatomy = cluster_anatomy_frame(expanded)
    assert set(anatomy["Type"]) == {"LIQUIDITY"}
    semantic = semantic_transitions_frame(replay)
    assert "EXPANDED" in set(semantic["Transition"])
