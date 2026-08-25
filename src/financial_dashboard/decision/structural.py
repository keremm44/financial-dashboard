from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from financial_dashboard.context.envelope import ContextDataQuality, FactRef
from financial_dashboard.context.projections import (
    StructuralFactsProjection,
    StructuralScopeProjection,
    StructuralTimeframeProjection,
)


class DecisionHorizon(StrEnum):
    LONG_TERM = "LONG_TERM"
    SHORT_TERM = "SHORT_TERM"


class StructuralDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    UNRESOLVED = "UNRESOLVED"


class ThesisState(StrEnum):
    INTACT = "INTACT"
    TRANSITIONING = "TRANSITIONING"
    INVALIDATED = "INVALIDATED"
    UNRESOLVED = "UNRESOLVED"


class HorizonRelation(StrEnum):
    ALIGNED = "ALIGNED"
    PULLBACK = "PULLBACK"
    COUNTER_REACTION = "COUNTER_REACTION"
    EARLY_TRANSITION = "EARLY_TRANSITION"
    STRUCTURAL_CONFLICT = "STRUCTURAL_CONFLICT"
    LT_UNRESOLVED = "LT_UNRESOLVED"
    ST_UNRESOLVED = "ST_UNRESOLVED"
    POST_INVALIDATION = "POST_INVALIDATION"


@dataclass(frozen=True, slots=True)
class StructuralAssessment:
    """Structure-owned directional assessment for exactly one decision horizon.

    The object intentionally contains no supporting-domain inputs. Direction and
    thesis lifecycle are derived from canonical external Structure only.
    """

    horizon: DecisionHorizon
    authority_timeframe: str
    direction: StructuralDirection
    thesis_state: ThesisState
    native_state: str | None
    transition_target: StructuralDirection | None
    data_quality: ContextDataQuality
    authority_as_of: object | None
    protected_high: float | None
    protected_low: float | None
    weak_high: float | None
    weak_low: float | None
    source_refs: tuple[FactRef, ...]
    reasons: tuple[str, ...]
    secondary_timeframe: str | None = None
    secondary_native_state: str | None = None

    def __post_init__(self) -> None:
        if not self.authority_timeframe.strip():
            raise ValueError("structural authority timeframe must be non-empty")
        if self.transition_target is StructuralDirection.UNRESOLVED:
            raise ValueError("transition target must be directional when present")
        if any(ref.timeframe not in {self.authority_timeframe, self.secondary_timeframe} for ref in self.source_refs):
            raise ValueError("structural assessment refs must belong to its authority context")


@dataclass(frozen=True, slots=True)
class HorizonStructuralSnapshot:
    long_term: StructuralAssessment
    short_term: StructuralAssessment
    relation: HorizonRelation
    reasons: tuple[str, ...]


_BULLISH_STATES = frozenset({"BULLISH", "STATE_BULLISH"})
_BEARISH_STATES = frozenset({"BEARISH", "STATE_BEARISH"})
_TRANSITION_UP_STATES = frozenset({"TRANSITION_UP", "STATE_TRANSITION_UP"})
_TRANSITION_DOWN_STATES = frozenset({"TRANSITION_DOWN", "STATE_TRANSITION_DOWN"})
_NEUTRAL_STATES = frozenset({"NEUTRAL", "STATE_NEUTRAL"})


def _state(scope: StructuralScopeProjection | None) -> str | None:
    if scope is None:
        return None
    token = str(scope.state).strip().upper()
    return token or None


def _external_refs(row: StructuralTimeframeProjection | None) -> tuple[FactRef, ...]:
    if row is None:
        return ()
    refs = [
        event.ref
        for event in row.events
        if event.scope.strip().upper() == "EXTERNAL"
        and event.confirmation_status.strip().upper() == "CONFIRMED"
        and event.ref.data_quality is ContextDataQuality.VALID
    ]
    return tuple(sorted(refs, key=lambda ref: ref.deterministic_key))


def _row(structural: StructuralFactsProjection, timeframe: str) -> StructuralTimeframeProjection | None:
    try:
        return structural.for_timeframe(timeframe)
    except KeyError:
        return None


def _unresolved(
    *,
    horizon: DecisionHorizon,
    timeframe: str,
    row: StructuralTimeframeProjection | None,
    reason: str,
) -> StructuralAssessment:
    return StructuralAssessment(
        horizon=horizon,
        authority_timeframe=timeframe,
        direction=StructuralDirection.UNRESOLVED,
        thesis_state=ThesisState.UNRESOLVED,
        native_state=None if row is None else _state(row.external),
        transition_target=None,
        data_quality=ContextDataQuality.UNAVAILABLE if row is None else row.data_quality,
        authority_as_of=None if row is None else row.as_of,
        protected_high=None,
        protected_low=None,
        weak_high=None,
        weak_low=None,
        source_refs=_external_refs(row),
        reasons=(reason,),
    )


def _primary_assessment(
    structural: StructuralFactsProjection,
    *,
    horizon: DecisionHorizon,
    timeframe: str,
) -> StructuralAssessment:
    normalized = timeframe.strip().lower()
    row = _row(structural, normalized)
    if row is None:
        return _unresolved(
            horizon=horizon,
            timeframe=normalized,
            row=None,
            reason=f"{normalized}:STRUCTURE_AUTHORITY_MISSING",
        )
    if row.data_quality is not ContextDataQuality.VALID:
        return _unresolved(
            horizon=horizon,
            timeframe=normalized,
            row=row,
            reason=f"{normalized}:STRUCTURE_AUTHORITY_{row.data_quality.value}",
        )
    external = row.external
    if external is None:
        return _unresolved(
            horizon=horizon,
            timeframe=normalized,
            row=row,
            reason=f"{normalized}:EXTERNAL_STRUCTURE_MISSING",
        )

    native_state = _state(external)
    direction = StructuralDirection.UNRESOLVED
    thesis_state = ThesisState.UNRESOLVED
    transition_target: StructuralDirection | None = None
    reason = f"{normalized}:STRUCTURE_UNRESOLVED:{native_state or 'NONE'}"

    if native_state in _BULLISH_STATES and int(external.direction) > 0:
        direction = StructuralDirection.LONG
        thesis_state = ThesisState.INTACT
        reason = f"{normalized}:CANONICAL_BULLISH_STRUCTURE"
    elif native_state in _BEARISH_STATES and int(external.direction) < 0:
        direction = StructuralDirection.SHORT
        thesis_state = ThesisState.INTACT
        reason = f"{normalized}:CANONICAL_BEARISH_STRUCTURE"
    elif native_state in _TRANSITION_DOWN_STATES:
        # Native Structure deliberately clears direction while a bearish CHoCH
        # awaits transition-confirming BOS. The previously established side is LONG.
        direction = StructuralDirection.LONG
        thesis_state = ThesisState.TRANSITIONING
        transition_target = StructuralDirection.SHORT
        reason = f"{normalized}:CANONICAL_TRANSITION_DOWN"
    elif native_state in _TRANSITION_UP_STATES:
        # Symmetric case: the established SHORT thesis is transitioning toward LONG.
        direction = StructuralDirection.SHORT
        thesis_state = ThesisState.TRANSITIONING
        transition_target = StructuralDirection.LONG
        reason = f"{normalized}:CANONICAL_TRANSITION_UP"
    elif native_state in _NEUTRAL_STATES:
        reason = f"{normalized}:CANONICAL_STRUCTURE_NEUTRAL"
    elif native_state in _BULLISH_STATES | _BEARISH_STATES:
        # A directional state/direction mismatch is not repaired downstream.
        reason = f"{normalized}:STRUCTURE_STATE_DIRECTION_MISMATCH"

    return StructuralAssessment(
        horizon=horizon,
        authority_timeframe=normalized,
        direction=direction,
        thesis_state=thesis_state,
        native_state=native_state,
        transition_target=transition_target,
        data_quality=row.data_quality,
        authority_as_of=row.as_of,
        protected_high=external.protected_high,
        protected_low=external.protected_low,
        weak_high=external.weak_high,
        weak_low=external.weak_low,
        source_refs=_external_refs(row),
        reasons=(reason,),
    )


def assess_long_term_structure(structural: StructuralFactsProjection) -> StructuralAssessment:
    """Build LT thesis from 1D authority and 4H transition context only.

    4H may mark the existing 1D thesis as transitioning when its native Structure
    enters the opposite transition state. It can never flip LT direction. 2H, 1H
    and 30m are intentionally absent from this function's authority logic.
    """

    base = _primary_assessment(
        structural,
        horizon=DecisionHorizon.LONG_TERM,
        timeframe="1d",
    )
    secondary = _row(structural, "4h")
    secondary_state = None if secondary is None else _state(secondary.external)
    secondary_refs = _external_refs(secondary)
    base = replace(
        base,
        secondary_timeframe="4h",
        secondary_native_state=secondary_state,
        source_refs=tuple(
            sorted(
                {ref.deterministic_key: ref for ref in (*base.source_refs, *secondary_refs)}.values(),
                key=lambda ref: ref.deterministic_key,
            )
        ),
    )

    if base.direction is StructuralDirection.UNRESOLVED or base.thesis_state is not ThesisState.INTACT:
        return base
    if secondary is None:
        return replace(base, reasons=(*base.reasons, "4h:SECONDARY_STRUCTURE_MISSING"))
    if secondary.data_quality is not ContextDataQuality.VALID or secondary.external is None:
        return replace(
            base,
            reasons=(*base.reasons, f"4h:SECONDARY_STRUCTURE_{secondary.data_quality.value}"),
        )

    if (
        base.direction is StructuralDirection.LONG
        and secondary_state in _TRANSITION_DOWN_STATES
    ):
        return replace(
            base,
            thesis_state=ThesisState.TRANSITIONING,
            transition_target=StructuralDirection.SHORT,
            reasons=(*base.reasons, "4h:OPPOSITE_TRANSITION_CONTEXT"),
        )
    if (
        base.direction is StructuralDirection.SHORT
        and secondary_state in _TRANSITION_UP_STATES
    ):
        return replace(
            base,
            thesis_state=ThesisState.TRANSITIONING,
            transition_target=StructuralDirection.LONG,
            reasons=(*base.reasons, "4h:OPPOSITE_TRANSITION_CONTEXT"),
        )

    return replace(base, reasons=(*base.reasons, f"4h:SECONDARY_CONTEXT:{secondary_state or 'NONE'}"))


def assess_short_term_structure(structural: StructuralFactsProjection) -> StructuralAssessment:
    """Build ST thesis from 1H Structure only; 2H/30m are never promoted."""

    return _primary_assessment(
        structural,
        horizon=DecisionHorizon.SHORT_TERM,
        timeframe="1h",
    )


def classify_horizon_relation(
    long_term: StructuralAssessment,
    short_term: StructuralAssessment,
) -> tuple[HorizonRelation, tuple[str, ...]]:
    """Describe LT/ST structural relationship without changing either thesis."""

    if long_term.thesis_state is ThesisState.INVALIDATED:
        return HorizonRelation.POST_INVALIDATION, ("LT_THESIS_INVALIDATED",)
    if long_term.direction is StructuralDirection.UNRESOLVED:
        return HorizonRelation.LT_UNRESOLVED, ("LT_DIRECTION_UNRESOLVED",)
    if short_term.direction is StructuralDirection.UNRESOLVED:
        return HorizonRelation.ST_UNRESOLVED, ("ST_DIRECTION_UNRESOLVED",)

    if long_term.direction is short_term.direction:
        return HorizonRelation.ALIGNED, ("LT_ST_ESTABLISHED_SIDES_ALIGNED",)

    if (
        long_term.thesis_state is ThesisState.TRANSITIONING
        and long_term.transition_target is short_term.direction
    ):
        return HorizonRelation.EARLY_TRANSITION, (
            "ST_ALREADY_ALIGNED_WITH_LT_TRANSITION_TARGET",
        )

    if (
        short_term.thesis_state is ThesisState.TRANSITIONING
        and short_term.transition_target is long_term.direction
    ):
        return HorizonRelation.PULLBACK, (
            "ST_COUNTER_SIDE_TRANSITIONING_BACK_TOWARD_LT",
        )

    if (
        long_term.thesis_state is ThesisState.INTACT
        and short_term.thesis_state is ThesisState.INTACT
    ):
        return HorizonRelation.COUNTER_REACTION, (
            "ST_ESTABLISHED_SIDE_OPPOSES_INTACT_LT",
        )

    return HorizonRelation.STRUCTURAL_CONFLICT, (
        "LT_ST_STRUCTURAL_STATES_NOT_CANONICALLY_RECONCILED",
    )


def build_horizon_structural_snapshot(
    structural: StructuralFactsProjection,
) -> HorizonStructuralSnapshot:
    long_term = assess_long_term_structure(structural)
    short_term = assess_short_term_structure(structural)
    relation, reasons = classify_horizon_relation(long_term, short_term)
    return HorizonStructuralSnapshot(
        long_term=long_term,
        short_term=short_term,
        relation=relation,
        reasons=reasons,
    )


__all__ = [
    "DecisionHorizon",
    "HorizonRelation",
    "HorizonStructuralSnapshot",
    "StructuralAssessment",
    "StructuralDirection",
    "ThesisState",
    "assess_long_term_structure",
    "assess_short_term_structure",
    "build_horizon_structural_snapshot",
    "classify_horizon_relation",
]
