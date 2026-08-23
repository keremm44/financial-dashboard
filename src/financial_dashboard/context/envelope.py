from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ContextDomain(StrEnum):
    MARKET_STRUCTURE = "MARKET_STRUCTURE"
    LIQUIDITY = "LIQUIDITY"
    ORDER_BLOCK = "ORDER_BLOCK"
    FVG = "FVG"
    ENGULFING = "ENGULFING"
    SUPPORT_RESISTANCE = "SUPPORT_RESISTANCE"
    VOLUME = "VOLUME"
    PATTERN = "PATTERN"
    VOLATILITY = "VOLATILITY"
    HAM = "HAM"
    STABIL_SUPPORT = "STABIL_SUPPORT"


class ContextDataQuality(StrEnum):
    VALID = "VALID"
    WARMING_UP = "WARMING_UP"
    DATA_LIMITED = "DATA_LIMITED"
    INCOMPLETE = "INCOMPLETE"
    UNSUPPORTED_TIMEFRAME = "UNSUPPORTED_TIMEFRAME"
    UNAVAILABLE = "UNAVAILABLE"


class CausalFamily(StrEnum):
    IMPULSE = "IMPULSE"
    STRUCTURAL_LEVEL = "STRUCTURAL_LEVEL"
    PARTICIPATION = "PARTICIPATION"
    REGIME = "REGIME"
    INDICATOR = "INDICATOR"


class SourceFamily(StrEnum):
    PRICE_GEOMETRY = "PRICE_GEOMETRY"
    PRICE_DERIVED_INDICATOR = "PRICE_DERIVED_INDICATOR"
    VOLUME_SERIES = "VOLUME_SERIES"


_VALID_QUALITY_TOKENS = frozenset({"OK", "DATA_OK", "READY", "VALID", "LOW_PARTICIPATION"})
_WARMUP_QUALITY_TOKENS = frozenset({"WARMUP", "WARMING_UP"})
_LIMITED_QUALITY_TOKENS = frozenset({"LIMITED", "DATA_LIMITED", "SOURCE_GAP"})
_INCOMPLETE_QUALITY_TOKENS = frozenset({"INCOMPLETE", "INCOMPLETE_BAR", "INCOMPLETE_TAIL"})
_UNSUPPORTED_QUALITY_TOKENS = frozenset({"UNSUPPORTED_TIMEFRAME"})
_UNAVAILABLE_QUALITY_TOKENS = frozenset(
    {"INVALID", "DATA_INVALID", "UNAVAILABLE", "VOLUME_UNAVAILABLE", "ERROR"}
)


def _quality_tokens(value: object) -> tuple[str, ...]:
    if isinstance(value, ContextDataQuality):
        return (value.value,)
    candidates: list[str] = []
    name = getattr(value, "name", None)
    native_value = getattr(value, "value", None)
    for candidate in (name, native_value, value if isinstance(value, str) else None):
        if candidate is None:
            continue
        token = str(candidate).strip().upper()
        if token and token not in candidates:
            candidates.append(token)
    return tuple(candidates)


def normalize_context_data_quality(value: object) -> ContextDataQuality:
    """Normalize a native quality token without importing native-domain modules.

    Unknown values fail closed instead of being silently interpreted as neutral or
    valid. Domain-specific projections may extend the mapping explicitly later.
    """

    if isinstance(value, ContextDataQuality):
        return value
    tokens = _quality_tokens(value)
    if any(token in _VALID_QUALITY_TOKENS for token in tokens):
        return ContextDataQuality.VALID
    if any(token in _WARMUP_QUALITY_TOKENS for token in tokens):
        return ContextDataQuality.WARMING_UP
    if any(token in _LIMITED_QUALITY_TOKENS for token in tokens):
        return ContextDataQuality.DATA_LIMITED
    if any(token in _INCOMPLETE_QUALITY_TOKENS for token in tokens):
        return ContextDataQuality.INCOMPLETE
    if any(token in _UNSUPPORTED_QUALITY_TOKENS for token in tokens):
        return ContextDataQuality.UNSUPPORTED_TIMEFRAME
    if any(token in _UNAVAILABLE_QUALITY_TOKENS for token in tokens):
        return ContextDataQuality.UNAVAILABLE
    raise ValueError(f"unsupported native data-quality value: {value!r}")


@dataclass(frozen=True, slots=True)
class FactRef:
    """Minimal immutable reference to one native or projected domain fact.

    ``native_id`` identifies the fact itself. ``lineage_id`` identifies a known
    shared causal origin and is deliberately optional: an unknown lineage must not
    be replaced with a fabricated identity.
    """

    domain: ContextDomain
    fact_type: str
    symbol: str
    timeframe: str
    native_id: str
    native_state: str
    origin_time: Any
    confirmed_at: Any | None
    available_at: Any
    lineage_id: str | None
    causal_family: CausalFamily
    source_family: SourceFamily
    data_quality: ContextDataQuality

    def __post_init__(self) -> None:
        for field_name in ("fact_type", "symbol", "timeframe", "native_id", "native_state"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if self.origin_time is None:
            raise ValueError("origin_time must be known")
        if self.available_at is None:
            raise ValueError("available_at must be known")
        if self.lineage_id is not None and not self.lineage_id.strip():
            raise ValueError("lineage_id must be non-empty when provided")

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None

    def is_available_at(self, as_of: Any) -> bool:
        if as_of is None:
            raise ValueError("as_of must be known")
        try:
            return bool(self.available_at <= as_of)
        except TypeError as exc:
            raise TypeError("available_at and as_of must be comparable") from exc

    @property
    def deterministic_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.domain.value,
            self.symbol,
            self.timeframe,
            self.fact_type,
            self.native_id,
        )


__all__ = [
    "CausalFamily",
    "ContextDataQuality",
    "ContextDomain",
    "FactRef",
    "SourceFamily",
    "normalize_context_data_quality",
]
