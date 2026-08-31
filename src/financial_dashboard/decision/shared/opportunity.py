"""Shared compatibility facade for canonical opportunity ownership."""

from ..opportunity import (
    OpportunityAssessment,
    OpportunityCalibration,
    OpportunityState,
    assess_opportunity,
)

__all__ = [
    "OpportunityAssessment",
    "OpportunityCalibration",
    "OpportunityState",
    "assess_opportunity",
]
