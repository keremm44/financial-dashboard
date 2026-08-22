from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TargetEvidenceType(StrEnum):
    LIQUIDITY = "LIQUIDITY"
    ORDER_BLOCK = "ORDER_BLOCK"
    FVG = "FVG"
    ENGULFING = "ENGULFING"


class TargetEvidenceFamily(StrEnum):
    STRUCTURAL = "STRUCTURAL"
    SUPPLY_DEMAND = "SUPPLY_DEMAND"
    IMBALANCE = "IMBALANCE"
    REACTION = "REACTION"


class TargetRole(StrEnum):
    MAGNET = "MAGNET"
    SUPPLY = "SUPPLY"
    DEMAND = "DEMAND"
    IMBALANCE = "IMBALANCE"
    REACTION = "REACTION"


class TargetSide(StrEnum):
    ABOVE = "ABOVE"
    BELOW = "BELOW"
    AT_PRICE = "AT_PRICE"


class TargetClusterKind(StrEnum):
    LIQUIDITY_TARGET = "LIQUIDITY_TARGET"
    TECHNICAL_ZONE = "TECHNICAL_ZONE"


class TargetClusterQuality(StrEnum):
    SINGLE = "SINGLE"
    SUPPORTED = "SUPPORTED"
    MULTI_EVIDENCE = "MULTI_EVIDENCE"
    DENSE = "DENSE"


class LiquidityScope(StrEnum):
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"
    UNCLASSIFIED = "UNCLASSIFIED"


@dataclass(frozen=True, slots=True)
class TargetEvidence:
    uid: str
    symbol: str
    timeframe: str
    evidence_type: TargetEvidenceType
    family: TargetEvidenceFamily
    roles: tuple[TargetRole, ...]
    low: float
    high: float
    anchor_price: float | None
    origin_index: int
    origin_time: Any
    confirmed_at: Any
    available_at: Any
    source_state: str
    target_eligible: bool
    native_origin_id: str
    origin_event_id: str
    source_identity: str
    formation_atr: float | None = None
    source_quality: float | None = None
    liquidity_scope: LiquidityScope | None = None

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError("target evidence high must be >= low")
        if not self.uid or not self.source_identity or not self.origin_event_id:
            raise ValueError("target evidence identities must be non-empty")
        if not self.roles:
            raise ValueError("target evidence must expose at least one semantic role")

    @property
    def midpoint(self) -> float:
        return (self.low + self.high) / 2.0

    @property
    def is_liquidity(self) -> bool:
        return self.evidence_type is TargetEvidenceType.LIQUIDITY


@dataclass(frozen=True, slots=True)
class TargetEvidenceSnapshot:
    symbol: str
    timeframe: str
    as_of: Any
    available_at: Any
    current_price: float
    atr: float
    evidence: tuple[TargetEvidence, ...]


@dataclass(frozen=True, slots=True)
class TargetCluster:
    identity: str
    side: TargetSide
    kind: TargetClusterKind
    envelope_low: float
    envelope_high: float
    core_low: float | None
    core_high: float | None
    liquidity_anchor: float | None
    distance_price: float
    distance_percent: float
    distance_atr: float
    evidence: tuple[TargetEvidence, ...]
    raw_source_count: int
    independent_origin_count: int
    independent_family_count: int
    timeframes_present: tuple[str, ...]
    roles_present: tuple[TargetRole, ...]
    quality: TargetClusterQuality

    @property
    def has_liquidity_anchor(self) -> bool:
        return self.liquidity_anchor is not None


@dataclass(frozen=True, slots=True)
class TargetingSnapshot:
    symbol: str
    as_of: Any
    current_price: float
    reference_timeframe: str
    reference_atr: float
    clusters: tuple[TargetCluster, ...]
    nearest_upside_target: TargetCluster | None
    nearest_downside_target: TargetCluster | None
    highest_confluence_upside: TargetCluster | None
    highest_confluence_downside: TargetCluster | None
    nearest_internal_upside_liquidity: TargetEvidence | None = None
    nearest_internal_downside_liquidity: TargetEvidence | None = None
    nearest_external_upside_liquidity: TargetEvidence | None = None
    nearest_external_downside_liquidity: TargetEvidence | None = None


__all__ = [
    "LiquidityScope",
    "TargetCluster",
    "TargetClusterKind",
    "TargetClusterQuality",
    "TargetEvidence",
    "TargetEvidenceFamily",
    "TargetEvidenceSnapshot",
    "TargetEvidenceType",
    "TargetRole",
    "TargetSide",
    "TargetingSnapshot",
]
