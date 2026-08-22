from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .models import LiquidityScope, TargetEvidence, TargetEvidenceType, TargetSide


class SemanticRole(StrEnum):
    DRAW_CANDIDATE = "DRAW_CANDIDATE"
    REACTION_ZONE = "REACTION_ZONE"
    BARRIER = "BARRIER"
    IMBALANCE = "IMBALANCE"
    CONFIRMATION = "CONFIRMATION"
    STRUCTURAL_CONTEXT = "STRUCTURAL_CONTEXT"


class BehaviorDirection(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class ObjectiveKind(StrEnum):
    LIQUIDITY = "LIQUIDITY"
    FVG_REFILL = "FVG_REFILL"


class ReactionKind(StrEnum):
    ORDER_BLOCK = "ORDER_BLOCK"
    FVG = "FVG"
    SUPPORT_RESISTANCE = "SUPPORT_RESISTANCE"


class ConfirmationKind(StrEnum):
    ENGULFING = "ENGULFING"


class ArrivalPosition(StrEnum):
    AHEAD = "AHEAD"
    AT_OBJECTIVE = "AT_OBJECTIVE"
    BEYOND = "BEYOND"
    CURRENT = "CURRENT"
    UNRELATED = "UNRELATED"


class ArrivalState(StrEnum):
    NO_ACTIVE_OBJECTIVE = "NO_ACTIVE_OBJECTIVE"
    OBJECTIVE_ONLY = "OBJECTIVE_ONLY"
    OBJECTIVE_WITH_REACTION = "OBJECTIVE_WITH_REACTION"
    MULTI_DOMAIN_REACTION = "MULTI_DOMAIN_REACTION"
    CONFLICTING_ARRIVAL = "CONFLICTING_ARRIVAL"
    REACTION_ZONE_ONLY = "REACTION_ZONE_ONLY"
    IN_REACTION_ZONE = "IN_REACTION_ZONE"
    AT_OBJECTIVE = "AT_OBJECTIVE"


@dataclass(frozen=True, slots=True)
class Objective:
    identity: str
    kind: ObjectiveKind
    side: TargetSide
    low: float
    high: float
    anchor_price: float
    source: TargetEvidence
    liquidity_scope: LiquidityScope | None = None

    def __post_init__(self) -> None:
        if self.kind is ObjectiveKind.LIQUIDITY and self.source.evidence_type is not TargetEvidenceType.LIQUIDITY:
            raise ValueError("liquidity objective must originate from liquidity evidence")
        if self.source.evidence_type in {TargetEvidenceType.ORDER_BLOCK, TargetEvidenceType.ENGULFING, TargetEvidenceType.SUPPORT_RESISTANCE}:
            raise ValueError(f"{self.source.evidence_type.value} cannot be an objective")


@dataclass(frozen=True, slots=True)
class ReactionZone:
    identity: str
    kind: ReactionKind
    side: TargetSide
    behavior: BehaviorDirection
    low: float
    high: float
    source: TargetEvidence
    roles: tuple[SemanticRole, ...]

    def __post_init__(self) -> None:
        if self.source.evidence_type is TargetEvidenceType.ENGULFING:
            raise ValueError("engulfing cannot create a reaction zone")
        if SemanticRole.REACTION_ZONE not in self.roles:
            raise ValueError("reaction zone must expose REACTION_ZONE role")


@dataclass(frozen=True, slots=True)
class Confirmation:
    identity: str
    kind: ConfirmationKind
    side: TargetSide
    behavior: BehaviorDirection
    low: float
    high: float
    source: TargetEvidence

    def __post_init__(self) -> None:
        if self.source.evidence_type is not TargetEvidenceType.ENGULFING:
            raise ValueError("confirmation currently supports engulfing evidence only")


@dataclass(frozen=True, slots=True)
class PositionedReaction:
    zone: ReactionZone
    position: ArrivalPosition
    independent_from_objective: bool


@dataclass(frozen=True, slots=True)
class ArrivalContext:
    objective: Objective
    state: ArrivalState
    reactions_ahead: tuple[PositionedReaction, ...]
    reactions_at: tuple[PositionedReaction, ...]
    reactions_beyond: tuple[PositionedReaction, ...]
    current_reactions: tuple[PositionedReaction, ...]
    confirmations: tuple[Confirmation, ...]
    independent_reaction_origins: int
    reaction_types: tuple[ReactionKind, ...]


@dataclass(frozen=True, slots=True)
class SemanticTargetingSnapshot:
    symbol: str
    as_of: Any
    current_price: float
    reference_atr: float
    objectives: tuple[Objective, ...]
    reaction_zones: tuple[ReactionZone, ...]
    confirmations: tuple[Confirmation, ...]
    nearest_upside_objective: Objective | None
    nearest_downside_objective: Objective | None
    upside_arrival: ArrivalContext | None
    downside_arrival: ArrivalContext | None
    state: ArrivalState


__all__ = [
    "ArrivalContext",
    "ArrivalPosition",
    "ArrivalState",
    "BehaviorDirection",
    "Confirmation",
    "ConfirmationKind",
    "Objective",
    "ObjectiveKind",
    "PositionedReaction",
    "ReactionKind",
    "ReactionZone",
    "SemanticRole",
    "SemanticTargetingSnapshot",
]
