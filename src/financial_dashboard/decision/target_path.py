from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Iterable

from financial_dashboard.context.envelope import ContextDataQuality, FactRef
from financial_dashboard.context.projections import (
    LiquidityProjection,
    ReactionEvidenceProjection,
)
from financial_dashboard.context.structural_levels import (
    StructuralLevelKind,
    StructuralLevelObservation,
    StructuralLevelRole,
    StructuralLevelSide,
    StructuralLevelView,
)
from financial_dashboard.context.support_resistance_projection import SupportResistanceProjection
from financial_dashboard.context.zone_interaction import ZoneInteractionState
from financial_dashboard.context.zones import (
    QualifiedZone,
    QualifiedZoneSide,
    ZoneAnchorKind,
    ZoneIntelligenceSnapshot,
)
from financial_dashboard.targeting.models import (
    TargetCluster,
    TargetClusterKind,
    TargetEvidence,
    TargetEvidenceType,
    TargetSide,
    TargetingSnapshot,
)

from .structural import StructuralDirection


class TargetPathRole(StrEnum):
    OBJECTIVE = "OBJECTIVE"
    BARRIER = "BARRIER"
    REACTION_WAYPOINT = "REACTION_WAYPOINT"


class TargetPathSource(StrEnum):
    LIQUIDITY = "LIQUIDITY"
    STRUCTURAL_WEAK = "STRUCTURAL_WEAK"
    SUPPORT_RESISTANCE = "SUPPORT_RESISTANCE"
    ORDER_BLOCK = "ORDER_BLOCK"
    FVG = "FVG"
    QUALIFIED_ZONE = "QUALIFIED_ZONE"


class NativePathDisposition(StrEnum):
    PENDING = "PENDING"
    CLEARED = "CLEARED"
    DEFENDED = "DEFENDED"


class TargetPathNodeState(StrEnum):
    ACTIVE = "ACTIVE"
    LOCKED = "LOCKED"
    CLEARED = "CLEARED"
    DEFENDED = "DEFENDED"


class TargetPathStatus(StrEnum):
    READY = "READY"
    NO_OBSERVED_PATH = "NO_OBSERVED_PATH"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class TargetPathBoundary:
    timeframe: str
    scope: str
    kind: StructuralLevelKind
    price: float
    identity: int


@dataclass(frozen=True, slots=True)
class TargetPathNode:
    identity: str
    direction: StructuralDirection
    low: float
    high: float
    anchor_price: float
    distance_price: float
    distance_atr: float | None
    roles: tuple[TargetPathRole, ...]
    sources: tuple[TargetPathSource, ...]
    timeframes: tuple[str, ...]
    source_keys: tuple[str, ...]
    lineage_ids: tuple[str, ...]
    source_refs: tuple[FactRef, ...]
    native_states: tuple[str, ...]
    native_disposition: NativePathDisposition
    state: TargetPathNodeState = TargetPathNodeState.LOCKED

    @property
    def is_objective(self) -> bool:
        return TargetPathRole.OBJECTIVE in self.roles


@dataclass(frozen=True, slots=True)
class TargetPath:
    symbol: str
    as_of: Any
    direction: StructuralDirection
    current_price: float
    status: TargetPathStatus
    nodes: tuple[TargetPathNode, ...]
    thesis_boundaries: tuple[TargetPathBoundary, ...]
    reasons: tuple[str, ...]

    @property
    def active_node(self) -> TargetPathNode | None:
        for node in self.nodes:
            if node.state in {TargetPathNodeState.ACTIVE, TargetPathNodeState.DEFENDED}:
                return node
        return None

    @property
    def active_objective(self) -> TargetPathNode | None:
        node = self.active_node
        return node if node is not None and node.is_objective else None


@dataclass
class _Candidate:
    identity: str
    low: float
    high: float
    roles: set[TargetPathRole]
    sources: set[TargetPathSource]
    timeframes: set[str]
    source_keys: set[str]
    lineage_ids: set[str]
    refs: dict[tuple[str, str, str, str, str], FactRef]
    native_states: set[str]
    dispositions: list[NativePathDisposition]


def _direction_side(direction: StructuralDirection) -> TargetSide | None:
    if direction is StructuralDirection.LONG:
        return TargetSide.ABOVE
    if direction is StructuralDirection.SHORT:
        return TargetSide.BELOW
    return None


def _zone_side(direction: StructuralDirection) -> QualifiedZoneSide | None:
    if direction is StructuralDirection.LONG:
        return QualifiedZoneSide.RESISTANCE
    if direction is StructuralDirection.SHORT:
        return QualifiedZoneSide.SUPPORT
    return None


def _ahead(direction: StructuralDirection, current_price: float, low: float, high: float) -> bool:
    if direction is StructuralDirection.LONG:
        return float(high) > current_price and float(low) >= current_price
    if direction is StructuralDirection.SHORT:
        return float(low) < current_price and float(high) <= current_price
    return False


def _distance(direction: StructuralDirection, current_price: float, low: float, high: float) -> float:
    if direction is StructuralDirection.LONG:
        return max(0.0, float(low) - current_price)
    if direction is StructuralDirection.SHORT:
        return max(0.0, current_price - float(high))
    return 0.0


def _lineage(ref: FactRef) -> str:
    return ref.lineage_id or f"{ref.domain.value}:{ref.timeframe}:{ref.native_id}"


def _new_candidate(
    *,
    identity: str,
    low: float,
    high: float,
    role: TargetPathRole,
    source: TargetPathSource,
    timeframe: str,
    source_keys: Iterable[str],
    refs: Iterable[FactRef] = (),
    native_states: Iterable[str] = (),
    disposition: NativePathDisposition = NativePathDisposition.PENDING,
) -> _Candidate:
    ref_rows = tuple(refs)
    return _Candidate(
        identity=identity,
        low=min(float(low), float(high)),
        high=max(float(low), float(high)),
        roles={role},
        sources={source},
        timeframes={timeframe},
        source_keys={str(key) for key in source_keys if str(key).strip()},
        lineage_ids={_lineage(ref) for ref in ref_rows},
        refs={ref.deterministic_key: ref for ref in ref_rows},
        native_states={str(state) for state in native_states if str(state).strip()},
        dispositions=[disposition],
    )


def _merge(left: _Candidate, right: _Candidate) -> _Candidate:
    left.low = min(left.low, right.low)
    left.high = max(left.high, right.high)
    left.roles.update(right.roles)
    left.sources.update(right.sources)
    left.timeframes.update(right.timeframes)
    left.source_keys.update(right.source_keys)
    left.lineage_ids.update(right.lineage_ids)
    left.refs.update(right.refs)
    left.native_states.update(right.native_states)
    left.dispositions.extend(right.dispositions)
    return left


def _deduplicate(candidates: Iterable[_Candidate]) -> list[_Candidate]:
    merged: list[_Candidate] = []
    for candidate in candidates:
        match = next(
            (
                existing
                for existing in merged
                if existing.source_keys.intersection(candidate.source_keys)
                or existing.lineage_ids.intersection(candidate.lineage_ids)
            ),
            None,
        )
        if match is None:
            merged.append(candidate)
        else:
            _merge(match, candidate)
    return merged


def _aggregate_disposition(values: Iterable[NativePathDisposition]) -> NativePathDisposition:
    rows = tuple(values)
    if NativePathDisposition.DEFENDED in rows:
        return NativePathDisposition.DEFENDED
    if rows and all(item is NativePathDisposition.CLEARED for item in rows):
        return NativePathDisposition.CLEARED
    return NativePathDisposition.PENDING


def _liquidity_disposition(
    evidence: Iterable[TargetEvidence],
    liquidity: LiquidityProjection | None,
) -> NativePathDisposition:
    if liquidity is None:
        return NativePathDisposition.PENDING
    states: list[NativePathDisposition] = []
    behavior = liquidity.behavior_observations
    for item in evidence:
        if item.evidence_type is not TargetEvidenceType.LIQUIDITY:
            continue
        matches = [
            row
            for row in behavior
            if row.ref.timeframe == item.timeframe and row.pool_identity == item.source_identity
        ]
        if not matches:
            states.append(NativePathDisposition.PENDING)
            continue
        row = matches[-1]
        removal = str(row.removal).strip().upper()
        if removal in {"ACCEPTED_BEYOND", "CONSUMED"}:
            states.append(NativePathDisposition.CLEARED)
        elif removal in {"SWEEP_REJECTING", "SWEEP_RECLAIMED"}:
            states.append(NativePathDisposition.DEFENDED)
        else:
            states.append(NativePathDisposition.PENDING)
    return _aggregate_disposition(states)


def _cluster_candidates(
    targeting: TargetingSnapshot | None,
    *,
    direction: StructuralDirection,
    current_price: float,
    liquidity: LiquidityProjection | None,
    as_of: Any,
) -> list[_Candidate]:
    if targeting is None:
        return []
    required_side = _direction_side(direction)
    if required_side is None:
        return []
    rows: list[_Candidate] = []
    for cluster in targeting.clusters:
        if cluster.side is not required_side or cluster.kind is not TargetClusterKind.LIQUIDITY_TARGET:
            continue
        if not _ahead(direction, current_price, cluster.envelope_low, cluster.envelope_high):
            continue
        evidence = tuple(
            item
            for item in cluster.evidence
            if item.evidence_type is TargetEvidenceType.LIQUIDITY
            and item.target_eligible
            and item.available_at <= as_of
        )
        if not evidence:
            continue
        refs = tuple(
            row.ref
            for row in (() if liquidity is None else liquidity.observations)
            for item in evidence
            if row.ref.timeframe == item.timeframe
            and (
                row.ref.native_id == item.native_origin_id
                or row.ref.lineage_id == item.origin_event_id
            )
            and row.ref.is_available_at(as_of)
        )
        keys = {
            cluster.identity,
            *(item.source_identity for item in evidence),
            *(item.native_origin_id for item in evidence),
            *(item.origin_event_id for item in evidence),
        }
        rows.append(
            _new_candidate(
                identity=f"LIQUIDITY:{cluster.identity}",
                low=cluster.envelope_low,
                high=cluster.envelope_high,
                role=TargetPathRole.OBJECTIVE,
                source=TargetPathSource.LIQUIDITY,
                timeframe=min(item.timeframe for item in evidence),
                source_keys=keys,
                refs=refs,
                native_states=(item.source_state for item in evidence),
                disposition=_liquidity_disposition(evidence, liquidity),
            )
        )
        rows[-1].timeframes.update(item.timeframe for item in evidence)
    return rows


def _structural_objective_candidates(
    levels: StructuralLevelView,
    *,
    direction: StructuralDirection,
    current_price: float,
) -> list[_Candidate]:
    rows: list[_Candidate] = []
    for level in levels.objectives:
        if level.role is not StructuralLevelRole.STRUCTURAL_OBJECTIVE:
            continue
        if direction is StructuralDirection.LONG:
            if level.kind is not StructuralLevelKind.WEAK_HIGH or level.side is not StructuralLevelSide.ABOVE:
                continue
        elif direction is StructuralDirection.SHORT:
            if level.kind is not StructuralLevelKind.WEAK_LOW or level.side is not StructuralLevelSide.BELOW:
                continue
        else:
            continue
        identity = f"MS:{level.timeframe}:{level.scope}:{level.kind.value}:{level.identity}"
        rows.append(
            _new_candidate(
                identity=identity,
                low=level.price,
                high=level.price,
                role=TargetPathRole.OBJECTIVE,
                source=TargetPathSource.STRUCTURAL_WEAK,
                timeframe=level.timeframe,
                source_keys=(identity,),
                native_states=(level.kind.value,),
            )
        )
    return rows


def _sr_disposition(lifecycle: str) -> NativePathDisposition:
    token = str(lifecycle).strip().upper()
    if token == "BROKEN":
        return NativePathDisposition.CLEARED
    if token == "BREAK_FAILED":
        return NativePathDisposition.DEFENDED
    return NativePathDisposition.PENDING


def _sr_candidates(
    support_resistance: SupportResistanceProjection | None,
    *,
    direction: StructuralDirection,
    current_price: float,
    as_of: Any,
) -> list[_Candidate]:
    if support_resistance is None:
        return []
    expected_side = "RESISTANCE" if direction is StructuralDirection.LONG else "SUPPORT"
    if direction is StructuralDirection.UNRESOLVED:
        return []
    rows: list[_Candidate] = []
    for timeframe in support_resistance.timeframe_facts:
        if not timeframe.ref.is_available_at(as_of) or timeframe.ref.data_quality is not ContextDataQuality.VALID:
            continue
        for zone in timeframe.zones:
            if str(zone.side).strip().upper() != expected_side:
                continue
            if str(zone.lifecycle).strip().upper() in {"ARCHIVED", "INVALIDATED"}:
                continue
            if not _ahead(direction, current_price, zone.low, zone.high):
                continue
            key = f"SR:{timeframe.timeframe}:{zone.zone_id}"
            rows.append(
                _new_candidate(
                    identity=key,
                    low=zone.low,
                    high=zone.high,
                    role=TargetPathRole.BARRIER,
                    source=TargetPathSource.SUPPORT_RESISTANCE,
                    timeframe=timeframe.timeframe,
                    source_keys=(key, zone.zone_id),
                    refs=(timeframe.ref,),
                    native_states=(zone.lifecycle,),
                    disposition=_sr_disposition(zone.lifecycle),
                )
            )
    return rows


def _qualified_zone_disposition(zone: QualifiedZone) -> NativePathDisposition:
    if zone.interaction is ZoneInteractionState.ACCEPTED_THROUGH:
        return NativePathDisposition.CLEARED
    if zone.interaction in {ZoneInteractionState.DEFENDED, ZoneInteractionState.RECLAIMED}:
        return NativePathDisposition.DEFENDED
    return NativePathDisposition.PENDING


def _qualified_zone_candidates(
    zones: ZoneIntelligenceSnapshot | None,
    *,
    direction: StructuralDirection,
    current_price: float,
    as_of: Any,
) -> list[_Candidate]:
    if zones is None:
        return []
    expected_side = _zone_side(direction)
    if expected_side is None:
        return []
    rows: list[_Candidate] = []
    for zone in zones.zones:
        if zone.side is not expected_side:
            continue
        if zone.anchor_kind is ZoneAnchorKind.PROTECTED_LEVEL:
            continue
        if zone.data_quality is not ContextDataQuality.VALID:
            continue
        if not zone.is_currently_qualified and zone.interaction is not ZoneInteractionState.ACCEPTED_THROUGH:
            continue
        if not _ahead(direction, current_price, zone.low, zone.high):
            continue
        refs = tuple(
            ref
            for ref in (*zone.reaction_refs, *zone.objective_refs, *zone.confirmation_refs)
            if ref.is_available_at(as_of)
        )
        role = (
            TargetPathRole.REACTION_WAYPOINT
            if zone.reaction_refs
            else TargetPathRole.BARRIER
        )
        keys = {zone.zone_id, *zone.anchor_refs}
        keys.update(ref.native_id for ref in refs)
        keys.update(ref.lineage_id for ref in refs if ref.lineage_id)
        rows.append(
            _new_candidate(
                identity=f"ZONE:{zone.zone_id}",
                low=zone.low,
                high=zone.high,
                role=role,
                source=TargetPathSource.QUALIFIED_ZONE,
                timeframe=zone.anchor_timeframe,
                source_keys=keys,
                refs=refs,
                native_states=(zone.native_lifecycle, zone.interaction.value),
                disposition=_qualified_zone_disposition(zone),
            )
        )
        if zone.objective_refs:
            rows[-1].roles.add(TargetPathRole.OBJECTIVE)
    return rows


def _reaction_disposition(native_state: str) -> NativePathDisposition:
    token = str(native_state).strip().upper()
    if token in {"CONSUMED", "FULL_FILL", "INVALID", "SUPERSEDED"}:
        return NativePathDisposition.CLEARED
    if token in {
        "REACTION",
        "REACTION_HOLDING",
        "HOLDING_FAVORABLE",
        "REACTION_CONFIRMED",
        "CONTINUATION_CONFIRMED",
    }:
        return NativePathDisposition.DEFENDED
    return NativePathDisposition.PENDING


def _reaction_candidates(
    reaction: ReactionEvidenceProjection | None,
    *,
    direction: StructuralDirection,
    current_price: float,
    as_of: Any,
) -> list[_Candidate]:
    if reaction is None or direction is StructuralDirection.UNRESOLVED:
        return []
    rows: list[_Candidate] = []
    for item in reaction.reaction_zones:
        if not item.ref.is_available_at(as_of) or item.ref.data_quality is not ContextDataQuality.VALID:
            continue
        roles = {str(role).strip().upper() for role in item.roles}
        if direction is StructuralDirection.LONG and "SUPPLY" not in roles:
            continue
        if direction is StructuralDirection.SHORT and "DEMAND" not in roles:
            continue
        if not _ahead(direction, current_price, item.low, item.high):
            continue
        if item.evidence_type == TargetEvidenceType.ORDER_BLOCK.value:
            source = TargetPathSource.ORDER_BLOCK
        elif item.evidence_type == TargetEvidenceType.FVG.value:
            source = TargetPathSource.FVG
        else:
            # Engulfing lives in confirmations, not reaction_zones. Unknown reaction
            # types fail closed instead of becoming a path target.
            continue
        keys = {item.ref.native_id, _lineage(item.ref)}
        rows.append(
            _new_candidate(
                identity=f"REACTION:{item.ref.native_id}",
                low=item.low,
                high=item.high,
                role=TargetPathRole.REACTION_WAYPOINT,
                source=source,
                timeframe=item.ref.timeframe,
                source_keys=keys,
                refs=(item.ref,),
                native_states=(item.ref.native_state,),
                disposition=_reaction_disposition(item.ref.native_state),
            )
        )
    return rows


def _boundaries(
    levels: StructuralLevelView,
    direction: StructuralDirection,
) -> tuple[TargetPathBoundary, ...]:
    rows: list[TargetPathBoundary] = []
    for level in levels.thesis_boundaries:
        if direction is StructuralDirection.LONG:
            if level.kind is not StructuralLevelKind.PROTECTED_LOW:
                continue
        elif direction is StructuralDirection.SHORT:
            if level.kind is not StructuralLevelKind.PROTECTED_HIGH:
                continue
        else:
            continue
        rows.append(
            TargetPathBoundary(
                timeframe=level.timeframe,
                scope=level.scope,
                kind=level.kind,
                price=level.price,
                identity=level.identity,
            )
        )
    return tuple(sorted(rows, key=lambda item: (item.timeframe, item.scope, item.price, item.identity)))


def _to_node(
    candidate: _Candidate,
    *,
    direction: StructuralDirection,
    current_price: float,
    reference_atr: float | None,
) -> TargetPathNode:
    distance_price = _distance(direction, current_price, candidate.low, candidate.high)
    distance_atr = (
        None
        if reference_atr is None or reference_atr <= 0
        else distance_price / float(reference_atr)
    )
    disposition = _aggregate_disposition(candidate.dispositions)
    return TargetPathNode(
        identity=candidate.identity,
        direction=direction,
        low=candidate.low,
        high=candidate.high,
        anchor_price=(candidate.low + candidate.high) * 0.5,
        distance_price=distance_price,
        distance_atr=distance_atr,
        roles=tuple(sorted(candidate.roles, key=lambda item: item.value)),
        sources=tuple(sorted(candidate.sources, key=lambda item: item.value)),
        timeframes=tuple(sorted(candidate.timeframes)),
        source_keys=tuple(sorted(candidate.source_keys)),
        lineage_ids=tuple(sorted(candidate.lineage_ids)),
        source_refs=tuple(sorted(candidate.refs.values(), key=lambda ref: ref.deterministic_key)),
        native_states=tuple(sorted(candidate.native_states)),
        native_disposition=disposition,
    )


def _sort_nodes(nodes: Iterable[TargetPathNode], direction: StructuralDirection) -> tuple[TargetPathNode, ...]:
    if direction is StructuralDirection.LONG:
        return tuple(sorted(nodes, key=lambda item: (item.distance_price, item.low, item.identity)))
    return tuple(sorted(nodes, key=lambda item: (item.distance_price, -item.high, item.identity)))


def _assign_fsm(nodes: tuple[TargetPathNode, ...]) -> tuple[TargetPathNode, ...]:
    assigned: list[TargetPathNode] = []
    unlocked = True
    for node in nodes:
        if node.native_disposition is NativePathDisposition.CLEARED:
            assigned.append(replace(node, state=TargetPathNodeState.CLEARED))
            continue
        if unlocked and node.native_disposition is NativePathDisposition.DEFENDED:
            assigned.append(replace(node, state=TargetPathNodeState.DEFENDED))
            unlocked = False
            continue
        if unlocked:
            assigned.append(replace(node, state=TargetPathNodeState.ACTIVE))
            unlocked = False
            continue
        assigned.append(replace(node, state=TargetPathNodeState.LOCKED))
    return tuple(assigned)


def build_target_path(
    *,
    symbol: str,
    as_of: Any,
    direction: StructuralDirection,
    current_price: float,
    structural_levels: StructuralLevelView,
    targeting: TargetingSnapshot | None = None,
    liquidity: LiquidityProjection | None = None,
    support_resistance: SupportResistanceProjection | None = None,
    reaction: ReactionEvidenceProjection | None = None,
    qualified_zones: ZoneIntelligenceSnapshot | None = None,
    reference_atr: float | None = None,
) -> TargetPath:
    """Build an ordered causal path of objectives/barriers without predicting reach.

    Ordering is geometric only. Quality scores never move a farther node ahead of a
    closer barrier. Native lifecycle decides whether the first node was actually
    cleared or defended; a sweep/reclaim never unlocks the next node.
    """

    if direction is StructuralDirection.UNRESOLVED:
        return TargetPath(
            symbol=symbol,
            as_of=as_of,
            direction=direction,
            current_price=float(current_price),
            status=TargetPathStatus.UNKNOWN,
            nodes=(),
            thesis_boundaries=(),
            reasons=("TARGET_PATH_DIRECTION_UNRESOLVED",),
        )

    candidates: list[_Candidate] = []
    candidates.extend(
        _cluster_candidates(
            targeting,
            direction=direction,
            current_price=float(current_price),
            liquidity=liquidity,
            as_of=as_of,
        )
    )
    candidates.extend(
        _structural_objective_candidates(
            structural_levels,
            direction=direction,
            current_price=float(current_price),
        )
    )
    candidates.extend(
        _sr_candidates(
            support_resistance,
            direction=direction,
            current_price=float(current_price),
            as_of=as_of,
        )
    )
    candidates.extend(
        _qualified_zone_candidates(
            qualified_zones,
            direction=direction,
            current_price=float(current_price),
            as_of=as_of,
        )
    )
    candidates.extend(
        _reaction_candidates(
            reaction,
            direction=direction,
            current_price=float(current_price),
            as_of=as_of,
        )
    )

    merged = _deduplicate(candidates)
    nodes = _sort_nodes(
        (
            _to_node(
                candidate,
                direction=direction,
                current_price=float(current_price),
                reference_atr=reference_atr,
            )
            for candidate in merged
        ),
        direction,
    )
    nodes = _assign_fsm(nodes)
    boundaries = _boundaries(structural_levels, direction)
    status = TargetPathStatus.READY if nodes else TargetPathStatus.NO_OBSERVED_PATH
    reasons = (
        f"TARGET_PATH_NODES:{len(nodes)}",
        "TARGET_PATH_ORDER:GEOMETRIC_NEAREST_FIRST",
        "NO_OBSERVED_PATH_IS_NOT_CLEAR_PATH" if not nodes else "TARGET_PATH_CAUSAL_FSM",
    )
    return TargetPath(
        symbol=symbol,
        as_of=as_of,
        direction=direction,
        current_price=float(current_price),
        status=status,
        nodes=nodes,
        thesis_boundaries=boundaries,
        reasons=reasons,
    )


def build_target_path_from_snapshot(snapshot: Any, direction: StructuralDirection) -> TargetPath:
    reference_atr = None
    if snapshot.targeting is not None:
        reference_atr = float(snapshot.targeting.reference_atr)
    return build_target_path(
        symbol=snapshot.symbol,
        as_of=snapshot.as_of,
        direction=direction,
        current_price=float(snapshot.current_price),
        structural_levels=snapshot.structural_levels,
        targeting=snapshot.targeting,
        liquidity=snapshot.liquidity,
        support_resistance=snapshot.support_resistance,
        reaction=snapshot.reaction,
        qualified_zones=snapshot.qualified_zones,
        reference_atr=reference_atr,
    )


__all__ = [
    "NativePathDisposition",
    "TargetPath",
    "TargetPathBoundary",
    "TargetPathNode",
    "TargetPathNodeState",
    "TargetPathRole",
    "TargetPathSource",
    "TargetPathStatus",
    "build_target_path",
    "build_target_path_from_snapshot",
]
