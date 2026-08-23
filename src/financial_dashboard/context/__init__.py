"""Authority-preserving cross-domain context contracts.

The package is read-only with respect to native domain engines. It exposes common
fact references and causal/correlation metadata only; BUY/SELL and action authority
are intentionally outside this package.
"""

from .envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
    normalize_context_data_quality,
)
from .lineage import (
    LineageGroup,
    build_lineage_groups,
    families_for,
    known_independent_origin_count,
    lineage_id_from_origin_event,
    unknown_lineage_refs,
)

__all__ = [
    "CausalFamily",
    "ContextDataQuality",
    "ContextDomain",
    "FactRef",
    "LineageGroup",
    "SourceFamily",
    "build_lineage_groups",
    "families_for",
    "known_independent_origin_count",
    "lineage_id_from_origin_event",
    "normalize_context_data_quality",
    "unknown_lineage_refs",
]
