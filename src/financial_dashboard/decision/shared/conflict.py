"""Shared compatibility facade for canonical conflict ownership."""

from ..conflict import (
    ConflictAssessment,
    ConflictFamilyEvidence,
    ConflictSeverity,
    ConflictState,
    assess_conflict,
)

__all__ = [
    "ConflictAssessment",
    "ConflictFamilyEvidence",
    "ConflictSeverity",
    "ConflictState",
    "assess_conflict",
]
