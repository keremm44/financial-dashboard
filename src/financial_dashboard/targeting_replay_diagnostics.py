from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .targeting.models import LiquidityScope, TargetCluster
from .targeting_historical_replay import TargetingHistoricalReplay, TargetingReplayPoint


class SemanticTransitionKind(StrEnum):
    NEW = "NEW"
    DISAPPEARED = "DISAPPEARED"
    REPLACED = "REPLACED"
    EXPANDED = "EXPANDED"
    NARROWED = "NARROWED"
    ENRICHED = "ENRICHED"


class SemanticReplayTransitionKind(StrEnum):
    OBJECTIVE_NEW = "OBJECTIVE_NEW"
    OBJECTIVE_DISAPPEARED = "OBJECTIVE_DISAPPEARED"
    OBJECTIVE_REPLACED = "OBJECTIVE_REPLACED"
    ARRIVAL_STATE_CHANGED = "ARRIVAL_STATE_CHANGED"
    CURRENT_REACTION_ENTERED = "CURRENT_REACTION_ENTERED"
    CURRENT_REACTION_EXITED = "CURRENT_REACTION_EXITED"
    AHEAD_REACTION_APPEARED = "AHEAD_REACTION_APPEARED"
    AHEAD_REACTION_DISAPPEARED = "AHEAD_REACTION_DISAPPEARED"
    AT_REACTION_APPEARED = "AT_REACTION_APPEARED"
    AT_REACTION_DISAPPEARED = "AT_REACTION_DISAPPEARED"


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
class SemanticReplayTransition:
    available_at: Any
    side: str
    kind: SemanticReplayTransitionKind
    previous: str | None
    current: str | None


@dataclass(frozen=True, slots=True)
class LiquidityScopeDiagnostic:
    timeframe: str
    observations: int
    unique_objectives: int
    internal: int
    external: int
    unclassified: int
    internal_pct: float
    external_pct: float
    unclassified_pct: float


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
    return not (left.envelope_high < right.envelope_low or right.envelope_high < left.envelope_low)


def _same_region(left: TargetCluster, right: TargetCluster) -> bool:
    return left.side is right.side and left.kind is right.kind and _overlaps(left, right)


def _transition_kind(previous: TargetCluster | None, current: TargetCluster | None) -> SemanticTransitionKind | None:
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
    previous_contains_current = previous.envelope_low <= current.envelope_low and previous.envelope_high >= current.envelope_high
    current_contains_previous = current.envelope_low <= previous.envelope_low and current.envelope_high >= previous.envelope_high
    bounds_changed = previous.envelope_low != current.envelope_low or previous.envelope_high != current.envelope_high
    if bounds_changed and current_contains_previous and not previous_contains_current:
        return SemanticTransitionKind.EXPANDED
    if bounds_changed and previous_contains_current and not current_contains_previous:
        return SemanticTransitionKind.NARROWED
    return SemanticTransitionKind.ENRICHED


def semantic_transition_ledger(replay: TargetingHistoricalReplay) -> tuple[SemanticTargetTransition, ...]:
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
            out.append(SemanticTargetTransition(
                available_at=point.available_at,
                field=field,
                kind=kind,
                previous_identity=None if previous is None else previous.identity,
                new_identity=None if current is None else current.identity,
                previous_envelope=None if previous is None else (previous.envelope_low, previous.envelope_high),
                new_envelope=None if current is None else (current.envelope_low, current.envelope_high),
                previous_distance_atr=None if previous is None else float(previous.distance_atr),
                new_distance_atr=None if current is None else float(current.distance_atr),
            ))
        previous_point = point
    return tuple(out)


def _semantic_side(snapshot, side: str):
    if snapshot is None:
        return None, None
    if side == "upside":
        return snapshot.nearest_upside_objective, snapshot.upside_arrival
    return snapshot.nearest_downside_objective, snapshot.downside_arrival


def semantic_replay_transition_ledger(replay: TargetingHistoricalReplay) -> tuple[SemanticReplayTransition, ...]:
    if len(replay.points) < 2:
        return ()
    out: list[SemanticReplayTransition] = []
    previous = replay.points[0]
    for point in replay.points[1:]:
        for side in ("upside", "downside"):
            prev_obj, prev_ctx = _semantic_side(previous.semantic_snapshot, side)
            curr_obj, curr_ctx = _semantic_side(point.semantic_snapshot, side)
            if prev_obj is None and curr_obj is not None:
                out.append(SemanticReplayTransition(point.available_at, side, SemanticReplayTransitionKind.OBJECTIVE_NEW, None, curr_obj.identity))
            elif prev_obj is not None and curr_obj is None:
                out.append(SemanticReplayTransition(point.available_at, side, SemanticReplayTransitionKind.OBJECTIVE_DISAPPEARED, prev_obj.identity, None))
            elif prev_obj is not None and curr_obj is not None and prev_obj.identity != curr_obj.identity:
                out.append(SemanticReplayTransition(point.available_at, side, SemanticReplayTransitionKind.OBJECTIVE_REPLACED, prev_obj.identity, curr_obj.identity))

            prev_state = None if prev_ctx is None else prev_ctx.state.value
            curr_state = None if curr_ctx is None else curr_ctx.state.value
            if prev_state != curr_state:
                out.append(SemanticReplayTransition(point.available_at, side, SemanticReplayTransitionKind.ARRIVAL_STATE_CHANGED, prev_state, curr_state))

            for attr, appeared, disappeared in (
                ("current_reactions", SemanticReplayTransitionKind.CURRENT_REACTION_ENTERED, SemanticReplayTransitionKind.CURRENT_REACTION_EXITED),
                ("reactions_ahead", SemanticReplayTransitionKind.AHEAD_REACTION_APPEARED, SemanticReplayTransitionKind.AHEAD_REACTION_DISAPPEARED),
                ("reactions_at", SemanticReplayTransitionKind.AT_REACTION_APPEARED, SemanticReplayTransitionKind.AT_REACTION_DISAPPEARED),
            ):
                prev_count = 0 if prev_ctx is None else len(getattr(prev_ctx, attr))
                curr_count = 0 if curr_ctx is None else len(getattr(curr_ctx, attr))
                if prev_count == 0 and curr_count > 0:
                    out.append(SemanticReplayTransition(point.available_at, side, appeared, str(prev_count), str(curr_count)))
                elif prev_count > 0 and curr_count == 0:
                    out.append(SemanticReplayTransition(point.available_at, side, disappeared, str(prev_count), str(curr_count)))
        previous = point
    return tuple(out)


def liquidity_scope_diagnostics(replay: TargetingHistoricalReplay) -> tuple[LiquidityScopeDiagnostic, ...]:
    buckets: dict[str, dict[str, object]] = {}
    for point in replay.points:
        semantic = point.semantic_snapshot
        if semantic is None:
            continue
        for objective in semantic.objectives:
            tf = objective.source.timeframe
            bucket = buckets.setdefault(tf, {"observations": 0, "unique": set(), "internal": 0, "external": 0, "unclassified": 0})
            bucket["observations"] = int(bucket["observations"]) + 1
            cast_unique = bucket["unique"]
            assert isinstance(cast_unique, set)
            cast_unique.add(objective.source.source_identity)
            scope = objective.liquidity_scope or LiquidityScope.UNCLASSIFIED
            key = scope.value.lower()
            bucket[key] = int(bucket[key]) + 1
    out: list[LiquidityScopeDiagnostic] = []
    for tf in sorted(buckets):
        bucket = buckets[tf]
        total = int(bucket["observations"])
        unique = bucket["unique"]
        assert isinstance(unique, set)
        internal = int(bucket["internal"])
        external = int(bucket["external"])
        unclassified = int(bucket["unclassified"])
        denom = max(total, 1)
        out.append(LiquidityScopeDiagnostic(
            timeframe=tf,
            observations=total,
            unique_objectives=len(unique),
            internal=internal,
            external=external,
            unclassified=unclassified,
            internal_pct=100.0 * internal / denom,
            external_pct=100.0 * external / denom,
            unclassified_pct=100.0 * unclassified / denom,
        ))
    return tuple(out)


def _intersection_width(left: TargetCluster, right: TargetCluster) -> float:
    return max(0.0, min(left.envelope_high, right.envelope_high) - max(left.envelope_low, right.envelope_low))


def _best_lineage_match(target: TargetCluster, point: TargetingReplayPoint) -> TargetCluster | None:
    candidates = [cluster for cluster in point.snapshot.clusters if _same_region(target, cluster)]
    if not candidates:
        return None
    return max(candidates, key=lambda cluster: (_intersection_width(target, cluster), cluster.independent_origin_count, cluster.independent_family_count))


def cluster_stability(replay: TargetingHistoricalReplay, *, point_index: int, cluster: TargetCluster) -> ClusterStability:
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
        age_reference_bars=int(current_point.reference_index) - int(first_point.reference_index) + 1,
    )


__all__ = [
    "ClusterStability",
    "LiquidityScopeDiagnostic",
    "SemanticReplayTransition",
    "SemanticReplayTransitionKind",
    "SemanticTargetTransition",
    "SemanticTransitionKind",
    "cluster_stability",
    "liquidity_scope_diagnostics",
    "semantic_replay_transition_ledger",
    "semantic_transition_ledger",
]
