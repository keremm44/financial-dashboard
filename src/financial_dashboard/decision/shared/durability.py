"""Shared compatibility facade for canonical Stabil durability ownership."""

from ..durability import DurabilityAssessment, DurabilityState, assess_durability

__all__ = [
    "DurabilityAssessment",
    "DurabilityState",
    "assess_durability",
]
