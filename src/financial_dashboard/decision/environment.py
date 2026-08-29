from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from financial_dashboard.context.envelope import (
    ContextDataQuality,
    FactRef,
    normalize_context_data_quality,
)
from financial_dashboard.context.volatility_environment_projection import (
    ExpansionCharacter,
    VolatilityEnvironmentProjection,
    VolatilityRangeRegime,
)

from .structural import StructuralDirection


class EnvironmentAlignment(StrEnum):
    ALIGNED = "ALIGNED"
    OPPOSING = "OPPOSING"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class EnvironmentRisk(StrEnum):
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    HARD_BLOCK = "HARD_BLOCK"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class EnvironmentAssessment:
    regime: VolatilityRangeRegime
    character: ExpansionCharacter
    alignment: EnvironmentAlignment
    risk: EnvironmentRisk
    data_quality: ContextDataQuality
    reasons: tuple[str, ...]
    source_refs: tuple[FactRef, ...]


_E = TypeVar("_E", bound=StrEnum)


def _legacy_enum(enum_type: type[_E], value: object, fallback: _E) -> _E:
    """Hydrate enum-like values from older frozen DecisionInput pickles.

    Old caches can contain the string representation produced by projection
    serialization while current in-memory projections contain the StrEnum member.
    Decision consumers must accept both without requiring expensive domain replay.
    Unknown tokens fail closed to the supplied UNAVAILABLE member.
    """

    if isinstance(value, enum_type):
        return value
    token = getattr(value, "value", value)
    try:
        return enum_type(str(token))
    except (TypeError, ValueError):
        return fallback


def _direction_value(side: StructuralDirection) -> int:
    if side is StructuralDirection.LONG:
        return 1
    if side is StructuralDirection.SHORT:
        return -1
    return 0


def assess_environment(
    side: StructuralDirection,
    volatility: VolatilityEnvironmentProjection | None,
    *,
    timeframe: str,
) -> EnvironmentAssessment:
    """Interpret native volatility regime relative to Structure without changing it.

    V1 has only two explicit risk policies here: SHOCK is marked for the later hard
    gate layer and UNSTABLE_CONFLICT is an elevated soft risk. Other native
    characters remain visible but are not assigned new severity heuristics yet.
    Legacy frozen projections are normalized at this decision boundary so old
    domain caches remain reusable after enum-backed projection upgrades.
    """

    normalized = timeframe.strip().lower()
    if volatility is None:
        return EnvironmentAssessment(
            VolatilityRangeRegime.UNAVAILABLE,
            ExpansionCharacter.UNAVAILABLE,
            EnvironmentAlignment.UNKNOWN,
            EnvironmentRisk.UNKNOWN,
            ContextDataQuality.UNAVAILABLE,
            (f"VOLATILITY_UNAVAILABLE:{normalized}",),
            (),
        )
    try:
        row = volatility.for_timeframe(normalized)
    except KeyError:
        return EnvironmentAssessment(
            VolatilityRangeRegime.UNAVAILABLE,
            ExpansionCharacter.UNAVAILABLE,
            EnvironmentAlignment.UNKNOWN,
            EnvironmentRisk.UNKNOWN,
            ContextDataQuality.UNAVAILABLE,
            (f"VOLATILITY_TIMEFRAME_UNAVAILABLE:{normalized}",),
            (),
        )

    quality = normalize_context_data_quality(row.ref.data_quality)
    regime = _legacy_enum(
        VolatilityRangeRegime,
        row.range_regime,
        VolatilityRangeRegime.UNAVAILABLE,
    )
    character = _legacy_enum(
        ExpansionCharacter,
        row.expansion_character,
        ExpansionCharacter.UNAVAILABLE,
    )
    if quality is not ContextDataQuality.VALID or regime is VolatilityRangeRegime.UNAVAILABLE:
        return EnvironmentAssessment(
            regime,
            character,
            EnvironmentAlignment.UNKNOWN,
            EnvironmentRisk.UNKNOWN,
            quality,
            (f"VOLATILITY_DATA_{quality.value}:{normalized}",),
            (row.ref,),
        )

    direction = _direction_value(side)
    if direction == 0 or row.expansion_direction == 0:
        alignment = EnvironmentAlignment.NEUTRAL if direction != 0 else EnvironmentAlignment.UNKNOWN
    elif row.expansion_direction == direction:
        alignment = EnvironmentAlignment.ALIGNED
    else:
        alignment = EnvironmentAlignment.OPPOSING

    if regime is VolatilityRangeRegime.SHOCK:
        risk = EnvironmentRisk.HARD_BLOCK
        reasons = ("VOLATILITY_SHOCK",)
    elif character is ExpansionCharacter.UNSTABLE_CONFLICT:
        risk = EnvironmentRisk.ELEVATED
        reasons = ("VOLATILITY_UNSTABLE_CONFLICT",)
    else:
        risk = EnvironmentRisk.NORMAL
        reasons = (
            f"VOLATILITY_REGIME:{regime.value}",
            f"VOLATILITY_CHARACTER:{character.value}",
        )

    if alignment is EnvironmentAlignment.OPPOSING:
        reasons = (*reasons, "VOLATILITY_EXPANSION_OPPOSES_STRUCTURE")
    elif alignment is EnvironmentAlignment.ALIGNED:
        reasons = (*reasons, "VOLATILITY_EXPANSION_ALIGNS_STRUCTURE")

    return EnvironmentAssessment(
        regime,
        character,
        alignment,
        risk,
        quality,
        reasons,
        (row.ref,),
    )


__all__ = [
    "EnvironmentAlignment",
    "EnvironmentAssessment",
    "EnvironmentRisk",
    "assess_environment",
]
