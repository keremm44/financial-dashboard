from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from itertools import combinations
import json
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from .models import (
    EvidenceDataQuality,
    EvidenceDirection,
    EvidenceFamily,
    EvidenceRole,
    NormalizedLevel,
    ProvenanceType,
    TechnicalEvidenceItem,
    TechnicalEvidencePacket,
)


class FreshnessClass(StrEnum):
    CURRENT = "CURRENT"
    RECENT = "RECENT"
    AGED = "AGED"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class ConflictKind(StrEnum):
    SAME_ROLE_OPPOSITION = "SAME_ROLE_OPPOSITION"
    CROSS_ROLE_OPPOSITION = "CROSS_ROLE_OPPOSITION"
    DERIVED_OPPOSITION = "DERIVED_OPPOSITION"


@dataclass(frozen=True, slots=True)
class FreshnessRecord:
    target_id: str
    target_kind: str
    value: float | None
    classification: FreshnessClass
    age_bars: int | None
    anchor: str
    horizon_bars: float | None


@dataclass(frozen=True, slots=True)
class DependencyValidationReport:
    unresolved_dependencies: tuple[tuple[str, str], ...] = ()
    cycles: tuple[tuple[str, ...], ...] = ()
    future_dependencies: tuple[tuple[str, str], ...] = ()
    unverifiable_order: tuple[tuple[str, str], ...] = ()

    @property
    def valid(self) -> bool:
        return not self.unresolved_dependencies and not self.cycles and not self.future_dependencies


@dataclass(frozen=True, slots=True)
class EvidenceLineage:
    evidence_id: str
    independence_group: str
    root_ids: tuple[str, ...]
    depth: int
    dependency_ids: tuple[str, ...]
    overlaps_with: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceConflict:
    id: str
    kind: ConflictKind
    left_id: str
    right_id: str
    left_role: EvidenceRole
    right_role: EvidenceRole
    left_family: EvidenceFamily
    right_family: EvidenceFamily
    left_direction: EvidenceDirection
    right_direction: EvidenceDirection
    independent: bool
    shared_lineage: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SemanticRoleSummary:
    role: EvidenceRole
    evidence_ids: tuple[str, ...] = ()
    root_like_ids: tuple[str, ...] = ()
    derived_ids: tuple[str, ...] = ()
    bullish_ids: tuple[str, ...] = ()
    bearish_ids: tuple[str, ...] = ()
    neutral_ids: tuple[str, ...] = ()
    current_ids: tuple[str, ...] = ()
    stale_ids: tuple[str, ...] = ()
    unknown_freshness_ids: tuple[str, ...] = ()
    limited_quality_ids: tuple[str, ...] = ()
    conflict_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SemanticEvidenceSummary:
    context: SemanticRoleSummary
    structure: SemanticRoleSummary
    location: SemanticRoleSummary
    trigger: SemanticRoleSummary
    confirmation: SemanticRoleSummary
    timing: SemanticRoleSummary
    risk: SemanticRoleSummary

    def for_role(self, role: EvidenceRole) -> SemanticRoleSummary:
        mapping = {
            EvidenceRole.CONTEXT: self.context,
            EvidenceRole.STRUCTURE: self.structure,
            EvidenceRole.LOCATION: self.location,
            EvidenceRole.TRIGGER: self.trigger,
            EvidenceRole.CONFIRMATION: self.confirmation,
            EvidenceRole.TIMING: self.timing,
            EvidenceRole.RISK: self.risk,
        }
        return mapping[role]


@dataclass(frozen=True, slots=True)
class EvidenceAudit:
    evidence_count: int
    level_count: int
    root_count: int
    derived_count: int
    aggregated_count: int
    contextual_count: int
    dependency_edges: int
    independence_group_count: int
    unlinked_derived_ids: tuple[str, ...] = ()
    multi_item_independence_groups: tuple[tuple[str, tuple[str, ...]], ...] = ()
    overlapping_lineage_pairs: tuple[tuple[str, str], ...] = ()
    unverifiable_dependency_order: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class TechnicalEvidenceBundle:
    as_of_timestamp: Any | None
    packets: tuple[TechnicalEvidencePacket, ...]
    evidence: tuple[TechnicalEvidenceItem, ...]
    levels: tuple[NormalizedLevel, ...]
    freshness: tuple[FreshnessRecord, ...]
    lineage: tuple[EvidenceLineage, ...]
    conflicts: tuple[EvidenceConflict, ...]
    semantic: SemanticEvidenceSummary
    audit: EvidenceAudit

    def __post_init__(self) -> None:
        evidence_ids = [item.id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("duplicate evidence id in Technical Evidence bundle")
        level_ids = [level.id for level in self.levels]
        if len(level_ids) != len(set(level_ids)):
            raise ValueError("duplicate level id in Technical Evidence bundle")
        valid_levels = set(level_ids)
        dangling = sorted({ref for item in self.evidence for ref in item.level_refs if ref not in valid_levels})
        if dangling:
            raise ValueError(f"dangling bundle level references: {dangling}")

    def evidence_by_id(self, evidence_id: str) -> TechnicalEvidenceItem | None:
        return next((item for item in self.evidence if item.id == evidence_id), None)

    def level_by_id(self, level_id: str) -> NormalizedLevel | None:
        return next((level for level in self.levels if level.id == level_id), None)

    def lineage_by_id(self, evidence_id: str) -> EvidenceLineage | None:
        return next((item for item in self.lineage if item.evidence_id == evidence_id), None)


class EvidenceGraphError(ValueError):
    pass


_ROLE_FRESHNESS_HORIZON = {
    EvidenceRole.CONTEXT: 20.0,
    EvidenceRole.STRUCTURE: 18.0,
    EvidenceRole.LOCATION: 24.0,
    EvidenceRole.TRIGGER: 6.0,
    EvidenceRole.CONFIRMATION: 8.0,
    EvidenceRole.TIMING: 4.0,
    EvidenceRole.RISK: 8.0,
}

_PERSISTENT_STATE_TOKENS = (
    "ACTIVE",
    "PROTECTED",
    "CONFIRMED",
    "STABLE",
    "HEALTHY",
    "DEFINED",
    "STABILIZING",
)
_TERMINAL_STATE_TOKENS = (
    "INVALID",
    "EXPIRED",
    "SUPERSEDED",
    "FULL_FILL",
    "FAILED",
    "ENDED",
)

_UNUSABLE_FOR_CONFLICT = {
    EvidenceDataQuality.WARMUP,
    EvidenceDataQuality.INCOMPLETE_BAR,
    EvidenceDataQuality.SOURCE_GAP,
    EvidenceDataQuality.DATA_INVALID,
    EvidenceDataQuality.UNSUPPORTED_TIMEFRAME,
}


_SOURCE_GROUPS = {
    "market_structure": "MARKET_STRUCTURE_CORE",
    "pattern_compression": "PATTERN_CORE",
    "mtf_story": "MTF_STORY_DERIVED",
    "liquidity": "LIQUIDITY_CORE",
    "auction": "AUCTION_CORE",
    "support_resistance": "SUPPORT_RESISTANCE_CORE",
    "volume_participation": "VOLUME_PARTICIPATION_CORE",
    "stabil_trend": "STABIL_TREND_CORE",
    "volatility_bands_fib": "VOLATILITY_BANDS_FIB_CORE",
    "order_block": "ORDER_BLOCK_CORE",
    "fvg_engulfing": "FVG_ENGULFING_CORE",
}


def independence_group_for(item: TechnicalEvidenceItem) -> str:
    if item.source_engine == "ham_dashboard":
        if item.evidence_type == "HAM_MOMENTUM":
            return "HAM_MOMENTUM"
        if item.evidence_type == "HAM_TIMING":
            return "HAM_TIMING"
        return "HAM_DASHBOARD"
    return _SOURCE_GROUPS.get(item.source_engine, item.source_engine.upper())


def link_visible_dependencies(items: Sequence[TechnicalEvidenceItem]) -> tuple[TechnicalEvidenceItem, ...]:
    """Link only dependencies that are visible in TEL.

    MTF Story is the only current downstream export explicitly derived from other
    TEL-visible engines. It may depend on Market Structure and Pattern evidence
    for the timeframes named by its own public `timeframe_states` payload.
    """

    out: list[TechnicalEvidenceItem] = []
    for item in items:
        if item.family is not EvidenceFamily.MTF_STORY or item.provenance_type is not ProvenanceType.DERIVED:
            out.append(item)
            continue

        raw_states = item.raw_export.get("timeframe_states", ()) if isinstance(item.raw_export, Mapping) else ()
        timeframes = {
            str(state.get("timeframe")).strip().lower()
            for state in raw_states
            if isinstance(state, Mapping) and state.get("timeframe")
        }
        if not timeframes:
            out.append(item)
            continue

        visible = []
        for candidate in items:
            if candidate.id == item.id:
                continue
            if candidate.family not in {EvidenceFamily.MARKET_STRUCTURE, EvidenceFamily.PATTERN}:
                continue
            if candidate.timeframe.strip().lower() not in timeframes:
                continue
            relation = _causal_relation(item, candidate)
            if relation is False:
                continue
            visible.append(candidate.id)
        deps = tuple(sorted(set(item.depends_on).union(visible)))
        out.append(replace(item, depends_on=deps))
    return tuple(_sort_evidence(out))


def validate_dependency_graph(items: Sequence[TechnicalEvidenceItem]) -> DependencyValidationReport:
    by_id = {item.id: item for item in items}
    unresolved: list[tuple[str, str]] = []
    future: list[tuple[str, str]] = []
    unverifiable: list[tuple[str, str]] = []

    for item in items:
        for dep_id in item.depends_on:
            dep = by_id.get(dep_id)
            if dep is None:
                unresolved.append((item.id, dep_id))
                continue
            relation = _causal_relation(item, dep)
            if relation is False:
                future.append((item.id, dep_id))
            elif relation is None:
                unverifiable.append((item.id, dep_id))

    cycles = _find_cycles(items, by_id)
    return DependencyValidationReport(
        unresolved_dependencies=tuple(sorted(set(unresolved))),
        cycles=cycles,
        future_dependencies=tuple(sorted(set(future))),
        unverifiable_order=tuple(sorted(set(unverifiable))),
    )


def build_technical_evidence_bundle(
    packets: Iterable[TechnicalEvidencePacket],
    *,
    as_of_timestamp: Any | None = None,
    as_of_known_bars: Mapping[str, int] | None = None,
) -> TechnicalEvidenceBundle:
    packet_tuple = tuple(sorted(tuple(packets), key=_packet_sort_key))
    raw_evidence = _dedupe_evidence(item for packet in packet_tuple for item in packet.evidence)
    raw_levels = _dedupe_levels(level for packet in packet_tuple for level in packet.levels)

    resolved_as_of = as_of_timestamp if as_of_timestamp is not None else _latest_comparable_timestamp(
        packet.timestamp for packet in packet_tuple
    )
    _validate_as_of_timestamp(raw_evidence, raw_levels, resolved_as_of)

    linked = link_visible_dependencies(raw_evidence)
    graph_report = validate_dependency_graph(linked)
    if not graph_report.valid:
        raise EvidenceGraphError(_graph_error_message(graph_report))

    known_bars = _resolve_known_bars(linked, packet_tuple, as_of_known_bars)
    _validate_as_of_bars(linked, raw_levels, known_bars)

    fresh_evidence, fresh_levels, freshness_records = _apply_freshness(linked, raw_levels, known_bars)
    lineage = _build_lineage(fresh_evidence)
    conflicts = _build_conflicts(fresh_evidence, lineage)
    semantic = _build_semantic(fresh_evidence, conflicts)
    audit = _build_audit(fresh_evidence, fresh_levels, lineage, graph_report)

    return TechnicalEvidenceBundle(
        as_of_timestamp=resolved_as_of,
        packets=packet_tuple,
        evidence=tuple(_sort_evidence(fresh_evidence)),
        levels=tuple(_sort_levels(fresh_levels)),
        freshness=tuple(sorted(freshness_records, key=lambda item: (item.target_kind, item.target_id))),
        lineage=lineage,
        conflicts=conflicts,
        semantic=semantic,
        audit=audit,
    )


def _build_lineage(items: Sequence[TechnicalEvidenceItem]) -> tuple[EvidenceLineage, ...]:
    by_id = {item.id: item for item in items}
    memo: dict[str, tuple[tuple[str, ...], int]] = {}

    def resolve(evidence_id: str) -> tuple[tuple[str, ...], int]:
        if evidence_id in memo:
            return memo[evidence_id]
        item = by_id[evidence_id]
        if not item.depends_on:
            roots = () if item.provenance_type is ProvenanceType.DERIVED else (item.id,)
            depth = 1 if item.provenance_type is ProvenanceType.DERIVED else 0
            memo[evidence_id] = (roots, depth)
            return memo[evidence_id]
        roots: set[str] = set()
        depths: list[int] = []
        for dep_id in item.depends_on:
            dep_roots, dep_depth = resolve(dep_id)
            roots.update(dep_roots)
            depths.append(dep_depth)
        memo[evidence_id] = (tuple(sorted(roots)), 1 + max(depths, default=0))
        return memo[evidence_id]

    base: dict[str, EvidenceLineage] = {}
    for item in items:
        roots, depth = resolve(item.id)
        base[item.id] = EvidenceLineage(
            evidence_id=item.id,
            independence_group=independence_group_for(item),
            root_ids=roots,
            depth=depth,
            dependency_ids=tuple(sorted(item.depends_on)),
        )

    overlap_map: dict[str, set[str]] = {item.id: set() for item in items}
    for left, right in combinations(items, 2):
        left_lineage = base[left.id]
        right_lineage = base[right.id]
        shared_roots = set(left_lineage.root_ids).intersection(right_lineage.root_ids)
        same_group = left_lineage.independence_group == right_lineage.independence_group
        if shared_roots or same_group:
            overlap_map[left.id].add(right.id)
            overlap_map[right.id].add(left.id)

    return tuple(
        replace(base[evidence_id], overlaps_with=tuple(sorted(overlap_map[evidence_id])))
        for evidence_id in sorted(base)
    )


def _build_conflicts(
    items: Sequence[TechnicalEvidenceItem],
    lineage: Sequence[EvidenceLineage],
) -> tuple[EvidenceConflict, ...]:
    lineage_by_id = {item.evidence_id: item for item in lineage}
    conflicts: list[EvidenceConflict] = []
    for left, right in combinations(_sort_evidence(items), 2):
        if left.direction is EvidenceDirection.NEUTRAL or right.direction is EvidenceDirection.NEUTRAL:
            continue
        if left.direction is right.direction:
            continue
        if left.data_quality in _UNUSABLE_FOR_CONFLICT or right.data_quality in _UNUSABLE_FOR_CONFLICT:
            continue
        left_lineage = lineage_by_id[left.id]
        right_lineage = lineage_by_id[right.id]
        if left_lineage.independence_group == right_lineage.independence_group:
            continue
        shared = tuple(sorted(set(left_lineage.root_ids).intersection(right_lineage.root_ids)))
        derived = (
            left.provenance_type is ProvenanceType.DERIVED
            or right.provenance_type is ProvenanceType.DERIVED
            or bool(shared)
        )
        if derived:
            kind = ConflictKind.DERIVED_OPPOSITION
        elif left.role is right.role:
            kind = ConflictKind.SAME_ROLE_OPPOSITION
        else:
            kind = ConflictKind.CROSS_ROLE_OPPOSITION
        conflict_id = _stable_id("conflict", kind.value, left.id, right.id)
        conflicts.append(
            EvidenceConflict(
                id=conflict_id,
                kind=kind,
                left_id=left.id,
                right_id=right.id,
                left_role=left.role,
                right_role=right.role,
                left_family=left.family,
                right_family=right.family,
                left_direction=left.direction,
                right_direction=right.direction,
                independent=not derived,
                shared_lineage=shared,
            )
        )
    return tuple(sorted(conflicts, key=lambda item: item.id))


def _build_semantic(
    items: Sequence[TechnicalEvidenceItem],
    conflicts: Sequence[EvidenceConflict],
) -> SemanticEvidenceSummary:
    summaries = {role: _role_summary(role, items, conflicts) for role in EvidenceRole}
    return SemanticEvidenceSummary(
        context=summaries[EvidenceRole.CONTEXT],
        structure=summaries[EvidenceRole.STRUCTURE],
        location=summaries[EvidenceRole.LOCATION],
        trigger=summaries[EvidenceRole.TRIGGER],
        confirmation=summaries[EvidenceRole.CONFIRMATION],
        timing=summaries[EvidenceRole.TIMING],
        risk=summaries[EvidenceRole.RISK],
    )


def _role_summary(
    role: EvidenceRole,
    items: Sequence[TechnicalEvidenceItem],
    conflicts: Sequence[EvidenceConflict],
) -> SemanticRoleSummary:
    selected = [item for item in items if item.role is role]
    ids = {item.id for item in selected}
    conflict_ids = [
        conflict.id
        for conflict in conflicts
        if conflict.left_id in ids or conflict.right_id in ids
    ]
    return SemanticRoleSummary(
        role=role,
        evidence_ids=tuple(sorted(ids)),
        root_like_ids=tuple(sorted(item.id for item in selected if item.provenance_type is not ProvenanceType.DERIVED)),
        derived_ids=tuple(sorted(item.id for item in selected if item.provenance_type is ProvenanceType.DERIVED)),
        bullish_ids=tuple(sorted(item.id for item in selected if item.direction is EvidenceDirection.BULL)),
        bearish_ids=tuple(sorted(item.id for item in selected if item.direction is EvidenceDirection.BEAR)),
        neutral_ids=tuple(sorted(item.id for item in selected if item.direction is EvidenceDirection.NEUTRAL)),
        current_ids=tuple(sorted(item.id for item in selected if item.freshness is not None and item.freshness >= 0.80)),
        stale_ids=tuple(sorted(item.id for item in selected if item.freshness is not None and item.freshness < 0.25)),
        unknown_freshness_ids=tuple(sorted(item.id for item in selected if item.freshness is None)),
        limited_quality_ids=tuple(
            sorted(
                item.id
                for item in selected
                if item.data_quality not in {EvidenceDataQuality.OK, EvidenceDataQuality.UNKNOWN}
            )
        ),
        conflict_ids=tuple(sorted(conflict_ids)),
    )


def _build_audit(
    items: Sequence[TechnicalEvidenceItem],
    levels: Sequence[NormalizedLevel],
    lineage: Sequence[EvidenceLineage],
    graph_report: DependencyValidationReport,
) -> EvidenceAudit:
    group_map: dict[str, list[str]] = {}
    for record in lineage:
        group_map.setdefault(record.independence_group, []).append(record.evidence_id)
    multi_groups = tuple(
        (group, tuple(sorted(ids)))
        for group, ids in sorted(group_map.items())
        if len(ids) > 1
    )
    overlap_pairs = {
        tuple(sorted((record.evidence_id, other)))
        for record in lineage
        for other in record.overlaps_with
        if record.evidence_id != other
    }
    return EvidenceAudit(
        evidence_count=len(items),
        level_count=len(levels),
        root_count=sum(item.provenance_type is ProvenanceType.ROOT for item in items),
        derived_count=sum(item.provenance_type is ProvenanceType.DERIVED for item in items),
        aggregated_count=sum(item.provenance_type is ProvenanceType.AGGREGATED for item in items),
        contextual_count=sum(item.provenance_type is ProvenanceType.CONTEXTUAL for item in items),
        dependency_edges=sum(len(item.depends_on) for item in items),
        independence_group_count=len(group_map),
        unlinked_derived_ids=tuple(
            sorted(item.id for item in items if item.provenance_type is ProvenanceType.DERIVED and not item.depends_on)
        ),
        multi_item_independence_groups=multi_groups,
        overlapping_lineage_pairs=tuple(sorted(overlap_pairs)),
        unverifiable_dependency_order=graph_report.unverifiable_order,
    )


def _apply_freshness(
    items: Sequence[TechnicalEvidenceItem],
    levels: Sequence[NormalizedLevel],
    known_bars: Mapping[str, int],
) -> tuple[tuple[TechnicalEvidenceItem, ...], tuple[NormalizedLevel, ...], tuple[FreshnessRecord, ...]]:
    records: list[FreshnessRecord] = []
    evidence_out: list[TechnicalEvidenceItem] = []
    evidence_freshness: dict[str, float] = {}

    for item in items:
        record = _evidence_freshness(item, known_bars)
        records.append(record)
        evidence_out.append(replace(item, freshness=record.value))
        if record.value is not None:
            evidence_freshness[item.id] = record.value

    refs: dict[str, list[float]] = {}
    for item in evidence_out:
        if item.freshness is None:
            continue
        for level_id in item.level_refs:
            refs.setdefault(level_id, []).append(item.freshness)

    level_out: list[NormalizedLevel] = []
    for level in levels:
        own = _level_freshness(level, known_bars)
        if own.value is None and refs.get(level.id):
            value = max(refs[level.id])
            own = FreshnessRecord(
                target_id=level.id,
                target_kind="LEVEL",
                value=value,
                classification=_freshness_class(value),
                age_bars=None,
                anchor="REFERENCING_EVIDENCE",
                horizon_bars=None,
            )
        records.append(own)
        level_out.append(replace(level, freshness=own.value))

    return tuple(evidence_out), tuple(level_out), tuple(records)


def _evidence_freshness(item: TechnicalEvidenceItem, known_bars: Mapping[str, int]) -> FreshnessRecord:
    if item.source_bar is None or item.timeframe not in known_bars:
        return FreshnessRecord(item.id, "EVIDENCE", None, FreshnessClass.UNKNOWN, None, "UNKNOWN", None)
    age = max(0, int(known_bars[item.timeframe]) - int(item.source_bar))
    horizon = _ROLE_FRESHNESS_HORIZON[item.role] * _state_horizon_multiplier(item.source_state)
    value = horizon / (horizon + float(age))
    return FreshnessRecord(
        target_id=item.id,
        target_kind="EVIDENCE",
        value=value,
        classification=_freshness_class(value),
        age_bars=age,
        anchor="SOURCE_BAR",
        horizon_bars=horizon,
    )


def _level_freshness(level: NormalizedLevel, known_bars: Mapping[str, int]) -> FreshnessRecord:
    if level.source_bar is None or level.timeframe not in known_bars:
        return FreshnessRecord(level.id, "LEVEL", None, FreshnessClass.UNKNOWN, None, "UNKNOWN", None)
    age = max(0, int(known_bars[level.timeframe]) - int(level.source_bar))
    horizon = _ROLE_FRESHNESS_HORIZON[EvidenceRole.LOCATION] * _state_horizon_multiplier(level.state)
    value = horizon / (horizon + float(age))
    return FreshnessRecord(
        target_id=level.id,
        target_kind="LEVEL",
        value=value,
        classification=_freshness_class(value),
        age_bars=age,
        anchor="SOURCE_BAR",
        horizon_bars=horizon,
    )


def _freshness_class(value: float | None) -> FreshnessClass:
    if value is None:
        return FreshnessClass.UNKNOWN
    if value >= 0.80:
        return FreshnessClass.CURRENT
    if value >= 0.50:
        return FreshnessClass.RECENT
    if value >= 0.25:
        return FreshnessClass.AGED
    return FreshnessClass.STALE


def _state_horizon_multiplier(state: Any) -> float:
    text = "" if state is None else str(getattr(state, "value", state)).upper()
    if any(token in text for token in _TERMINAL_STATE_TOKENS):
        return 0.50
    if any(token in text for token in _PERSISTENT_STATE_TOKENS):
        return 2.00
    return 1.00


def _resolve_known_bars(
    items: Sequence[TechnicalEvidenceItem],
    packets: Sequence[TechnicalEvidencePacket],
    override: Mapping[str, int] | None,
) -> dict[str, int]:
    resolved: dict[str, int] = {}
    for packet in packets:
        if packet.known_bar is not None:
            resolved[packet.timeframe] = max(resolved.get(packet.timeframe, packet.known_bar), packet.known_bar)
    for item in items:
        if item.known_bar is not None:
            resolved[item.timeframe] = max(resolved.get(item.timeframe, item.known_bar), item.known_bar)
    if override is not None:
        for timeframe, known_bar in override.items():
            resolved[str(timeframe)] = int(known_bar)
    return resolved


def _validate_as_of_bars(
    items: Sequence[TechnicalEvidenceItem],
    levels: Sequence[NormalizedLevel],
    known_bars: Mapping[str, int],
) -> None:
    for item in items:
        if item.known_bar is not None and item.timeframe in known_bars and item.known_bar > known_bars[item.timeframe]:
            raise EvidenceGraphError(f"future evidence beyond as-of bar: {item.id}")
    for level in levels:
        if level.known_bar is not None and level.timeframe in known_bars and level.known_bar > known_bars[level.timeframe]:
            raise EvidenceGraphError(f"future level beyond as-of bar: {level.id}")


def _validate_as_of_timestamp(
    items: Sequence[TechnicalEvidenceItem],
    levels: Sequence[NormalizedLevel],
    as_of_timestamp: Any | None,
) -> None:
    if as_of_timestamp is None:
        return
    for target_id, timestamp in [
        *((item.id, item.timestamp) for item in items),
        *((level.id, level.timestamp) for level in levels),
    ]:
        relation = _timestamp_relation(timestamp, as_of_timestamp)
        if relation is not None and relation > 0:
            raise EvidenceGraphError(f"future evidence beyond as-of timestamp: {target_id}")


def _causal_relation(child: TechnicalEvidenceItem, dependency: TechnicalEvidenceItem) -> bool | None:
    if child.timeframe == dependency.timeframe and child.known_bar is not None and dependency.known_bar is not None:
        return dependency.known_bar <= child.known_bar
    relation = _timestamp_relation(dependency.timestamp, child.timestamp)
    if relation is None:
        return None
    return relation <= 0


def _timestamp_relation(left: Any | None, right: Any | None) -> int | None:
    if left is None or right is None:
        return None
    try:
        left_ts = pd.Timestamp(left)
        right_ts = pd.Timestamp(right)
    except (TypeError, ValueError):
        return None
    if pd.isna(left_ts) or pd.isna(right_ts):
        return None
    left_aware = left_ts.tzinfo is not None
    right_aware = right_ts.tzinfo is not None
    if left_aware != right_aware:
        return None
    if left_aware:
        left_ts = left_ts.tz_convert("UTC")
        right_ts = right_ts.tz_convert("UTC")
    return -1 if left_ts < right_ts else 1 if left_ts > right_ts else 0


def _latest_comparable_timestamp(values: Iterable[Any | None]) -> Any | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    best = present[0]
    for value in present[1:]:
        relation = _timestamp_relation(value, best)
        if relation is None:
            return None
        if relation > 0:
            best = value
    return best


def _find_cycles(
    items: Sequence[TechnicalEvidenceItem],
    by_id: Mapping[str, TechnicalEvidenceItem],
) -> tuple[tuple[str, ...], ...]:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []
    found: set[tuple[str, ...]] = set()

    def walk(node_id: str) -> None:
        if node_id in visited:
            return
        if node_id in visiting:
            if node_id in stack:
                index = stack.index(node_id)
                cycle = tuple(stack[index:] + [node_id])
                found.add(_canonical_cycle(cycle))
            return
        visiting.add(node_id)
        stack.append(node_id)
        for dep_id in by_id[node_id].depends_on:
            if dep_id in by_id:
                walk(dep_id)
        stack.pop()
        visiting.remove(node_id)
        visited.add(node_id)

    for item in sorted(items, key=lambda value: value.id):
        walk(item.id)
    return tuple(sorted(found))


def _canonical_cycle(cycle: tuple[str, ...]) -> tuple[str, ...]:
    nodes = list(cycle[:-1]) if len(cycle) > 1 and cycle[0] == cycle[-1] else list(cycle)
    if not nodes:
        return cycle
    rotations = [tuple(nodes[index:] + nodes[:index]) for index in range(len(nodes))]
    best = min(rotations)
    return best + (best[0],)


def _graph_error_message(report: DependencyValidationReport) -> str:
    parts: list[str] = []
    if report.unresolved_dependencies:
        parts.append(f"unresolved={report.unresolved_dependencies}")
    if report.cycles:
        parts.append(f"cycles={report.cycles}")
    if report.future_dependencies:
        parts.append(f"future={report.future_dependencies}")
    return "invalid Technical Evidence dependency graph: " + "; ".join(parts)


def _dedupe_evidence(values: Iterable[TechnicalEvidenceItem]) -> tuple[TechnicalEvidenceItem, ...]:
    by_id: dict[str, TechnicalEvidenceItem] = {}
    for item in values:
        existing = by_id.get(item.id)
        if existing is not None and existing != item:
            raise ValueError(f"conflicting duplicate evidence id: {item.id}")
        by_id[item.id] = item
    return tuple(_sort_evidence(by_id.values()))


def _dedupe_levels(values: Iterable[NormalizedLevel]) -> tuple[NormalizedLevel, ...]:
    by_id: dict[str, NormalizedLevel] = {}
    for item in values:
        existing = by_id.get(item.id)
        if existing is not None and existing != item:
            raise ValueError(f"conflicting duplicate level id: {item.id}")
        by_id[item.id] = item
    return tuple(_sort_levels(by_id.values()))


def _sort_evidence(values: Iterable[TechnicalEvidenceItem]) -> list[TechnicalEvidenceItem]:
    return sorted(
        values,
        key=lambda item: (
            item.timeframe,
            -1 if item.known_bar is None else item.known_bar,
            item.source_engine,
            item.evidence_type,
            item.id,
        ),
    )


def _sort_levels(values: Iterable[NormalizedLevel]) -> list[NormalizedLevel]:
    return sorted(
        values,
        key=lambda item: (
            item.timeframe,
            -1 if item.known_bar is None else item.known_bar,
            item.source_engine,
            item.level_type,
            item.id,
        ),
    )


def _packet_sort_key(packet: TechnicalEvidencePacket) -> tuple[str, int, str]:
    return (
        packet.timeframe,
        -1 if packet.known_bar is None else packet.known_bar,
        "" if packet.timestamp is None else str(packet.timestamp),
    )


def _stable_id(*parts: Any) -> str:
    body = json.dumps(parts, ensure_ascii=True, separators=(",", ":"), default=str)
    return sha256(body.encode("utf-8")).hexdigest()[:24]
