from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Iterable

import pandas as pd

from financial_dashboard.context.envelope import ContextDataQuality, FactRef, normalize_context_data_quality

from .evidence_quality import normalize_decision_reaction_projections
from .lifecycle import ExitStage
from .participation import ParticipationState, assess_participation
from .reaction import ReactionRelevancePolicy, assess_reaction, select_relevant_zones
from .stabil_authority import assess_stabil_authority
from .structural import StructuralDirection, ThesisState
from .trade_exit import LongExitAssessment, PositionHealth

if TYPE_CHECKING:
    from financial_dashboard.decision_input import DecisionInputSnapshot
    from .structural import StructuralAssessment


class STBearishReversalState(StrEnum):
    NONE = "NONE"
    WATCH = "WATCH"
    DEVELOPING = "DEVELOPING"
    STRONG = "STRONG"


@dataclass(frozen=True, slots=True)
class STBearishReversalAssessment:
    state: STBearishReversalState
    current_bearish_choch: bool
    bearish_reaction_confirmed: bool
    participation_supportive: bool
    stabil_breakdown_supportive: bool
    participation_opposes: bool
    reasons: tuple[str, ...]
    source_refs: tuple[FactRef, ...]

    @property
    def can_arm_exit(self) -> bool:
        return self.state is STBearishReversalState.STRONG


def _event_time(event: Any) -> pd.Timestamp | None:
    ref = getattr(event, "ref", None)
    if ref is None:
        return None
    value = getattr(ref, "available_at", None) or getattr(ref, "confirmed_at", None)
    if value is None:
        return None
    try:
        return pd.Timestamp(value)
    except (TypeError, ValueError):
        return None


def _eligible_external_events(snapshot: "DecisionInputSnapshot") -> tuple[object, ...]:
    try:
        row = snapshot.structure.for_timeframe("1h")
    except (KeyError, AttributeError, TypeError):
        return ()

    values: list[object] = []
    for event in row.events:
        if str(event.scope).strip().upper() != "EXTERNAL":
            continue
        if str(event.confirmation_status).strip().upper() != "CONFIRMED":
            continue
        if str(event.validity).strip().upper() != "VALID":
            continue
        quality = normalize_context_data_quality(event.ref.data_quality)
        if quality not in {ContextDataQuality.VALID, ContextDataQuality.DATA_LIMITED}:
            continue
        timestamp = _event_time(event)
        if timestamp is None or timestamp > pd.Timestamp(snapshot.as_of):
            continue
        values.append(event)
    return tuple(values)


def _latest_event(
    events: Iterable[object],
    *,
    direction: int,
    event_type: str,
):
    token = event_type.strip().upper()
    candidates: list[tuple[pd.Timestamp, object]] = []
    for event in events:
        if int(getattr(event, "direction", 0)) != int(direction):
            continue
        if str(getattr(event, "event_type", "")).strip().upper() != token:
            continue
        timestamp = _event_time(event)
        if timestamp is not None:
            candidates.append((timestamp, event))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _current_bearish_choch(snapshot: "DecisionInputSnapshot"):
    """Return a bearish external CHoCH only while no later bullish reset supersedes it."""

    events = _eligible_external_events(snapshot)
    bearish_choch = _latest_event(events, direction=-1, event_type="EVENT_CHOCH")
    if bearish_choch is None:
        return None

    bearish_time = _event_time(bearish_choch)
    if bearish_time is None:
        return None

    bullish_choch = _latest_event(events, direction=1, event_type="EVENT_CHOCH")
    bullish_bos = _latest_event(events, direction=1, event_type="EVENT_BOS")
    reset_times = tuple(
        timestamp
        for timestamp in (_event_time(bullish_choch), _event_time(bullish_bos))
        if timestamp is not None
    )
    if reset_times and max(reset_times) > bearish_time:
        return None
    return bearish_choch


def _unique_refs(*groups: Iterable[FactRef]) -> tuple[FactRef, ...]:
    values = {
        ref.deterministic_key: ref
        for group in groups
        for ref in group
    }
    return tuple(sorted(values.values(), key=lambda ref: ref.deterministic_key))


def assess_st_bearish_reversal(
    snapshot: "DecisionInputSnapshot",
    native_structural: "StructuralAssessment",
    *,
    reaction_relevance: ReactionRelevancePolicy | None = ReactionRelevancePolicy(),
    participation_conflict_max_age_bars: int | None = 24,
) -> STBearishReversalAssessment:
    """Detect an early bearish reversal against an open short-term long.

    The evidence assessment may describe WATCH/DEVELOPING states for diagnostics, but
    those states are deliberately non-actionable. Only a multi-family STRONG reversal
    may alter short-term position management.
    """

    if (
        native_structural.data_quality is not ContextDataQuality.VALID
        or native_structural.direction is not StructuralDirection.LONG
        or native_structural.thesis_state is not ThesisState.INTACT
    ):
        return STBearishReversalAssessment(
            STBearishReversalState.NONE,
            False,
            False,
            False,
            False,
            False,
            ("ST_EARLY_BEARISH_REVERSAL_NOT_APPLICABLE",),
            (),
        )

    reaction_ob, reaction_fvg = normalize_decision_reaction_projections(
        snapshot.order_block_behavior,
        snapshot.fvg_engulfing_lifecycle,
    )
    if reaction_relevance is not None:
        reaction_ob, reaction_fvg = select_relevant_zones(
            reaction_ob,
            reaction_fvg,
            current_price=snapshot.current_price,
            policy=reaction_relevance,
        )

    reaction = assess_reaction(
        StructuralDirection.SHORT,
        order_blocks=reaction_ob,
        fvg_engulfing=reaction_fvg,
        timeframes=("4h", "2h", "1h", "30m"),
        relevance=reaction_relevance,
    )
    participation = assess_participation(
        StructuralDirection.SHORT,
        snapshot.participation_behavior,
        timeframe="1h",
        max_heavy_conflict_age_bars=participation_conflict_max_age_bars,
    )
    stabil = assess_stabil_authority(getattr(snapshot, "stabil_support", None))
    choch = _current_bearish_choch(snapshot)

    current_bearish_choch = choch is not None
    bearish_reaction_confirmed = bool(reaction.confirmation_present)
    bearish_reaction_developing = bool(reaction.developing_present)
    participation_supportive = participation.state is ParticipationState.SUPPORTIVE
    participation_opposes = bool(
        participation.state is ParticipationState.OPPOSING
        or getattr(participation, "heavy_conflict", False)
    )
    stabil_breakdown_supportive = bool(
        stabil.breakdown_developing or stabil.breakdown_confirmed
    )

    reasons: list[str] = []
    if current_bearish_choch:
        reasons.append("CURRENT_EXTERNAL_BEARISH_CHOCH")
    if bearish_reaction_confirmed:
        reasons.append("BEARISH_REACTION_CONFIRMED")
    elif bearish_reaction_developing:
        reasons.append("BEARISH_REACTION_DEVELOPING")
    if participation_supportive:
        reasons.append("BEARISH_PARTICIPATION_SUPPORTIVE")
    elif participation_opposes:
        reasons.append("PARTICIPATION_OPPOSES_BEARISH_REVERSAL")
    if stabil_breakdown_supportive:
        reasons.append("STABIL_BREAKDOWN_SUPPORTS_BEARISH_REVERSAL")

    independent_confirmation = participation_supportive or stabil_breakdown_supportive
    strong = bool(
        current_bearish_choch
        and bearish_reaction_confirmed
        and independent_confirmation
        and not participation_opposes
    )
    developing = bool(
        current_bearish_choch
        and (bearish_reaction_confirmed or bearish_reaction_developing)
    )
    watch = bool(current_bearish_choch or bearish_reaction_confirmed or bearish_reaction_developing)

    if strong:
        state = STBearishReversalState.STRONG
        reasons.append("ST_BEARISH_REVERSAL_MULTI_FAMILY_CONFIRMED")
    elif developing:
        state = STBearishReversalState.DEVELOPING
        reasons.append("ST_BEARISH_REVERSAL_REQUIRES_INDEPENDENT_CONFIRMATION")
    elif watch:
        state = STBearishReversalState.WATCH
        reasons.append("ST_BEARISH_REVERSAL_WATCH_ONLY")
    else:
        state = STBearishReversalState.NONE
        reasons.append("NO_CURRENT_ST_BEARISH_REVERSAL_EVIDENCE")

    structural_refs = () if choch is None else (choch.ref,)
    refs = _unique_refs(
        structural_refs,
        reaction.source_refs,
        participation.source_refs,
        stabil.source_refs,
    )
    return STBearishReversalAssessment(
        state,
        current_bearish_choch,
        bearish_reaction_confirmed,
        participation_supportive,
        stabil_breakdown_supportive,
        participation_opposes,
        tuple(dict.fromkeys(reasons)),
        refs,
    )


def refine_short_term_exit_with_bearish_reversal(
    assessment: LongExitAssessment,
    reversal: STBearishReversalAssessment | None,
) -> LongExitAssessment:
    """Only a STRONG bearish reversal may change short-term exit maturity.

    WATCH and DEVELOPING remain diagnostics. They must not turn a healthy short-term
    position into EXIT_WATCH or add a waiting requirement. This preserves trend carry
    until the bearish reversal is actually multi-family confirmed.
    """

    if reversal is None or not reversal.can_arm_exit:
        return assessment

    refs = _unique_refs(assessment.source_refs, reversal.source_refs)
    reasons = tuple(dict.fromkeys((*assessment.reasons, *reversal.reasons)))

    if assessment.stage is ExitStage.EXIT_READY:
        return LongExitAssessment(
            ExitStage.EXIT_READY,
            assessment.position_health,
            reasons,
            assessment.waiting_for,
            refs,
        )

    return LongExitAssessment(
        ExitStage.EXIT_READY,
        PositionHealth.PRESSURED,
        tuple(dict.fromkeys((*reasons, "ST_STRONG_BEARISH_REVERSAL_ARMS_EXIT"))),
        ("FRESH_LONG_EXIT_EXECUTION_EVENT",),
        refs,
    )


__all__ = [
    "STBearishReversalAssessment",
    "STBearishReversalState",
    "assess_st_bearish_reversal",
    "refine_short_term_exit_with_bearish_reversal",
]
