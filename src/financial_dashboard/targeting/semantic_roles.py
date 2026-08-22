from __future__ import annotations

from .models import TargetEvidence, TargetEvidenceType, TargetRole, TargetSide
from .semantic_models import (
    BehaviorDirection,
    Confirmation,
    ConfirmationKind,
    Objective,
    ObjectiveKind,
    ReactionKind,
    ReactionZone,
    SemanticRole,
)


def evidence_side(item: TargetEvidence, current_price: float) -> TargetSide:
    if item.low > current_price:
        return TargetSide.ABOVE
    if item.high < current_price:
        return TargetSide.BELOW
    return TargetSide.AT_PRICE


def evidence_behavior(item: TargetEvidence) -> BehaviorDirection:
    roles = set(item.roles)
    if TargetRole.SUPPLY in roles:
        return BehaviorDirection.BEARISH
    if TargetRole.DEMAND in roles:
        return BehaviorDirection.BULLISH
    return BehaviorDirection.NEUTRAL


def semantic_roles(item: TargetEvidence) -> tuple[SemanticRole, ...]:
    if item.evidence_type is TargetEvidenceType.LIQUIDITY:
        return (SemanticRole.DRAW_CANDIDATE, SemanticRole.STRUCTURAL_CONTEXT)
    if item.evidence_type is TargetEvidenceType.ORDER_BLOCK:
        return (SemanticRole.REACTION_ZONE, SemanticRole.BARRIER)
    if item.evidence_type is TargetEvidenceType.FVG:
        return (SemanticRole.REACTION_ZONE, SemanticRole.IMBALANCE)
    if item.evidence_type is TargetEvidenceType.ENGULFING:
        return (SemanticRole.CONFIRMATION,)
    if item.evidence_type is TargetEvidenceType.SUPPORT_RESISTANCE:
        return (SemanticRole.REACTION_ZONE, SemanticRole.BARRIER, SemanticRole.STRUCTURAL_CONTEXT)
    return (SemanticRole.STRUCTURAL_CONTEXT,)


def is_objective_eligible(item: TargetEvidence) -> bool:
    # Legacy `target_eligible` is treated only as native lifecycle activity here;
    # semantic authority comes from evidence type + this derived axis.
    return item.evidence_type is TargetEvidenceType.LIQUIDITY and bool(item.target_eligible)


def is_reaction_eligible(item: TargetEvidence) -> bool:
    return (
        item.evidence_type
        in {
            TargetEvidenceType.ORDER_BLOCK,
            TargetEvidenceType.FVG,
            TargetEvidenceType.SUPPORT_RESISTANCE,
        }
        and bool(item.target_eligible)
    )


def is_confirmation_eligible(item: TargetEvidence) -> bool:
    return item.evidence_type is TargetEvidenceType.ENGULFING and bool(item.target_eligible)


def to_objective(item: TargetEvidence, *, current_price: float) -> Objective | None:
    # Phase-1 policy: only active/tested Liquidity is an objective. FVG refill
    # stays schema-supported but disabled until replay validates it independently.
    if not is_objective_eligible(item):
        return None
    anchor = float(item.anchor_price if item.anchor_price is not None else item.midpoint)
    return Objective(
        identity=f"OBJ:{item.uid}",
        kind=ObjectiveKind.LIQUIDITY,
        side=evidence_side(item, current_price),
        low=float(item.low),
        high=float(item.high),
        anchor_price=anchor,
        source=item,
        liquidity_scope=item.liquidity_scope,
    )


def to_reaction_zone(item: TargetEvidence, *, current_price: float) -> ReactionZone | None:
    if not is_reaction_eligible(item):
        return None
    if item.evidence_type is TargetEvidenceType.ORDER_BLOCK:
        kind = ReactionKind.ORDER_BLOCK
    elif item.evidence_type is TargetEvidenceType.FVG:
        kind = ReactionKind.FVG
    else:
        kind = ReactionKind.SUPPORT_RESISTANCE
    return ReactionZone(
        identity=f"RX:{item.uid}",
        kind=kind,
        side=evidence_side(item, current_price),
        behavior=evidence_behavior(item),
        low=float(item.low),
        high=float(item.high),
        source=item,
        roles=semantic_roles(item),
    )


def to_confirmation(item: TargetEvidence, *, current_price: float) -> Confirmation | None:
    if not is_confirmation_eligible(item):
        return None
    return Confirmation(
        identity=f"CF:{item.uid}",
        kind=ConfirmationKind.ENGULFING,
        side=evidence_side(item, current_price),
        behavior=evidence_behavior(item),
        low=float(item.low),
        high=float(item.high),
        source=item,
    )


def reaction_direction(zone: ReactionZone) -> str:
    return zone.behavior.value


__all__ = [
    "evidence_behavior",
    "evidence_side",
    "is_confirmation_eligible",
    "is_objective_eligible",
    "is_reaction_eligible",
    "reaction_direction",
    "semantic_roles",
    "to_confirmation",
    "to_objective",
    "to_reaction_zone",
]
