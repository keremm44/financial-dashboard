from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Iterable

from financial_dashboard.context.envelope import ContextDataQuality, FactRef
from financial_dashboard.context.pattern_behavior_projection import PatternBehaviorPhase

from .lifecycle import ExitStage, PositionState, TradeLifecycleState
from .participation import ParticipationState, assess_participation
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
    values = {ref.deterministic_key: ref for ref in refs}
    return tuple(sorted(values.values(), key=lambda ref: ref.deterministic_key))


def _causal_valid_ref(ref: FactRef | None, as_of: Any) -> bool:
    if ref is None or ref.data_quality is not ContextDataQuality.VALID:
        return False
    try:
        return ref.is_available_at(as_of)
    except (TypeError, ValueError):
        return False


def _confirmed_after_entry(ref: FactRef, entry_as_of: Any) -> bool:
    if ref.confirmed_at is None:
        return False
    try:
        return ref.confirmed_at > entry_as_of
    except TypeError:
        return False


def _ranges_overlap(low_a: float, high_a: float, low_b: float, high_b: float) -> bool:
    return max(float(low_a), float(low_b)) <= min(float(high_a), float(high_b))


def _timing_relation(
    shadow_state: STProtectiveShadowState,
    canonical_stage: ExitStage | None,
) -> STProtectiveTimingRelation:
    if canonical_stage is None:
        return STProtectiveTimingRelation.NOT_COMPARED
    shadow_ready = shadow_state is STProtectiveShadowState.PROTECTIVE_INTENT
    canonical_ready = canonical_stage is ExitStage.EXIT_READY
    if shadow_ready and canonical_ready:
        return STProtectiveTimingRelation.ALIGNED
    if shadow_ready:
        return STProtectiveTimingRelation.SHADOW_EARLIER
    if canonical_ready:
        return STProtectiveTimingRelation.CANONICAL_EARLIER
    return STProtectiveTimingRelation.BOTH_INACTIVE


def _result(
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


def _timeframe_row(projection: Any | None, timeframe: str) -> Any | None:
    if projection is None:
        return None
    normalized = timeframe.strip().lower()
    getter = getattr(projection, "for_timeframe", None)
    if callable(getter):
        try:
            return getter(normalized)
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
    row = _timeframe_row(getattr(snapshot, "structure", None), timeframe)
    if row is None or getattr(row, "data_quality", None) is not ContextDataQuality.VALID:
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


def _reclaim_failure_from_ob(
    snapshot: "DecisionInputSnapshot",
    *,
    timeframe: str,
    entry_as_of: Any,
    anchor_identity: str,
    anchor_low: float,
    anchor_high: float,
) -> tuple[bool | None, tuple[FactRef, ...]]:
    projection = getattr(snapshot, "order_block_behavior", None)
    rows = () if projection is None else getattr(projection, "observations", ())
    exact_identity = anchor_identity.split(":", 1)[1] if anchor_identity.startswith("OB:") else None
    related: list[Any] = []
    for row in rows:
        if str(getattr(row, "timeframe", "")).strip().lower() != timeframe:
            continue
        if not bool(getattr(row, "bullish", False)):
            continue
        if exact_identity is not None and str(getattr(row, "identity", "")) != exact_identity:
            continue
        low = getattr(row, "bottom", None)
        high = getattr(row, "top", None)
        if low is None or high is None or not _ranges_overlap(low, high, anchor_low, anchor_high):
            continue
        ref = getattr(row, "ref", None)
        if not _causal_valid_ref(ref, snapshot.as_of) or not _confirmed_after_entry(ref, entry_as_of):
            continue
        related.append(row)
    if not related:
        return None, ()

    confirmed = any(
        str(getattr(row, "interaction", "")).strip().upper() == "REACTION_CONFIRMED"
        or str(getattr(row, "state", "")).strip().upper() == "REACTION_CONFIRMED"
        for row in related
    )
    failed = any(
        str(getattr(row, "interaction", "")).strip().upper() == "FAILED"
        or str(getattr(row, "state", "")).strip().upper() in {"CONSUMED", "EXPIRED_CANDIDATE"}
        for row in related
    )
    refs = _unique_refs(getattr(row, "ref") for row in related)
    if confirmed:
        return False, refs
    return failed, refs


def _reclaim_failure_from_fvg(
    snapshot: "DecisionInputSnapshot",
    *,
    timeframe: str,
    entry_as_of: Any,
    anchor_identity: str,
    anchor_low: float,
    anchor_high: float,
) -> tuple[bool | None, tuple[FactRef, ...]]:
    projection = getattr(snapshot, "fvg_engulfing_lifecycle", None)
    rows = () if projection is None else getattr(projection, "fvg", ())
    exact_identity = anchor_identity.split(":", 1)[1] if anchor_identity.startswith("FVG:") else None
    related: list[Any] = []
    for row in rows:
        ref = getattr(row, "ref", None)
        if str(getattr(ref, "timeframe", "")).strip().lower() != timeframe:
            continue
        if int(getattr(row, "direction", 0) or 0) != 1:
            continue
        if exact_identity is not None and str(getattr(row, "identity", "")) != exact_identity:
            continue
        low = getattr(row, "lower_boundary", None)
        high = getattr(row, "upper_boundary", None)
        if low is None or high is None or not _ranges_overlap(low, high, anchor_low, anchor_high):
            continue
        if not _causal_valid_ref(ref, snapshot.as_of) or not _confirmed_after_entry(ref, entry_as_of):
            continue
        related.append(row)
    if not related:
        return None, ()

    confirmed = any(bool(getattr(row, "reaction_confirmed", False)) for row in related)
    failed = any(
        bool(getattr(row, "failed_reaction", False))
        or bool(getattr(row, "full_fill", False))
        or bool(getattr(row, "invalid", False))
        for row in related
    )
    refs = _unique_refs(getattr(row, "ref") for row in related)
    if confirmed:
        return False, refs
    return failed, refs


def _buyer_reclaim_failure(
    snapshot: "DecisionInputSnapshot",
    *,
    timeframe: str,
    entry_as_of: Any,
    anchor_identity: str,
    anchor_low: float,
    anchor_high: float,
) -> tuple[bool | None, tuple[FactRef, ...]]:
    candidates: list[tuple[bool | None, tuple[FactRef, ...]]] = []
    if anchor_identity.startswith("OB:"):
        candidates.append(
            _reclaim_failure_from_ob(
                snapshot,
                timeframe=timeframe,
                entry_as_of=entry_as_of,
                anchor_identity=anchor_identity,
                anchor_low=anchor_low,
                anchor_high=anchor_high,
            )
        )
    elif anchor_identity.startswith("FVG:"):
        candidates.append(
            _reclaim_failure_from_fvg(
                snapshot,
                timeframe=timeframe,
                entry_as_of=entry_as_of,
                anchor_identity=anchor_identity,
                anchor_low=anchor_low,
                anchor_high=anchor_high,
            )
        )
    else:
        candidates.extend(
            (
                _reclaim_failure_from_ob(
                    snapshot,
                    timeframe=timeframe,
                    entry_as_of=entry_as_of,
                    anchor_identity=anchor_identity,
                    anchor_low=anchor_low,
                    anchor_high=anchor_high,
                ),
                _reclaim_failure_from_fvg(
                    snapshot,
                    timeframe=timeframe,
                    entry_as_of=entry_as_of,
                    anchor_identity=anchor_identity,
                    anchor_low=anchor_low,
                    anchor_high=anchor_high,
                ),
            )
        )

    known = [value for value, _ in candidates if value is not None]
    refs = _unique_refs(ref for _, item_refs in candidates for ref in item_refs)
    if not known:
        return None, refs
    if False in known:
        return False, refs
    return True, refs


def _breakout_range_reentry(
    snapshot: "DecisionInputSnapshot",
    *,
    timeframe: str,
    anchor_low: float,
    anchor_high: float,
) -> tuple[bool | None, tuple[FactRef, ...]]:
    row = _timeframe_row(getattr(snapshot, "support_resistance", None), timeframe)
    ref = None if row is None else getattr(row, "ref", None)
    if row is None or not _causal_valid_ref(ref, snapshot.as_of):
        return None, ()

    role_low = getattr(row, "role_reversal_support_low", None)
    role_high = getattr(row, "role_reversal_support_high", None)
    if (
        role_low is None
        or role_high is None
        or float(role_low) != float(anchor_low)
        or float(role_high) != float(anchor_high)
    ):
        return None, (ref,)

    location = str(getattr(row, "price_location", "") or "").strip().upper()
    native_state = str(getattr(row, "state", "") or "").strip().upper()
    returned_to_old_area = location in {"INSIDE_RANGE", "UPPER_ZONE", "LOWER_ZONE", "BELOW_RANGE"}
    failed_excursion = native_state == "RANGE_BREAK_FAILED"
    return returned_to_old_area or failed_excursion, (ref,)


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
        refs.extend(ref for ref in participation.source_refs if _causal_valid_ref(ref, snapshot.as_of))

    pattern = _timeframe_row(getattr(snapshot, "pattern_behavior", None), timeframe)
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
    """Measure thesis invalidation without changing canonical SELL or execution."""

    if state.position is not PositionState.OPEN:
        return _result(
            state=STProtectiveShadowState.NOT_APPLICABLE,
            family=None,
            canonical_stage=canonical_stage,
            reasons=("ST_PROTECTIVE_SHADOW_REQUIRES_OPEN_POSITION",),
        )

    metadata = state.entry_metadata
    if metadata is None or metadata.entry_horizon is not DecisionHorizon.SHORT_TERM:
        return _result(
            state=STProtectiveShadowState.NOT_APPLICABLE,
            family=None,
            canonical_stage=canonical_stage,
            reasons=("ST_PROTECTIVE_SHADOW_REQUIRES_ST_ENTRY_OWNERSHIP",),
        )

    memory = metadata.st_trade_memory
    if memory is None or memory.thesis_family is STThesisFamily.UNRESOLVED:
        return _result(
            state=STProtectiveShadowState.UNRESOLVED,
            family=None if memory is None else memory.thesis_family,
            canonical_stage=canonical_stage,
            reasons=("ST_PROTECTIVE_THESIS_IDENTITY_UNRESOLVED",),
        )

    anchor = memory.initial_defended_anchor
    if anchor is None:
        return _result(
            state=STProtectiveShadowState.UNRESOLVED,
            family=memory.thesis_family,
            canonical_stage=canonical_stage,
            reasons=("ST_PROTECTIVE_INITIAL_DEFENDED_ANCHOR_UNRESOLVED",),
        )

    timeframe = anchor.timeframe.strip().lower()
    if float(snapshot.current_price) >= float(anchor.low):
        return _result(
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
        entry_as_of=metadata.entry_as_of,
        anchor_identity=anchor.identity,
        anchor_low=float(anchor.low),
        anchor_high=float(anchor.high),
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
        structural_row = _timeframe_row(getattr(snapshot, "structure", None), timeframe)
        unresolved = structural_row is None or getattr(structural_row, "data_quality", None) is not ContextDataQuality.VALID
        return _result(
            state=STProtectiveShadowState.UNRESOLVED if unresolved else STProtectiveShadowState.NO_INTENT,
            family=memory.thesis_family,
            canonical_stage=canonical_stage,
            reasons=(
                "ST_PROTECTIVE_DOWNSIDE_STRUCTURE_UNRESOLVED"
                if unresolved
                else "ST_PROTECTIVE_DOWNSIDE_PROGRESS_NOT_CONFIRMED",
            ),
            primary=primary,
            secondary=secondary,
            refs=refs,
        )

    if reclaim_failed is None:
        return _result(
            state=STProtectiveShadowState.UNRESOLVED,
            family=memory.thesis_family,
            canonical_stage=canonical_stage,
            reasons=("ST_PROTECTIVE_BUYER_RECLAIM_STATUS_UNRESOLVED",),
            primary=primary,
            secondary=secondary,
            refs=refs,
        )
    if reclaim_failed is False:
        return _result(
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
            return _result(
                state=STProtectiveShadowState.UNRESOLVED,
                family=memory.thesis_family,
                canonical_stage=canonical_stage,
                reasons=("ST_PROTECTIVE_BREAKOUT_RANGE_RELATION_UNRESOLVED",),
                primary=primary,
                secondary=secondary,
                refs=refs,
            )
        if range_reentry is False:
            return _result(
                state=STProtectiveShadowState.NO_INTENT,
                family=memory.thesis_family,
                canonical_stage=canonical_stage,
                reasons=("ST_PROTECTIVE_OLD_RANGE_REENTRY_NOT_CONFIRMED",),
                primary=primary,
                secondary=secondary,
                refs=refs,
            )

    reason = {
        STThesisFamily.PULLBACK_CONTINUATION: "ST_PULLBACK_CONTINUATION_INVALIDATED",
        STThesisFamily.BREAKOUT_ACCEPTANCE: "ST_BREAKOUT_ACCEPTANCE_INVALIDATED",
        STThesisFamily.FAILED_SELL_RECLAIM: "ST_FAILED_SELL_RECLAIM_INVALIDATED",
    }[memory.thesis_family]
    return _result(
        state=STProtectiveShadowState.PROTECTIVE_INTENT,
        family=memory.thesis_family,
        canonical_stage=canonical_stage,
        reasons=(reason,),
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
