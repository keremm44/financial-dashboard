from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Iterable

from financial_dashboard.context.envelope import ContextDataQuality, FactRef
from financial_dashboard.context.pattern_behavior_projection import PatternBehaviorPhase

from .lifecycle import ExitStage, PositionState, TradeLifecycleState
from .participation import ParticipationState, assess_participation
from .reaction import ReactionState, assess_reaction
from .st_thesis_identity import STThesisFamily
from .structural import DecisionHorizon, StructuralDirection

if TYPE_CHECKING:
    from financial_dashboard.decision_input import DecisionInputSnapshot


class STProtectiveShadowState(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNRESOLVED = "UNRESOLVED"
    NO_INTENT = "NO_INTENT"
    PROTECTIVE_INTENT = "PROTECTIVE_INTENT"


class STProtectiveTimingRelation(StrEnum):
    NOT_COMPARED = "NOT_COMPARED"
    BOTH_INACTIVE = "BOTH_INACTIVE"
    SHADOW_EARLIER = "SHADOW_EARLIER"
    ALIGNED = "ALIGNED"
    CANONICAL_EARLIER = "CANONICAL_EARLIER"


@dataclass(frozen=True, slots=True)
class STProtectiveShadowAssessment:
    state: STProtectiveShadowState
    thesis_family: STThesisFamily | None
    timing_relation: STProtectiveTimingRelation
    reasons: tuple[str, ...]
    primary_evidence: tuple[str, ...]
    secondary_evidence: tuple[str, ...]
    source_refs: tuple[FactRef, ...]

    @property
    def protective_intent(self) -> bool:
        return self.state is STProtectiveShadowState.PROTECTIVE_INTENT


def _unique_refs(refs: Iterable[FactRef]) -> tuple[FactRef, ...]:
    by_key = {ref.deterministic_key: ref for ref in refs}
    return tuple(sorted(by_key.values(), key=lambda ref: ref.deterministic_key))


def _causal_valid_ref(ref: FactRef | None, as_of: Any) -> bool:
    if ref is None or ref.data_quality is not ContextDataQuality.VALID:
        return False
    try:
        return ref.is_available_at(as_of)
    except TypeError:
        return False


def _confirmed_after_entry(ref: FactRef, entry_as_of: Any) -> bool:
    confirmed_at = ref.confirmed_at
    if confirmed_at is None:
        return False
    try:
        return confirmed_at > entry_as_of
    except TypeError:
        return False


def _timing_relation(
    state: STProtectiveShadowState,
    canonical_stage: ExitStage | None,
) -> STProtectiveTimingRelation:
    if canonical_stage is None:
        return STProtectiveTimingRelation.NOT_COMPARED
    shadow_ready = state is STProtectiveShadowState.PROTECTIVE_INTENT
    canonical_ready = canonical_stage is ExitStage.EXIT_READY
    if shadow_ready and canonical_ready:
        return STProtectiveTimingRelation.ALIGNED
    if shadow_ready:
        return STProtectiveTimingRelation.SHADOW_EARLIER
    if canonical_ready:
        return STProtectiveTimingRelation.CANONICAL_EARLIER
    return STProtectiveTimingRelation.BOTH_INACTIVE


def _assessment(
    *,
    state: STProtectiveShadowState,
    family: STThesisFamily | None,
    canonical_stage: ExitStage | None,
    reasons: Iterable[str],
    primary: Iterable[str] = (),
    secondary: Iterable[str] = (),
    refs: Iterable[FactRef] = (),
) -> STProtectiveShadowAssessment:
    return STProtectiveShadowAssessment(
        state=state,
        thesis_family=family,
        timing_relation=_timing_relation(state, canonical_stage),
        reasons=tuple(dict.fromkeys(reasons)),
        primary_evidence=tuple(dict.fromkeys(primary)),
        secondary_evidence=tuple(dict.fromkeys(secondary)),
        source_refs=_unique_refs(refs),
    )


def _one_timeframe_fact(projection: Any | None, timeframe: str) -> Any | None:
    if projection is None:
        return None
    normalized = timeframe.strip().lower()
    method = getattr(projection, "for_timeframe", None)
    if callable(method):
        try:
            return method(normalized)
        except KeyError:
            return None
    for row in getattr(projection, "timeframe_facts", ()):
        if str(getattr(row, "timeframe", "")).strip().lower() == normalized:
            return row
    return None


def _downside_progress(
    snapshot: "DecisionInputSnapshot",
    *,
    timeframe: str,
    entry_as_of: Any,
    anchor_high: float,
) -> tuple[bool, tuple[FactRef, ...]]:
    row = _one_timeframe_fact(getattr(snapshot, "structure", None), timeframe)
    if row is None or getattr(row, "data_quality", ContextDataQuality.UNAVAILABLE) is not ContextDataQuality.VALID:
        return False, ()

    refs: list[FactRef] = []
    for event in getattr(row, "events", ()):
        ref = getattr(event, "ref", None)
        if not _causal_valid_ref(ref, snapshot.as_of):
            continue
        if not _confirmed_after_entry(ref, entry_as_of):
            continue
        if int(getattr(event, "direction", 0) or 0) != -1:
            continue
        if str(getattr(event, "confirmation_status", "")).strip().upper() != "CONFIRMED":
            continue
        if str(getattr(event, "validity", "")).strip().upper() != "VALID":
            continue
        if str(getattr(event, "relevance", "")).strip().upper() not in {"CURRENT", "ACTIVE"}:
            continue
        broken_level = getattr(event, "broken_level", None)
        if broken_level is None or float(broken_level) > float(anchor_high):
            continue
        refs.append(ref)
    return bool(refs), _unique_refs(refs)


def _anchor_specific_reclaim_failure(
    snapshot: "DecisionInputSnapshot",
    *,
    identity: str,
    timeframe: str,
) -> tuple[bool | None, tuple[FactRef, ...]]:
    normalized = timeframe.strip().lower()
    if identity.startswith("OB:"):
        target = identity.split(":", 1)[1]
        projection = getattr(snapshot, "order_block_behavior", None)
        rows = () if projection is None else getattr(projection, "observations", ())
        matches = [
            row
            for row in rows
            if str(getattr(row, "timeframe", "")).strip().lower() == normalized
            and bool(getattr(row, "bullish", False))
            and str(getattr(row, "identity", "")) == target
        ]
        if not matches:
            return None, ()
        valid = [row for row in matches if _causal_valid_ref(getattr(row, "ref", None), snapshot.as_of)]
        if not valid:
            return None, ()
        failed = any(
            str(getattr(row, "interaction", "")).strip().upper() == "FAILED"
            or str(getattr(row, "state", "")).strip().upper() in {"CONSUMED", "EXPIRED_CANDIDATE"}
            for row in valid
        )
        return failed, _unique_refs(getattr(row, "ref") for row in valid)

    if identity.startswith("FVG:"):
        target = identity.split(":", 1)[1]
        projection = getattr(snapshot, "fvg_engulfing_lifecycle", None)
        rows = () if projection is None else getattr(projection, "fvg", ())
        matches = [
            row
            for row in rows
            if str(getattr(getattr(row, "ref", None), "timeframe", "")).strip().lower() == normalized
            and int(getattr(row, "direction", 0) or 0) == 1
            and str(getattr(row, "identity", "")) == target
        ]
        if not matches:
            return None, ()
        valid = [row for row in matches if _causal_valid_ref(getattr(row, "ref", None), snapshot.as_of)]
        if not valid:
            return None, ()
        failed = any(
            bool(getattr(row, "failed_reaction", False))
            or bool(getattr(row, "full_fill", False))
            or bool(getattr(row, "invalid", False))
            for row in valid
        )
        return failed, _unique_refs(getattr(row, "ref") for row in valid)

    return None, ()


def _buyer_reclaim_failure(
    snapshot: "DecisionInputSnapshot",
    *,
    timeframe: str,
    anchor_identity: str,
) -> tuple[bool | None, tuple[FactRef, ...]]:
    specific, refs = _anchor_specific_reclaim_failure(
        snapshot,
        identity=anchor_identity,
        timeframe=timeframe,
    )
    if specific is not None:
        return specific, refs

    reaction = assess_reaction(
        StructuralDirection.LONG,
        order_blocks=getattr(snapshot, "order_block_behavior", None),
        fvg_engulfing=getattr(snapshot, "fvg_engulfing_lifecycle", None),
        timeframes=(timeframe,),
    )
    valid_refs = tuple(
        ref for ref in reaction.source_refs if _causal_valid_ref(ref, snapshot.as_of)
    )
    if reaction.state is ReactionState.UNKNOWN or reaction.data_quality is not ContextDataQuality.VALID:
        return None, _unique_refs(valid_refs)
    return reaction.state is ReactionState.FAILED, _unique_refs(valid_refs)


def _breakout_range_reentry(
    snapshot: "DecisionInputSnapshot",
    *,
    timeframe: str,
    anchor_low: float,
    anchor_high: float,
) -> tuple[bool | None, tuple[FactRef, ...]]:
    row = _one_timeframe_fact(getattr(snapshot, "support_resistance", None), timeframe)
    ref = None if row is None else getattr(row, "ref", None)
    if row is None or not _causal_valid_ref(ref, snapshot.as_of):
        return None, ()

    role_low = getattr(row, "role_reversal_support_low", None)
    role_high = getattr(row, "role_reversal_support_high", None)
    matched_role = (
        role_low is not None
        and role_high is not None
        and float(role_low) == float(anchor_low)
        and float(role_high) == float(anchor_high)
    )
    if not matched_role:
        return None, (ref,)

    location = str(getattr(row, "price_location", "") or "").strip().upper()
    state = str(getattr(row, "state", "") or "").strip().upper()
    returned_to_range = location in {"INSIDE_RANGE", "UPPER_ZONE", "LOWER_ZONE", "BELOW_RANGE"}
    explicit_failed_excursion = state == "RANGE_BREAK_FAILED"
    return returned_to_range or explicit_failed_excursion, (ref,)


def _secondary_context(
    snapshot: "DecisionInputSnapshot",
    *,
    timeframe: str,
) -> tuple[tuple[str, ...], tuple[FactRef, ...]]:
    evidence: list[str] = []
    refs: list[FactRef] = []

    participation = assess_participation(
        StructuralDirection.LONG,
        getattr(snapshot, "participation_behavior", None),
        timeframe=timeframe,
    )
    if participation.state is ParticipationState.OPPOSING and participation.data_quality is ContextDataQuality.VALID:
        evidence.append("PARTICIPATION_EFFECTIVE_COUNTER_PRESSURE")
        refs.extend(
            ref for ref in participation.source_refs if _causal_valid_ref(ref, snapshot.as_of)
        )

    pattern = _one_timeframe_fact(getattr(snapshot, "pattern_behavior", None), timeframe)
    pattern_ref = None if pattern is None else getattr(pattern, "ref", None)
    if pattern is not None and _causal_valid_ref(pattern_ref, snapshot.as_of):
        phase = getattr(pattern, "phase", PatternBehaviorPhase.UNAVAILABLE)
        direction = int(getattr(pattern, "classic_direction", 0) or 0)
        if direction in {0, 1} and phase in {
            PatternBehaviorPhase.BREAK_FAILED,
            PatternBehaviorPhase.WEAKENING,
            PatternBehaviorPhase.INVALIDATED,
        }:
            evidence.append(f"PATTERN_LONG_SUPPORT_{phase.value}")
            refs.append(pattern_ref)

    return tuple(evidence), _unique_refs(refs)


def assess_st_protective_shadow(
    snapshot: "DecisionInputSnapshot",
    state: TradeLifecycleState,
    *,
    canonical_stage: ExitStage | None = None,
) -> STProtectiveShadowAssessment:
    """Derive thesis-specific ST protective intent without changing canonical exit.

    This is a Step-5 shadow policy. It reads the immutable entry thesis plus causal
    current facts and returns an audit-only intent. It never emits SELL, never changes
    ``TradeLifecycleState``, and never consumes an execution event.
    """

    if state.position is not PositionState.OPEN:
        return _assessment(
            state=STProtectiveShadowState.NOT_APPLICABLE,
            family=None,
            canonical_stage=canonical_stage,
            reasons=("ST_PROTECTIVE_SHADOW_REQUIRES_OPEN_POSITION",),
        )

    metadata = state.entry_metadata
    if metadata is None or metadata.entry_horizon is not DecisionHorizon.SHORT_TERM:
        return _assessment(
            state=STProtectiveShadowState.NOT_APPLICABLE,
            family=None,
            canonical_stage=canonical_stage,
            reasons=("ST_PROTECTIVE_SHADOW_REQUIRES_ST_ENTRY_OWNERSHIP",),
        )

    memory = metadata.st_trade_memory
    if memory is None or memory.thesis_family is STThesisFamily.UNRESOLVED:
        return _assessment(
            state=STProtectiveShadowState.UNRESOLVED,
            family=STThesisFamily.UNRESOLVED if memory is not None else None,
            canonical_stage=canonical_stage,
            reasons=("ST_PROTECTIVE_THESIS_IDENTITY_UNRESOLVED",),
        )

    anchor = memory.initial_defended_anchor
    if anchor is None:
        return _assessment(
            state=STProtectiveShadowState.UNRESOLVED,
            family=memory.thesis_family,
            canonical_stage=canonical_stage,
            reasons=("ST_PROTECTIVE_INITIAL_DEFENDED_ANCHOR_UNRESOLVED",),
        )

    timeframe = anchor.timeframe.strip().lower()
    ground_lost = float(snapshot.current_price) < float(anchor.low)
    if not ground_lost:
        return _assessment(
            state=STProtectiveShadowState.NO_INTENT,
            family=memory.thesis_family,
            canonical_stage=canonical_stage,
            reasons=("ST_PROTECTIVE_DEFENDED_GROUND_INTACT",),
        )

    primary: list[str] = ["DEFENDED_GROUND_LOST"]
    refs: list[FactRef] = []

    downside_progress, structure_refs = _downside_progress(
        snapshot,
        timeframe=timeframe,
        entry_as_of=metadata.entry_as_of,
        anchor_high=float(anchor.high),
    )
    refs.extend(structure_refs)
    if downside_progress:
        primary.extend(("ACCEPTANCE_BELOW_DEFENDED_GROUND", "SELLER_DOWNSIDE_PROGRESS"))

    reclaim_failed, reaction_refs = _buyer_reclaim_failure(
        snapshot,
        timeframe=timeframe,
        anchor_identity=anchor.identity,
    )
    refs.extend(reaction_refs)
    if reclaim_failed is True:
        primary.append("BUYER_RECLAIM_FAILED")

    secondary, secondary_refs = _secondary_context(snapshot, timeframe=timeframe)
    refs.extend(secondary_refs)

    range_reentry: bool | None = True
    if memory.thesis_family is STThesisFamily.BREAKOUT_ACCEPTANCE:
        range_reentry, sr_refs = _breakout_range_reentry(
            snapshot,
            timeframe=timeframe,
            anchor_low=float(anchor.low),
            anchor_high=float(anchor.high),
        )
        refs.extend(sr_refs)
        if range_reentry is True:
            primary.extend(("OLD_RANGE_REENTERED", "BREAKOUT_FAILED_EXCURSION"))

    if not downside_progress:
        structural_row = _one_timeframe_fact(getattr(snapshot, "structure", None), timeframe)
        quality = None if structural_row is None else getattr(structural_row, "data_quality", None)
        unresolved = structural_row is None or quality is not ContextDataQuality.VALID
        return _assessment(
            state=STProtectiveShadowState.UNRESOLVED if unresolved else STProtectiveShadowState.NO_INTENT,
            family=memory.thesis_family,
            canonical_stage=canonical_stage,
            reasons=(
                "ST_PROTECTIVE_DOWNSIDE_STRUCTURE_UNRESOLVED"
                if unresolved
                else "ST_PROTECTIVE_DOWNSIDE_PROGRESS_NOT_CONFIRMED"
            ,),
            primary=primary,
            secondary=secondary,
            refs=refs,
        )

    if reclaim_failed is None:
        return _assessment(
            state=STProtectiveShadowState.UNRESOLVED,
            family=memory.thesis_family,
            canonical_stage=canonical_stage,
            reasons=("ST_PROTECTIVE_BUYER_RECLAIM_STATUS_UNRESOLVED",),
            primary=primary,
            secondary=secondary,
            refs=refs,
        )
    if reclaim_failed is False:
        return _assessment(
            state=STProtectiveShadowState.NO_INTENT,
            family=memory.thesis_family,
            canonical_stage=canonical_stage,
            reasons=("ST_PROTECTIVE_BUYER_RECLAIM_FAILURE_NOT_CONFIRMED",),
            primary=primary,
            secondary=secondary,
            refs=refs,
        )

    if memory.thesis_family is STThesisFamily.BREAKOUT_ACCEPTANCE:
        if range_reentry is None:
            return _assessment(
                state=STProtectiveShadowState.UNRESOLVED,
                family=memory.thesis_family,
                canonical_stage=canonical_stage,
                reasons=("ST_PROTECTIVE_BREAKOUT_RANGE_RELATION_UNRESOLVED",),
                primary=primary,
                secondary=secondary,
                refs=refs,
            )
        if range_reentry is False:
            return _assessment(
                state=STProtectiveShadowState.NO_INTENT,
                family=memory.thesis_family,
                canonical_stage=canonical_stage,
                reasons=("ST_PROTECTIVE_OLD_RANGE_REENTRY_NOT_CONFIRMED",),
                primary=primary,
                secondary=secondary,
                refs=refs,
            )

    thesis_reason = {
        STThesisFamily.PULLBACK_CONTINUATION: "ST_PULLBACK_CONTINUATION_INVALIDATED",
        STThesisFamily.BREAKOUT_ACCEPTANCE: "ST_BREAKOUT_ACCEPTANCE_INVALIDATED",
        STThesisFamily.FAILED_SELL_RECLAIM: "ST_FAILED_SELL_RECLAIM_INVALIDATED",
    }[memory.thesis_family]
    return _assessment(
        state=STProtectiveShadowState.PROTECTIVE_INTENT,
        family=memory.thesis_family,
        canonical_stage=canonical_stage,
        reasons=(thesis_reason,),
        primary=primary,
        secondary=secondary,
        refs=refs,
    )


__all__ = [
    "STProtectiveShadowAssessment",
    "STProtectiveShadowState",
    "STProtectiveTimingRelation",
    "assess_st_protective_shadow",
]
