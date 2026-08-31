"""Shared compatibility facade for canonical environment ownership."""

from ..environment import (
    EnvironmentAlignment,
    EnvironmentAssessment,
    EnvironmentRisk,
    assess_environment,
)

__all__ = [
    "EnvironmentAlignment",
    "EnvironmentAssessment",
    "EnvironmentRisk",
    "assess_environment",
]
