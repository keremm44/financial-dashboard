from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .axes import ContextAxes
from .envelope import FactRef
from .lineage import LineageGroup
from .zones import ZoneIntelligenceSnapshot


@dataclass(frozen=True, slots=True)
class KnowledgeBoundary:
    """Explain which facts were knowable at one deterministic decision boundary."""

    as_of: Any
    eligible_fact_ids: tuple[str, ...]
    excluded_future_fact_ids: tuple[str, ...]
    unconfirmed_fact_ids: tuple[str, ...]
    unsupported_contexts: tuple[str, ...] = ()

    @property
    def excluded_count(self) -> int:
        return len(self.excluded_future_fact_ids)

    @property
    def has_future_leakage_candidates(self) -> bool:
        return bool(self.excluded_future_fact_ids)


@dataclass(frozen=True, slots=True)
class CrossDomainContextSnapshot:
    """Immutable single-as-of context output; explicitly not an action signal."""

    symbol: str
    as_of: Any
    anchor_timeframe: str
    current_price: float
    axes: ContextAxes
    zones: ZoneIntelligenceSnapshot
    source_refs: tuple[FactRef, ...]
    lineage_groups: tuple[LineageGroup, ...]
    knowledge_boundary: KnowledgeBoundary

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol must be non-empty")
        if self.as_of is None:
            raise ValueError("as_of must be known")
        if not self.anchor_timeframe.strip():
            raise ValueError("anchor_timeframe must be non-empty")
        if self.axes.anchor_timeframe != self.anchor_timeframe.strip().lower():
            raise ValueError("axes anchor_timeframe must match snapshot anchor_timeframe")
        if self.zones.symbol != self.symbol:
            raise ValueError("zone snapshot symbol must match context snapshot symbol")
        if self.zones.as_of != self.as_of:
            raise ValueError("zone snapshot as_of must match context snapshot as_of")
        for ref in self.source_refs:
            if ref.symbol != self.symbol:
                raise ValueError("all source refs must match context snapshot symbol")
            if not ref.is_available_at(self.as_of):
                raise ValueError("source_refs cannot contain facts unavailable at snapshot as_of")


def _unique_refs(facts: Iterable[FactRef]) -> tuple[FactRef, ...]:
    unique = {ref.deterministic_key: ref for ref in facts}
    return tuple(sorted(unique.values(), key=lambda ref: ref.deterministic_key))


def _boundary_from_unique_refs(
    refs: tuple[FactRef, ...],
    *,
    as_of: Any,
    unsupported_contexts: Iterable[str] = (),
) -> KnowledgeBoundary:
    eligible_ids: list[str] = []
    future_ids: list[str] = []
    unconfirmed_ids: list[str] = []
    for ref in refs:
        if ref.is_available_at(as_of):
            eligible_ids.append(ref.native_id)
        else:
            future_ids.append(ref.native_id)
        if ref.confirmed_at is None:
            unconfirmed_ids.append(ref.native_id)
    unsupported = tuple(
        sorted(
            {
                str(item).strip()
                for item in unsupported_contexts
                if str(item).strip()
            }
        )
    )
    return KnowledgeBoundary(
        as_of=as_of,
        eligible_fact_ids=tuple(eligible_ids),
        excluded_future_fact_ids=tuple(future_ids),
        unconfirmed_fact_ids=tuple(unconfirmed_ids),
        unsupported_contexts=unsupported,
    )


def evaluate_knowledge_boundary(
    facts: Iterable[FactRef],
    *,
    as_of: Any,
    unsupported_contexts: Iterable[str] = (),
) -> KnowledgeBoundary:
    if as_of is None:
        raise ValueError("as_of must be known")
    return _boundary_from_unique_refs(
        _unique_refs(facts),
        as_of=as_of,
        unsupported_contexts=unsupported_contexts,
    )


def eligible_fact_refs(facts: Iterable[FactRef], *, as_of: Any) -> tuple[FactRef, ...]:
    """Apply only the knowledge-time boundary; semantic authority stays elsewhere."""

    if as_of is None:
        raise ValueError("as_of must be known")
    return tuple(ref for ref in _unique_refs(facts) if ref.is_available_at(as_of))


def build_context_snapshot(
    *,
    symbol: str,
    as_of: Any,
    anchor_timeframe: str,
    axes: ContextAxes,
    zones: ZoneIntelligenceSnapshot,
    all_fact_refs: Iterable[FactRef],
    lineage_groups: Iterable[LineageGroup] = (),
    unsupported_contexts: Iterable[str] = (),
) -> CrossDomainContextSnapshot:
    """Build one context snapshot while canonicalizing fact refs only once."""

    if as_of is None:
        raise ValueError("as_of must be known")
    refs = _unique_refs(all_fact_refs)
    boundary = _boundary_from_unique_refs(
        refs,
        as_of=as_of,
        unsupported_contexts=unsupported_contexts,
    )
    eligible = tuple(ref for ref in refs if ref.is_available_at(as_of))
    groups = tuple(sorted(lineage_groups, key=lambda group: group.lineage_id))
    return CrossDomainContextSnapshot(
        symbol=symbol,
        as_of=as_of,
        anchor_timeframe=anchor_timeframe.strip().lower(),
        current_price=float(zones.current_price),
        axes=axes,
        zones=zones,
        source_refs=eligible,
        lineage_groups=groups,
        knowledge_boundary=boundary,
    )


__all__ = [
    "CrossDomainContextSnapshot",
    "KnowledgeBoundary",
    "build_context_snapshot",
    "eligible_fact_refs",
    "evaluate_knowledge_boundary",
]
