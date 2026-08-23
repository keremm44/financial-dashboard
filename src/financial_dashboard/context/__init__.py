"""Authority-preserving cross-domain context contracts.

The package is read-only with respect to native domain engines. It exposes common
fact references, causal/correlation metadata, and thin domain read projections;
BUY/SELL and action authority are intentionally outside this package.
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
from .projections import (
    HamProjection,
    LiquidityProjection,
    ParticipationProjection,
    PatternProjection,
    ReactionEvidenceProjection,
    StabilSupportProjection,
    StructuralFactsProjection,
    VolatilityProjection,
    project_ham,
    project_liquidity,
    project_participation,
    project_pattern,
    project_reaction_evidence,
    project_stabil_support,
    project_structural_facts,
    project_volatility,
)

__all__ = [
    "CausalFamily",
    "ContextDataQuality",
    "ContextDomain",
    "FactRef",
    "HamProjection",
    "LineageGroup",
    "LiquidityProjection",
    "ParticipationProjection",
    "PatternProjection",
    "ReactionEvidenceProjection",
    "SourceFamily",
    "StabilSupportProjection",
    "StructuralFactsProjection",
    "VolatilityProjection",
    "build_lineage_groups",
    "families_for",
    "known_independent_origin_count",
    "lineage_id_from_origin_event",
    "normalize_context_data_quality",
    "project_ham",
    "project_liquidity",
    "project_participation",
    "project_pattern",
    "project_reaction_evidence",
    "project_stabil_support",
    "project_structural_facts",
    "project_volatility",
    "unknown_lineage_refs",
]
