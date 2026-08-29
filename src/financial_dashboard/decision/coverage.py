from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from financial_dashboard.context.envelope import (
    ContextDataQuality,
    normalize_context_data_quality,
)


class CoverageFamily(StrEnum):
    STRUCTURE = "STRUCTURE"
    STABIL = "STABIL"
    LIQUIDITY = "LIQUIDITY"
    REACTION = "REACTION"
    PARTICIPATION = "PARTICIPATION"
    VOLATILITY = "VOLATILITY"
    PATTERN = "PATTERN"
    HAM = "HAM"
    TARGETING = "TARGETING"


@dataclass(frozen=True, slots=True)
class CoverageAssessment:
    valid_fraction: float
    observed_fraction: float
    critical_path_missing: tuple[CoverageFamily, ...]
    degraded_families: tuple[CoverageFamily, ...]
    unavailable_families: tuple[CoverageFamily, ...]
    valid_families: tuple[CoverageFamily, ...]


def assess_coverage(
    family_quality: Mapping[CoverageFamily, ContextDataQuality],
    *,
    expected_families: tuple[CoverageFamily, ...],
    critical_families: tuple[CoverageFamily, ...] = (),
) -> CoverageAssessment:
    """Describe evidence availability without converting missing facts to neutral.

    Legacy frozen timelines can expose enum-backed qualities as strings. Every
    family is normalized here before breadth/critical-path classification so a
    stale representation cannot silently turn VALID evidence into unavailable.
    """

    expected = tuple(dict.fromkeys(expected_families))
    if not expected:
        raise ValueError("expected_families must be non-empty")
    critical = tuple(dict.fromkeys(critical_families))
    if any(item not in expected for item in critical):
        raise ValueError("critical_families must be a subset of expected_families")

    qualities = {
        family: normalize_context_data_quality(
            family_quality.get(family, ContextDataQuality.UNAVAILABLE)
        )
        for family in expected
    }
    valid: list[CoverageFamily] = []
    degraded: list[CoverageFamily] = []
    unavailable: list[CoverageFamily] = []

    for family in expected:
        quality = qualities[family]
        if quality is ContextDataQuality.VALID:
            valid.append(family)
        elif quality in {ContextDataQuality.DATA_LIMITED, ContextDataQuality.INCOMPLETE}:
            degraded.append(family)
        else:
            unavailable.append(family)

    observed = len(valid) + len(degraded)
    critical_missing = tuple(
        family for family in critical if qualities[family] is not ContextDataQuality.VALID
    )
    total = len(expected)
    return CoverageAssessment(
        valid_fraction=len(valid) / total,
        observed_fraction=observed / total,
        critical_path_missing=critical_missing,
        degraded_families=tuple(degraded),
        unavailable_families=tuple(unavailable),
        valid_families=tuple(valid),
    )


__all__ = ["CoverageAssessment", "CoverageFamily", "assess_coverage"]
