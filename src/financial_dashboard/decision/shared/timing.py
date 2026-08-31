"""Shared compatibility facade for canonical timing ownership."""

from ..timing import (
    SetupTriggerAssessment,
    SetupTriggerState,
    TimingAssessment,
    TimingState,
    assess_setup_trigger,
    assess_timing,
)

__all__ = [
    "SetupTriggerAssessment",
    "SetupTriggerState",
    "TimingAssessment",
    "TimingState",
    "assess_setup_trigger",
    "assess_timing",
]
