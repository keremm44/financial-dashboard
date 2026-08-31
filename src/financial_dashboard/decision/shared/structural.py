"""Shared compatibility facade for canonical structural ownership."""

from ..structural import (
    DecisionHorizon,
    HorizonRelation,
    HorizonStructuralSnapshot,
    StructuralAssessment,
    StructuralDirection,
    ThesisState,
    assess_long_term_structure,
    assess_short_term_structure,
    build_horizon_structural_snapshot,
    classify_horizon_relation,
)

__all__ = [
    "DecisionHorizon",
    "HorizonRelation",
    "HorizonStructuralSnapshot",
    "StructuralAssessment",
    "StructuralDirection",
    "ThesisState",
    "assess_long_term_structure",
    "assess_short_term_structure",
    "build_horizon_structural_snapshot",
    "classify_horizon_relation",
]
