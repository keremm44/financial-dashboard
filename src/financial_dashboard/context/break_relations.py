from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations

from .envelope import ContextDataQuality, ContextDomain, FactRef
from .pattern_behavior_projection import PatternBehaviorProjection, PatternBehaviorPhase
from .projections import StructuralFactsProjection
from .support_resistance_projection import SupportResistanceProjection


class BreakRelationKind(StrEnum):
    SAME_EVENT = "SAME_EVENT"
    POTENTIALLY_SHARED = "POTENTIALLY_SHARED"
    NESTED = "NESTED"
    INDEPENDENT = "INDEPENDENT"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class BreakEvidence:
    domain: ContextDomain
    timeframe: str
    direction: int
    level: float | None
    tolerance: float | None
    state: str
    ref: FactRef


@dataclass(frozen=True, slots=True)
class BreakRelation:
    left: BreakEvidence
    right: BreakEvidence
    relation: BreakRelationKind
    reasons: tuple[str, ...]

    @property
    def independently_countable(self) -> bool:
        return self.relation is BreakRelationKind.INDEPENDENT


@dataclass(frozen=True, slots=True)
class CrossDomainBreakRelations:
    evidence: tuple[BreakEvidence, ...]
    relations: tuple[BreakRelation, ...]


def classify_break_pair(left: BreakEvidence, right: BreakEvidence) -> BreakRelation:
    if left.domain is right.domain:
        return BreakRelation(
            left=left,
            right=right,
            relation=BreakRelationKind.UNRESOLVED,
            reasons=("SAME_DOMAIN_PAIR_NOT_CROSS_DOMAIN",),
        )

    if left.direction not in {-1, 1} or right.direction not in {-1, 1}:
        return BreakRelation(
            left=left,
            right=right,
            relation=BreakRelationKind.UNRESOLVED,
            reasons=("BREAK_DIRECTION_UNAVAILABLE",),
        )

    shared_lineage = (
        left.ref.lineage_id is not None
        and right.ref.lineage_id is not None
        and left.ref.lineage_id == right.ref.lineage_id
    )
    if shared_lineage:
        if left.direction != right.direction:
            return BreakRelation(
                left=left,
                right=right,
                relation=BreakRelationKind.UNRESOLVED,
                reasons=("SHARED_LINEAGE_DIRECTION_CONFLICT",),
            )
        return BreakRelation(
            left=left,
            right=right,
            relation=BreakRelationKind.SAME_EVENT,
            reasons=("EXPLICIT_SHARED_LINEAGE",),
        )

    if left.direction != right.direction:
        return BreakRelation(
            left=left,
            right=right,
            relation=BreakRelationKind.INDEPENDENT,
            reasons=("OPPOSITE_BREAK_DIRECTIONS",),
        )

    if left.level is None or right.level is None:
        return BreakRelation(
            left=left,
            right=right,
            relation=BreakRelationKind.UNRESOLVED,
            reasons=("BREAK_LEVEL_UNAVAILABLE",),
        )

    native_tolerances = tuple(
        value
        for value in (left.tolerance, right.tolerance)
        if value is not None and value >= 0.0
    )
    if not native_tolerances:
        return BreakRelation(
            left=left,
            right=right,
            relation=BreakRelationKind.UNRESOLVED,
            reasons=("NO_NATIVE_SPATIAL_TOLERANCE",),
        )

    tolerance = max(native_tolerances)
    distance = abs(float(left.level) - float(right.level))
    if distance > tolerance:
        return BreakRelation(
            left=left,
            right=right,
            relation=BreakRelationKind.INDEPENDENT,
            reasons=("BREAK_LEVELS_SEPARATED",),
        )

    if left.timeframe == right.timeframe:
        # Spatial alignment without explicit lineage is deliberately not promoted to
        # SAME_EVENT. Downstream code must not count this pair as independent.
        return BreakRelation(
            left=left,
            right=right,
            relation=BreakRelationKind.POTENTIALLY_SHARED,
            reasons=("SAME_TIMEFRAME_DIRECTION_AND_NATIVE_TOLERANCE_OVERLAP",),
        )

    return BreakRelation(
        left=left,
        right=right,
        relation=BreakRelationKind.NESTED,
        reasons=("CROSS_TIMEFRAME_DIRECTION_AND_NATIVE_TOLERANCE_OVERLAP",),
    )


def _structure_breaks(structure: StructuralFactsProjection) -> list[BreakEvidence]:
    rows: list[BreakEvidence] = []
    for timeframe_fact in structure.timeframe_facts:
        if timeframe_fact.data_quality is not ContextDataQuality.VALID:
            continue
        for event in timeframe_fact.events:
            if event.ref.data_quality is not ContextDataQuality.VALID:
                continue
            if event.event_type not in {"EVENT_BOS", "EVENT_CHOCH", "BOS", "CHOCH"}:
                continue
            if event.confirmation_status != "CONFIRMED":
                continue
            # This view describes evidence usable at the current decision snapshot;
            # superseded/historical structure remains auditable elsewhere but must
            # not participate in current cross-domain deduplication.
            if event.validity != "VALID" or event.relevance != "CURRENT":
                continue
            rows.append(
                BreakEvidence(
                    domain=ContextDomain.MARKET_STRUCTURE,
                    timeframe=timeframe_fact.timeframe,
                    direction=int(event.direction),
                    level=event.broken_level,
                    tolerance=None,
                    state=f"{event.event_type}:{event.outcome}:{event.bos_maturity}",
                    ref=event.ref,
                )
            )
    return rows


def _pattern_breaks(pattern: PatternBehaviorProjection | None) -> list[BreakEvidence]:
    if pattern is None:
        return []
    rows: list[BreakEvidence] = []
    active_phases = {
        PatternBehaviorPhase.BREAK_CONFIRMING,
        PatternBehaviorPhase.BREAK_CONFIRMED,
        PatternBehaviorPhase.POST_BREAK_RETEST,
        PatternBehaviorPhase.RETEST_HELD,
    }
    for item in pattern.timeframe_facts:
        if item.ref.data_quality is not ContextDataQuality.VALID:
            continue
        if item.phase not in active_phases:
            continue
        if item.break_state_code is None or item.break_state_code == 0:
            continue
        direction = 1 if item.break_state_code > 0 else -1
        rows.append(
            BreakEvidence(
                domain=ContextDomain.PATTERN,
                timeframe=item.timeframe,
                direction=direction,
                level=item.break_level,
                tolerance=item.retest_tolerance,
                state=f"{item.phase.value}:{item.break_state_code}",
                ref=item.ref,
            )
        )
    return rows


def _support_resistance_breaks(
    support_resistance: SupportResistanceProjection | None,
) -> list[BreakEvidence]:
    if support_resistance is None:
        return []
    rows: list[BreakEvidence] = []
    active_states = {"RANGE_BREAK_CANDIDATE", "RANGE_BREAK_CONFIRMED"}
    for item in support_resistance.timeframe_facts:
        if item.ref.data_quality is not ContextDataQuality.VALID:
            continue
        if item.state not in active_states or item.break_direction not in {-1, 1}:
            continue
        rows.append(
            BreakEvidence(
                domain=ContextDomain.SUPPORT_RESISTANCE,
                timeframe=item.timeframe,
                direction=int(item.break_direction),
                level=item.break_boundary,
                tolerance=item.break_buffer,
                state=str(item.state),
                ref=item.ref,
            )
        )
    return rows


def build_cross_domain_break_relations(
    *,
    structure: StructuralFactsProjection,
    pattern: PatternBehaviorProjection | None,
    support_resistance: SupportResistanceProjection | None,
) -> CrossDomainBreakRelations:
    evidence = tuple(
        sorted(
            (
                *_structure_breaks(structure),
                *_pattern_breaks(pattern),
                *_support_resistance_breaks(support_resistance),
            ),
            key=lambda item: (
                item.timeframe,
                item.domain.value,
                item.direction,
                float("inf") if item.level is None else item.level,
                item.ref.deterministic_key,
            ),
        )
    )
    relations = tuple(
        classify_break_pair(left, right)
        for left, right in combinations(evidence, 2)
        if left.domain is not right.domain
    )
    return CrossDomainBreakRelations(
        evidence=evidence,
        relations=relations,
    )


__all__ = [
    "BreakEvidence",
    "BreakRelation",
    "BreakRelationKind",
    "CrossDomainBreakRelations",
    "build_cross_domain_break_relations",
    "classify_break_pair",
]
