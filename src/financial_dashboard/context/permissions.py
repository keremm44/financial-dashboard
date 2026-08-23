from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .axes import (
    ConflictState,
    ContextDirection,
    ContinuationContext,
    ReactionContext,
    ReversalContext,
    StructuralThesis,
)
from .snapshot import CrossDomainContextSnapshot


class PermissionScope(StrEnum):
    NONE = "NONE"
    REACTION_ONLY = "REACTION_ONLY"
    CONTINUATION_ONLY = "CONTINUATION_ONLY"
    STRUCTURAL_TRANSITION = "STRUCTURAL_TRANSITION"


class PermittedSide(StrEnum):
    NONE = "NONE"
    LONG = "LONG"
    SHORT = "SHORT"


class GateState(StrEnum):
    BLOCKED = "BLOCKED"
    WAITING = "WAITING"
    CONDITIONAL = "CONDITIONAL"
    OPEN = "OPEN"


@dataclass(frozen=True, slots=True)
class PermissionEnvelope:
    """Scoped context permission. This is not BUY/SELL or an entry instruction."""

    scope: PermissionScope
    permitted_side: PermittedSide
    gate_state: GateState
    allowed_reasons: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    waiting_for: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()

    @property
    def is_actionable_signal(self) -> bool:
        """Always false by contract; action authority belongs to a later layer."""

        return False


def _side(direction: ContextDirection) -> PermittedSide:
    if direction is ContextDirection.UP:
        return PermittedSide.LONG
    if direction is ContextDirection.DOWN:
        return PermittedSide.SHORT
    return PermittedSide.NONE


def _source_refs(snapshot: CrossDomainContextSnapshot) -> tuple[str, ...]:
    refs = {
        ref
        for reason in snapshot.axes.reasons
        for ref in reason.source_refs
        if ref
    }
    return tuple(sorted(refs))


def resolve_permission(snapshot: CrossDomainContextSnapshot) -> PermissionEnvelope:
    """Resolve scoped permission with explicit gates and no numeric voting.

    Ordering is intentional: canonical structural blockers first, then structural
    transition, counter-reaction, and continuation scopes. Supporting domains can
    block/wait but cannot manufacture structural authority.
    """

    axes = snapshot.axes
    refs = _source_refs(snapshot)

    if axes.structural_thesis in {StructuralThesis.UNAVAILABLE, StructuralThesis.UNRESOLVED}:
        return PermissionEnvelope(
            scope=PermissionScope.NONE,
            permitted_side=PermittedSide.NONE,
            gate_state=GateState.BLOCKED,
            blocking_reasons=("CANONICAL_STRUCTURE_UNRESOLVED",),
            source_refs=refs,
        )

    if axes.conflict in {ConflictState.HIGH, ConflictState.UNRESOLVED}:
        return PermissionEnvelope(
            scope=PermissionScope.NONE,
            permitted_side=PermittedSide.NONE,
            gate_state=GateState.BLOCKED,
            blocking_reasons=(f"CONTEXT_CONFLICT_{axes.conflict.value}",),
            source_refs=refs,
        )

    if axes.reversal is ReversalContext.STRUCTURALLY_CONFIRMED:
        side = _side(axes.reversal_direction)
        if side is PermittedSide.NONE:
            return PermissionEnvelope(
                scope=PermissionScope.NONE,
                permitted_side=PermittedSide.NONE,
                gate_state=GateState.BLOCKED,
                blocking_reasons=("REVERSAL_DIRECTION_UNRESOLVED",),
                source_refs=refs,
            )
        return PermissionEnvelope(
            scope=PermissionScope.STRUCTURAL_TRANSITION,
            permitted_side=side,
            gate_state=GateState.CONDITIONAL,
            allowed_reasons=("CANONICAL_STRUCTURAL_TRANSITION_CONFIRMED",),
            waiting_for=("FUTURE_ACTION_LAYER_TIMING",),
            source_refs=refs,
        )

    if axes.reversal is ReversalContext.CANDIDATE:
        side = _side(axes.reversal_direction)
        return PermissionEnvelope(
            scope=PermissionScope.STRUCTURAL_TRANSITION,
            permitted_side=side,
            gate_state=GateState.WAITING,
            allowed_reasons=("CANONICAL_STRUCTURAL_TRANSITION_CANDIDATE",),
            waiting_for=("CANONICAL_STRUCTURAL_FOLLOW_THROUGH",),
            source_refs=refs,
        )

    counter_reaction = (
        axes.reaction in {ReactionContext.ACTIVE, ReactionContext.DEVELOPING}
        and axes.reaction_direction not in {ContextDirection.NONE, axes.structural_direction}
    )
    if counter_reaction:
        side = _side(axes.reaction_direction)
        if axes.reaction is ReactionContext.DEVELOPING:
            return PermissionEnvelope(
                scope=PermissionScope.REACTION_ONLY,
                permitted_side=side,
                gate_state=GateState.WAITING,
                allowed_reasons=("COUNTER_REACTION_DEVELOPING",),
                waiting_for=("REACTION_INTERACTION_TO_BECOME_ACTIVE",),
                source_refs=refs,
            )
        return PermissionEnvelope(
            scope=PermissionScope.REACTION_ONLY,
            permitted_side=side,
            gate_state=GateState.CONDITIONAL,
            allowed_reasons=("ACTIVE_COUNTER_REACTION_CONTEXT",),
            waiting_for=("FUTURE_ACTION_LAYER_TIMING",),
            source_refs=refs,
        )

    if axes.continuation is ContinuationContext.ALIGNED:
        side = _side(axes.structural_direction)
        if side is PermittedSide.NONE:
            return PermissionEnvelope(
                scope=PermissionScope.NONE,
                permitted_side=PermittedSide.NONE,
                gate_state=GateState.BLOCKED,
                blocking_reasons=("CONTINUATION_DIRECTION_UNRESOLVED",),
                source_refs=refs,
            )
        if axes.conflict is ConflictState.MATERIAL:
            return PermissionEnvelope(
                scope=PermissionScope.CONTINUATION_ONLY,
                permitted_side=side,
                gate_state=GateState.WAITING,
                allowed_reasons=("CANONICAL_CONTINUATION_ALIGNED",),
                waiting_for=("MATERIAL_SUPPORTING_CONFLICT_TO_RESOLVE",),
                source_refs=refs,
            )
        return PermissionEnvelope(
            scope=PermissionScope.CONTINUATION_ONLY,
            permitted_side=side,
            gate_state=GateState.OPEN,
            allowed_reasons=("CANONICAL_CONTINUATION_ALIGNED",),
            waiting_for=("FUTURE_ACTION_LAYER_TIMING",),
            source_refs=refs,
        )

    if axes.reaction is ReactionContext.ACTIVE:
        side = _side(axes.reaction_direction)
        return PermissionEnvelope(
            scope=PermissionScope.REACTION_ONLY,
            permitted_side=side,
            gate_state=GateState.CONDITIONAL,
            allowed_reasons=("ACTIVE_REACTION_CONTEXT",),
            waiting_for=("FUTURE_ACTION_LAYER_TIMING",),
            source_refs=refs,
        )

    return PermissionEnvelope(
        scope=PermissionScope.NONE,
        permitted_side=PermittedSide.NONE,
        gate_state=GateState.WAITING,
        waiting_for=("QUALIFIED_CONTINUATION_REACTION_OR_TRANSITION_CONTEXT",),
        source_refs=refs,
    )


__all__ = [
    "GateState",
    "PermissionEnvelope",
    "PermissionScope",
    "PermittedSide",
    "resolve_permission",
]
