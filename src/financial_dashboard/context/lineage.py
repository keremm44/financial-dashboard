from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from .envelope import CausalFamily, ContextDomain, FactRef, SourceFamily


class OriginEventLike(Protocol):
    origin_event_id: str


def lineage_id_from_origin_event(item: OriginEventLike) -> str | None:
    """Return an existing origin-event identity without inventing one.

    Targeting's ``deduplicate_origin_events`` remains the authority that decides
    whether same-timeframe TargetEvidence records share an ``origin_event_id``.
    This bridge only reads that result.
    """

    value = getattr(item, "origin_event_id", None)
    if value is None:
        return None
    lineage_id = str(value).strip()
    return lineage_id or None


def families_for(
    domain: ContextDomain,
    *,
    fact_type: str | None = None,
) -> tuple[CausalFamily, SourceFamily]:
    """Return conservative correlation metadata for a projected fact.

    ``source_family`` is metadata for explainability/representative selection; it
    is not a numerical correlation penalty and never creates a vote.
    """

    if domain in {
        ContextDomain.MARKET_STRUCTURE,
        ContextDomain.LIQUIDITY,
        ContextDomain.SUPPORT_RESISTANCE,
        ContextDomain.STABIL_SUPPORT,
    }:
        return CausalFamily.STRUCTURAL_LEVEL, SourceFamily.PRICE_GEOMETRY
    if domain in {ContextDomain.ORDER_BLOCK, ContextDomain.FVG, ContextDomain.ENGULFING}:
        return CausalFamily.IMPULSE, SourceFamily.PRICE_GEOMETRY
    if domain is ContextDomain.VOLUME:
        return CausalFamily.PARTICIPATION, SourceFamily.VOLUME_SERIES
    if domain is ContextDomain.PATTERN:
        return CausalFamily.REGIME, SourceFamily.PRICE_GEOMETRY
    if domain is ContextDomain.VOLATILITY:
        return CausalFamily.REGIME, SourceFamily.PRICE_DERIVED_INDICATOR
    if domain is ContextDomain.HAM:
        normalized = "" if fact_type is None else fact_type.strip().upper()
        if normalized.startswith("FLOW") or "FLOW" in normalized:
            return CausalFamily.PARTICIPATION, SourceFamily.VOLUME_SERIES
        return CausalFamily.INDICATOR, SourceFamily.PRICE_DERIVED_INDICATOR
    raise ValueError(f"no lineage-family mapping for domain: {domain!r}")


@dataclass(frozen=True, slots=True)
class LineageGroup:
    lineage_id: str
    causal_family: CausalFamily
    members: tuple[FactRef, ...]

    def __post_init__(self) -> None:
        if not self.lineage_id.strip():
            raise ValueError("lineage_id must be non-empty")
        if not self.members:
            raise ValueError("lineage group must contain at least one member")
        if any(member.lineage_id != self.lineage_id for member in self.members):
            raise ValueError("all lineage-group members must share lineage_id")
        if any(member.causal_family is not self.causal_family for member in self.members):
            raise ValueError("all lineage-group members must share causal_family")

    @property
    def source_families(self) -> tuple[SourceFamily, ...]:
        return tuple(sorted({member.source_family for member in self.members}, key=lambda item: item.value))


def build_lineage_groups(refs: Iterable[FactRef]) -> tuple[LineageGroup, ...]:
    """Group only facts with an explicitly known lineage.

    Facts with ``lineage_id=None`` are intentionally left ungrouped. Treating them
    as independent would convert missing causal knowledge into false independence.
    """

    buckets: dict[tuple[str, CausalFamily], list[FactRef]] = {}
    for ref in refs:
        if ref.lineage_id is None:
            continue
        key = (ref.lineage_id, ref.causal_family)
        buckets.setdefault(key, []).append(ref)

    groups = [
        LineageGroup(
            lineage_id=lineage_id,
            causal_family=causal_family,
            members=tuple(sorted(members, key=lambda member: member.deterministic_key)),
        )
        for (lineage_id, causal_family), members in buckets.items()
    ]
    return tuple(sorted(groups, key=lambda group: (group.causal_family.value, group.lineage_id)))


def unknown_lineage_refs(refs: Iterable[FactRef]) -> tuple[FactRef, ...]:
    return tuple(sorted((ref for ref in refs if ref.lineage_id is None), key=lambda ref: ref.deterministic_key))


def known_independent_origin_count(refs: Iterable[FactRef]) -> int:
    """Count only explicit lineage identities; unknown lineage is not a vote."""

    return len({ref.lineage_id for ref in refs if ref.lineage_id is not None})


__all__ = [
    "LineageGroup",
    "OriginEventLike",
    "build_lineage_groups",
    "families_for",
    "known_independent_origin_count",
    "lineage_id_from_origin_event",
    "unknown_lineage_refs",
]
