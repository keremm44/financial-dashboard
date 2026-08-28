from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .axes import (
    ConflictState,
    ContextAxes,
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


def _source_refs_from_axes(axes: ContextAxes) -> tuple[str, ...]:
    refs = {
        ref
        for reason in axes.reasons
        for ref in reason.source_refs
        if ref
    }
    return tuple(sorted(refs))


def resolve_permission_axes(
    axes: ContextAxes,
    *,
    source_refs: tuple[str, ...] | None = None,
) -> PermissionEnvelope:
    """Resolve scoped permission from one already-frozen context-axis view.

    This helper lets the decision layer derive horizon-aware permission cheaply from
    the same immutable projections without rebuilding native engines or counting the
    context summary as fresh evidence.
    """

    refs = source_refs if source_refs is not None else _source_refs_from_axes(axes)

    if axes.structural_thesis in {StructuralThesis.UNAVAILABLE, StructuralThesis.UNRESOLVED}:
        return PermissionEnvelope(
            scope=PermissionScope.NONE,
            permitted_side=PermittedSide.NONE,
            gate_state=GateState.BLOCKED,
            blocking_reasons=("CANONICAL_STRUCTURE_UNRESOLVED",),
            source_refs=refs,
        )

    # Accepted architecture: HIGH is a hard conflict gate; UNRESOLVED is not
    # automatically equivalent to HIGH. Preserve uncertainty as WAIT instead of
    # manufacturing a hard NO_TRADE.
    if axes.conflict is ConflictState.HIGH:
        return PermissionEnvelope(
            scope=PermissionScope.NONE,
            permitted_side=PermittedSide.NONE,
            gate_state=GateState.BLOCKED,
            blocking_reasons=("CONTEXT_CONFLICT_HIGH",),
            source_refs=refs,
        )
    if axes.conflict is ConflictState.UNRESOLVED:
        return PermissionEnvelope(
            scope=PermissionScope.NONE,
            permitted_side=_side(axes.structural_direction),
            gate_state=GateState.WAITING,
            allowed_reasons=("CONTEXT_CONFLICT_UNRESOLVED",),
            waiting_for=("CONTEXT_CONFLICT_TO_RESOLVE",),
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
        # Intact thesis: an opposing zone interaction is the pullback discount,
        # not a side flip. Flipping permitted_side to the counter move made
        # eligibility wait on PERMISSION_SCOPE_SIDE_TO_RECONCILE during the
        # exact window a continuation entry should consider. Reversal/transition
        # already returned above; HIGH / opposing BOS already blocked.
        if axes.structural_thesis in {StructuralThesis.UP, StructuralThesis.DOWN}:
            side = _side(axes.structural_direction)
            if side is PermittedSide.NONE:
                return PermissionEnvelope(
                    scope=PermissionScope.NONE,
                    permitted_side=PermittedSide.NONE,
                    gate_state=GateState.BLOCKED,
                    blocking_reasons=("CONTINUATION_DIRECTION_UNRESOLVED",),
                    source_refs=refs,
                )
            scope = (
                PermissionScope.CONTINUATION_ONLY
                if axes.anchor_timeframe.strip().lower() == "1d"
                else PermissionScope.REACTION_ONLY
            )
            return PermissionEnvelope(
                scope=scope,
                permitted_side=side,
                gate_state=GateState.CONDITIONAL,
                allowed_reasons=(
                    "PULLBACK_DISCOUNT_CONTEXT",
                    "COUNTER_REACTION_IS_DISCOUNT_NOT_SIDE_FLIP",
                ),
                waiting_for=("FUTURE_ACTION_LAYER_TIMING",),
                source_refs=refs,
            )
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

    # Intact thesis without a fresh aligned BOS is the pullback-discount window,
    # not a missing context. The BOS that established the thesis already lives
    # in structural_thesis; waiting for the next anchor-TF BOS would enter after
    # the discount is gone. HIGH / opposing BOS already returned above.
    # MATERIAL counter-CHOCH is the pullback itself and must not re-lock this
    # branch. OPEN is reserved for aligned continuation; timing still owns READY.
    if (
        axes.structural_thesis in {StructuralThesis.UP, StructuralThesis.DOWN}
        and axes.continuation in {ContinuationContext.WEAK, ContinuationContext.CONFLICTING}
        and axes.reaction is not ReactionContext.FAILED
    ):
        side = _side(axes.structural_direction)
        if side is PermittedSide.NONE:
            return PermissionEnvelope(
                scope=PermissionScope.NONE,
                permitted_side=PermittedSide.NONE,
                gate_state=GateState.BLOCKED,
                blocking_reasons=("CONTINUATION_DIRECTION_UNRESOLVED",),
                source_refs=refs,
            )
        scope = (
            PermissionScope.CONTINUATION_ONLY
            if axes.anchor_timeframe.strip().lower() == "1d"
            else PermissionScope.REACTION_ONLY
        )
        return PermissionEnvelope(
            scope=scope,
            permitted_side=side,
            gate_state=GateState.CONDITIONAL,
            allowed_reasons=("PULLBACK_DISCOUNT_CONTEXT",),
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


def resolve_permission(snapshot: CrossDomainContextSnapshot) -> PermissionEnvelope:
    """Resolve scoped permission with explicit gates and no numeric voting."""

    return resolve_permission_axes(snapshot.axes, source_refs=_source_refs_from_axes(snapshot.axes))


__all__ = [
    "GateState",
    "PermissionEnvelope",
    "PermissionScope",
    "PermittedSide",
    "resolve_permission",
    "resolve_permission_axes",
]
