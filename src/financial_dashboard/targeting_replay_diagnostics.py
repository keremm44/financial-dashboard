from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .targeting.models import TargetCluster
from .targeting_historical_replay import TargetingHistoricalReplay, TargetingReplayPoint


class SemanticTransitionKind(StrEnum):
    NEW = "NEW"
    DISAPPEARED = "DISAPPEARED"
    REPLACED = "REPLACED"
    EXPANDED = "EXPANDED"
    NARROWED = "NARROWED"
    ENRICHED = "ENRICHED"


@dataclass(frozen=True, slots=True)
class SemanticTargetTransition:
    available_at: Any
    field: str
    kind: SemanticTransitionKind
    previous_identity: str | None
    new_identity: str | None
    previous_envelope: tuple[float, float] | None
    new_envelope: tuple[float, float] | None
    previous_distance_atr: float | None
    new_distance_atr: float | None


@dataclass(frozen=True, slots=True)
class ClusterStability:
    first_seen_at: Any
    last_seen_at: Any
    consecutive_snapshots: int
    age_reference_bars: int


_TARGET_FIELDS = (
    "nearest_upside_target",
    "nearest_downside_target",
    "highest_confluence_upside",
    "highest_confluence_downside",
)


def _overlaps(left: TargetCluster, right: TargetCluster) -> bool:
    return not (
        left.envelope_high < right.envelope_low
        or right.envelope_high < left.envelope_low
    )


def _same_region(left: TargetCluster, right: TargetCluster) -> bool:
    return (
        left.side is right.side
        and left.kind is right.kind
        and _overlaps(left, right)
    )


def _transition_kind(
    previous: TargetCluster | None,
    current: TargetCluster | None,
) -> SemanticTransitionKind | None:
    if previous is None and current is None:
        return None
    if previous is None:
        return SemanticTransitionKind.NEW
    if current is None:
        return SemanticTransitionKind.DISAPPEARED
    if previous.identity == current.identity:
        return None
    if not _same_region(previous, current):
        return SemanticTransitionKind.REPLACED

    previous_contains_current = (
        previous.envelope_low <= current.envelope_low
        and previous.envelope_high >= current.envelope_high
    )
    current_contains_previous = (
        current.envelope_low <= previous.envelope_low
        and current.envelope_high >= previous.envelope_high
    )
    bounds_changed = (
        previous.envelope_low != current.envelope_low
        or previous.envelope_high != current.envelope_high
    )
    if bounds_changed and current_contains_previous and not previous_contains_current:
        return SemanticTransitionKind.EXPANDED
    if bounds_changed and previous_contains_current and not current_contains_previous:
        return SemanticTransitionKind.NARROWED
    return SemanticTransitionKind.ENRICHED


def semantic_transition_ledger(
    replay: TargetingHistoricalReplay,
) -> tuple[SemanticTargetTransition, ...]:
    if len(replay.points) < 2:
        return ()
    out: list[SemanticTargetTransition] = []
    previous_point = replay.points[0]
    for point in replay.points[1:]:
        for field in _TARGET_FIELDS:
            previous = getattr(previous_point.snapshot, field)
            current = getattr(point.snapshot, field)
            kind = _transition_kind(previous, current)
            if kind is None:
                continue
            out.append(
                SemanticTargetTransition(
                    available_at=point.available_at,
                    field=field,
                    kind=kind,
                    previous_identity=None if previous is None else previous.identity,
                    new_identity=None if current is None else current.identity,
                    previous_envelope=(
                        None
                        if previous is None
                        else (previous.envelope_low, previous.envelope_high)
                    ),
                    new_envelope=(
                        None
                        if current is None
                        else (current.envelope_low, current.envelope_high)
                    ),
                    previous_distance_atr=(
                        None if previous is None else float(previous.distance_atr)
                    ),
                    new_distance_atr=(
                        None if current is None else float(current.distance_atr)
                    ),
                )
            )
        previous_point = point
    return tuple(out)


def _intersection_width(left: TargetCluster, right: TargetCluster) -> float:
    return max(
        0.0,
        min(left.envelope_high, right.envelope_high)
        - max(left.envelope_low, right.envelope_low),
    )


def _best_lineage_match(
    target: TargetCluster,
    point: TargetingReplayPoint,
) -> TargetCluster | None:
    candidates = [
        cluster
        for cluster in point.snapshot.clusters
        if _same_region(target, cluster)
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda cluster: (
            _intersection_width(target, cluster),
            cluster.independent_origin_count,
            cluster.independent_family_count,
        ),
    )


def cluster_stability(
    replay: TargetingHistoricalReplay,
    *,
    point_index: int,
    cluster: TargetCluster,
) -> ClusterStability:
    if point_index < 0 or point_index >= len(replay.points):
        raise IndexError("point_index outside replay")

    current_point = replay.points[point_index]
    first_point = current_point
    lineage_cluster = cluster
    consecutive = 1
    for previous_index in range(point_index - 1, -1, -1):
        previous_point = replay.points[previous_index]
        match = _best_lineage_match(lineage_cluster, previous_point)
        if match is None:
            break
        first_point = previous_point
        lineage_cluster = match
        consecutive += 1

    return ClusterStability(
        first_seen_at=first_point.available_at,
        last_seen_at=current_point.available_at,
        consecutive_snapshots=consecutive,
        age_reference_bars=(
            int(current_point.reference_index) - int(first_point.reference_index) + 1
        ),
    )


__all__ = [
    "ClusterStability",
    "SemanticTargetTransition",
    "SemanticTransitionKind",
    "cluster_stability",
    "semantic_transition_ledger",
]
