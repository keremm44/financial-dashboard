from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .models import TargetEvidence, TargetSide
from .semantic_models import (
    ArrivalContext,
    ArrivalPosition,
    ArrivalState,
    Objective,
    PositionedReaction,
    ReactionKind,
    ReactionZone,
    SemanticTargetingSnapshot,
)
from .semantic_roles import reaction_direction, to_confirmation, to_objective, to_reaction_zone


def _interval_gap(a_low: float, a_high: float, b_low: float, b_high: float) -> float:
    if a_high < b_low:
        return b_low - a_high
    if b_high < a_low:
        return a_low - b_high
    return 0.0


def _distance_to_objective(objective: Objective, current_price: float) -> float:
    if objective.side is TargetSide.ABOVE:
        return max(0.0, objective.low - current_price)
    if objective.side is TargetSide.BELOW:
        return max(0.0, current_price - objective.high)
    return 0.0


def _reaction_position(
    zone: ReactionZone,
    objective: Objective,
    *,
    current_price: float,
    tolerance_price: float,
) -> ArrivalPosition:
    if zone.low <= current_price <= zone.high:
        return ArrivalPosition.CURRENT
    if _interval_gap(zone.low, zone.high, objective.low, objective.high) <= tolerance_price:
        return ArrivalPosition.AT_OBJECTIVE

    if objective.side is TargetSide.ABOVE:
        if zone.high < objective.low and zone.high >= current_price:
            return ArrivalPosition.AHEAD
        if zone.low > objective.high:
            return ArrivalPosition.BEYOND
    elif objective.side is TargetSide.BELOW:
        if zone.low > objective.high and zone.low <= current_price:
            return ArrivalPosition.AHEAD
        if zone.high < objective.low:
            return ArrivalPosition.BEYOND
    return ArrivalPosition.UNRELATED


def _arrival_state(
    objective: Objective,
    *,
    current_price: float,
    at: tuple[PositionedReaction, ...],
    ahead: tuple[PositionedReaction, ...],
    current: tuple[PositionedReaction, ...],
) -> ArrivalState:
    if objective.low <= current_price <= objective.high:
        return ArrivalState.AT_OBJECTIVE
    if current:
        return ArrivalState.IN_REACTION_ZONE
    all_reactions = (*at, *ahead)
    if not all_reactions:
        return ArrivalState.OBJECTIVE_ONLY

    directions = {reaction_direction(item.zone) for item in all_reactions} - {"NEUTRAL"}
    if len(directions) > 1:
        return ArrivalState.CONFLICTING_ARRIVAL
    origins = {item.zone.source.origin_event_id for item in all_reactions}
    kinds = {item.zone.kind for item in all_reactions}
    if len(origins) > 1 and len(kinds) > 1:
        return ArrivalState.MULTI_DOMAIN_REACTION
    return ArrivalState.OBJECTIVE_WITH_REACTION


def build_arrival_context(
    objective: Objective,
    *,
    current_price: float,
    reference_atr: float,
    reactions: Iterable[ReactionZone],
    confirmations,
    proximity_atr: float = 0.25,
) -> ArrivalContext:
    tolerance = max(float(reference_atr), 1e-12) * max(float(proximity_atr), 0.0)
    positioned: dict[ArrivalPosition, list[PositionedReaction]] = defaultdict(list)
    for zone in reactions:
        position = _reaction_position(
            zone,
            objective,
            current_price=current_price,
            tolerance_price=tolerance,
        )
        if position is ArrivalPosition.UNRELATED:
            continue
        positioned[position].append(
            PositionedReaction(
                zone=zone,
                position=position,
                independent_from_objective=(zone.source.origin_event_id != objective.source.origin_event_id),
            )
        )

    def ordered(position: ArrivalPosition) -> tuple[PositionedReaction, ...]:
        return tuple(
            sorted(
                positioned.get(position, ()),
                key=lambda item: (item.zone.low, item.zone.high, item.zone.identity),
            )
        )

    ahead = ordered(ArrivalPosition.AHEAD)
    at = ordered(ArrivalPosition.AT_OBJECTIVE)
    beyond = ordered(ArrivalPosition.BEYOND)
    current = ordered(ArrivalPosition.CURRENT)
    active = (*ahead, *at, *current)
    independent_origins = len(
        {
            item.zone.source.origin_event_id
            for item in active
            if item.independent_from_objective
        }
    )
    reaction_types = tuple(sorted({item.zone.kind for item in active}, key=lambda kind: kind.value))
    return ArrivalContext(
        objective=objective,
        state=_arrival_state(
            objective,
            current_price=current_price,
            at=at,
            ahead=ahead,
            current=current,
        ),
        reactions_ahead=ahead,
        reactions_at=at,
        reactions_beyond=beyond,
        current_reactions=current,
        confirmations=tuple(confirmations),
        independent_reaction_origins=independent_origins,
        reaction_types=reaction_types,
    )


def _nearest(objectives: Iterable[Objective], side: TargetSide, current_price: float) -> Objective | None:
    candidates = [objective for objective in objectives if objective.side is side]
    return min(
        candidates,
        key=lambda objective: (_distance_to_objective(objective, current_price), objective.identity),
        default=None,
    )


def build_semantic_targeting_snapshot(
    *,
    symbol: str,
    as_of,
    current_price: float,
    reference_atr: float,
    evidence: Iterable[TargetEvidence],
) -> SemanticTargetingSnapshot:
    items = tuple(evidence)
    objectives = tuple(
        objective
        for item in items
        if (objective := to_objective(item, current_price=current_price)) is not None
    )
    reactions = tuple(
        zone
        for item in items
        if (zone := to_reaction_zone(item, current_price=current_price)) is not None
    )
    confirmations = tuple(
        confirmation
        for item in items
        if (confirmation := to_confirmation(item, current_price=current_price)) is not None
    )
    nearest_up = _nearest(objectives, TargetSide.ABOVE, current_price)
    nearest_down = _nearest(objectives, TargetSide.BELOW, current_price)
    up_context = (
        None
        if nearest_up is None
        else build_arrival_context(
            nearest_up,
            current_price=current_price,
            reference_atr=reference_atr,
            reactions=reactions,
            confirmations=confirmations,
        )
    )
    down_context = (
        None
        if nearest_down is None
        else build_arrival_context(
            nearest_down,
            current_price=current_price,
            reference_atr=reference_atr,
            reactions=reactions,
            confirmations=confirmations,
        )
    )

    if nearest_up is None and nearest_down is None:
        state = ArrivalState.REACTION_ZONE_ONLY if reactions else ArrivalState.NO_ACTIVE_OBJECTIVE
    elif any(context is not None and context.state is ArrivalState.AT_OBJECTIVE for context in (up_context, down_context)):
        state = ArrivalState.AT_OBJECTIVE
    elif any(context is not None and context.state is ArrivalState.IN_REACTION_ZONE for context in (up_context, down_context)):
        state = ArrivalState.IN_REACTION_ZONE
    elif any(context is not None and context.state is ArrivalState.CONFLICTING_ARRIVAL for context in (up_context, down_context)):
        state = ArrivalState.CONFLICTING_ARRIVAL
    elif any(context is not None and context.state is ArrivalState.MULTI_DOMAIN_REACTION for context in (up_context, down_context)):
        state = ArrivalState.MULTI_DOMAIN_REACTION
    elif any(context is not None and context.state is ArrivalState.OBJECTIVE_WITH_REACTION for context in (up_context, down_context)):
        state = ArrivalState.OBJECTIVE_WITH_REACTION
    else:
        state = ArrivalState.OBJECTIVE_ONLY

    return SemanticTargetingSnapshot(
        symbol=symbol,
        as_of=as_of,
        current_price=float(current_price),
        reference_atr=float(reference_atr),
        objectives=objectives,
        reaction_zones=reactions,
        confirmations=confirmations,
        nearest_upside_objective=nearest_up,
        nearest_downside_objective=nearest_down,
        upside_arrival=up_context,
        downside_arrival=down_context,
        state=state,
    )


__all__ = ["build_arrival_context", "build_semantic_targeting_snapshot"]
