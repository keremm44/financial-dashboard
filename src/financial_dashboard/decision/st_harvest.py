from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Iterable

import pandas as pd

from financial_dashboard.context.envelope import ContextDataQuality, FactRef
from financial_dashboard.context.pattern_behavior_projection import PatternBehaviorPhase

from .lifecycle import PositionState, TradeLifecycleState
from .participation import ParticipationState, assess_participation
from .st_economic_history import (
    STContinuationEpisode,
    STContinuationEpisodeState,
    STEconomicHistory,
    STMissionCompletionMilestone,
    STProgressEvent,
    observe_st_economic_history,
)
from .st_protective import STProtectiveShadowState, assess_st_protective_shadow
from .st_thesis_identity import STThesisFamily
from .structural import (
    DecisionHorizon,
    StructuralDirection,
    ThesisState,
    assess_short_term_structure,
)

if TYPE_CHECKING:
    from financial_dashboard.decision_input import DecisionInputSnapshot


class STHarvestShadowState(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNRESOLVED = "UNRESOLVED"
    HOLD_MISSION_ACTIVE = "HOLD_MISSION_ACTIVE"
    HOLD_PROGRESS = "HOLD_PROGRESS"
    HOLD_CONTINUATION = "HOLD_CONTINUATION"
    HOLD_HEALTHY_BASE = "HOLD_HEALTHY_BASE"
    HOLD_UNCERTAIN = "HOLD_UNCERTAIN"
    PROFIT_HARVEST = "PROFIT_HARVEST"
    PROTECTIVE_PRECEDENCE = "PROTECTIVE_PRECEDENCE"


class STHealthyBaseState(StrEnum):
    UNRESOLVED = "UNRESOLVED"
    ABSENT = "ABSENT"
    CONFIRMED = "CONFIRMED"


@dataclass(frozen=True, slots=True)
class STHarvestShadowAssessment:
    state: STHarvestShadowState
    thesis_family: STThesisFamily | None
    mature: bool
    healthy_base_state: STHealthyBaseState
    reasons: tuple[str, ...]
    primary_evidence: tuple[str, ...]
    supporting_evidence: tuple[str, ...]
    source_refs: tuple[FactRef, ...]

    @property
    def consumed(self) -> bool:
        return self.state is STHarvestShadowState.PROFIT_HARVEST

    @property
    def healthy_base(self) -> bool:
        return self.healthy_base_state is STHealthyBaseState.CONFIRMED

    @property
    def protective_precedence(self) -> bool:
        return self.state is STHarvestShadowState.PROTECTIVE_PRECEDENCE


@dataclass(frozen=True, slots=True)
class _HealthyBaseAssessment:
    state: STHealthyBaseState
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]
    source_refs: tuple[FactRef, ...]


def _unique_refs(refs: Iterable[FactRef]) -> tuple[FactRef, ...]:
    by_key = {ref.deterministic_key: ref for ref in refs}
    return tuple(sorted(by_key.values(), key=lambda ref: ref.deterministic_key))


def _valid_ref(ref: FactRef | None, as_of: Any) -> bool:
    if ref is None or ref.data_quality is not ContextDataQuality.VALID:
        return False
    try:
        return ref.is_available_at(as_of)
    except TypeError:
        return False


def _overlap(low_a: float, high_a: float, low_b: float, high_b: float) -> bool:
    return max(float(low_a), float(low_b)) <= min(float(high_a), float(high_b))


def _timeframe_row(projection: Any | None, timeframe: str) -> Any | None:
    if projection is None:
        return None
    normalized = timeframe.strip().lower()
    method = getattr(projection, "for_timeframe", None)
    if callable(method):
        try:
            return method(normalized)
        except KeyError:
            return None
    return next(
        (
            row
            for row in getattr(projection, "timeframe_facts", ())
            if str(getattr(row, "timeframe", "")).strip().lower() == normalized
        ),
        None,
    )


def _result(
    *,
    state: STHarvestShadowState,
    family: STThesisFamily | None,
    mature: bool,
    healthy_base_state: STHealthyBaseState = STHealthyBaseState.ABSENT,
    reasons: Iterable[str],
    primary: Iterable[str] = (),
    supporting: Iterable[str] = (),
    refs: Iterable[FactRef] = (),
) -> STHarvestShadowAssessment:
    return STHarvestShadowAssessment(
        state=state,
        thesis_family=family,
        mature=mature,
        healthy_base_state=healthy_base_state,
        reasons=tuple(dict.fromkeys(reasons)),
        primary_evidence=tuple(dict.fromkeys(primary)),
        supporting_evidence=tuple(dict.fromkeys(supporting)),
        source_refs=_unique_refs(refs),
    )


def _mission_cutoff(
    history: STEconomicHistory,
    mission: STMissionCompletionMilestone,
) -> tuple[pd.Timestamp, STProgressEvent | None]:
    mission_at = pd.Timestamp(mission.observed_at)
    later = tuple(
        progress
        for progress in history.progress_events
        if pd.Timestamp(progress.observed_at) > mission_at
    )
    if not later:
        return mission_at, None
    latest = max(
        later,
        key=lambda progress: (pd.Timestamp(progress.observed_at), progress.event_id),
    )
    return pd.Timestamp(latest.observed_at), latest


def _episodes_after(
    history: STEconomicHistory,
    cutoff: pd.Timestamp,
) -> tuple[STContinuationEpisode, ...]:
    return tuple(
        sorted(
            (
                episode
                for episode in history.continuation_episodes
                if pd.Timestamp(episode.formed_at) > cutoff
            ),
            key=lambda episode: (pd.Timestamp(episode.formed_at), episode.episode_id),
        )
    )


def _current_downside_progress_below_defense(
    snapshot: "DecisionInputSnapshot",
    *,
    timeframe: str,
    defense_low: float,
    defense_observed_at: Any,
) -> tuple[bool | None, tuple[FactRef, ...]]:
    row = _timeframe_row(getattr(snapshot, "structure", None), timeframe)
    if row is None or getattr(row, "data_quality", ContextDataQuality.UNAVAILABLE) is not ContextDataQuality.VALID:
        return None, ()

    refs: list[FactRef] = []
    for event in getattr(row, "events", ()):
        ref = getattr(event, "ref", None)
        if not _valid_ref(ref, snapshot.as_of):
            continue
        if str(getattr(event, "confirmation_status", "")).strip().upper() != "CONFIRMED":
            continue
        if str(getattr(event, "validity", "")).strip().upper() != "VALID":
            continue
        if str(getattr(event, "relevance", "")).strip().upper() not in {"CURRENT", "ACTIVE"}:
            continue
        if int(getattr(event, "direction", 0) or 0) != -1:
            continue
        confirmed_at = getattr(ref, "confirmed_at", None)
        if confirmed_at is None or pd.Timestamp(confirmed_at) <= pd.Timestamp(defense_observed_at):
            continue
        broken_level = getattr(event, "broken_level", None)
        if broken_level is None or float(broken_level) > float(defense_low):
            continue
        refs.append(ref)
    return bool(refs), _unique_refs(refs)


def _buyer_reaction_at_defense(
    snapshot: "DecisionInputSnapshot",
    *,
    timeframe: str,
    defense_low: float,
    defense_high: float,
) -> tuple[bool | None, tuple[str, ...], tuple[FactRef, ...]]:
    normalized = timeframe.strip().lower()
    valid_related_seen = False
    unavailable_related_seen = False
    alive = False
    evidence: list[str] = []
    refs: list[FactRef] = []

    order_blocks = getattr(snapshot, "order_block_behavior", None)
    if order_blocks is not None:
        for item in getattr(order_blocks, "observations", ()):
            if str(getattr(item, "timeframe", "")).strip().lower() != normalized:
                continue
            if not bool(getattr(item, "bullish", False)):
                continue
            if not _overlap(
                float(getattr(item, "bottom")),
                float(getattr(item, "top")),
                defense_low,
                defense_high,
            ):
                continue
            ref = getattr(item, "ref", None)
            if not _valid_ref(ref, snapshot.as_of):
                unavailable_related_seen = True
                continue
            valid_related_seen = True
            refs.append(ref)
            state = str(getattr(item, "state", "") or "").strip().upper()
            interaction = str(getattr(item, "interaction", "") or "").strip().upper()
            failed = interaction == "FAILED" or state in {"CONSUMED", "EXPIRED_CANDIDATE"}
            if failed:
                continue
            if interaction == "REACTION_CONFIRMED" or state == "REACTION_CONFIRMED":
                alive = True
                evidence.append(
                    f"HEALTHY_BASE_BUYER_REACTION_CONFIRMED:OB:{getattr(item, 'identity', 'UNKNOWN')}"
                )
            elif bool(getattr(item, "active", False)) and interaction in {
                "APPROACHING",
                "ENTERED",
                "DWELLING_INSIDE",
                "EXITING_FAVORABLE",
                "HOLDING_FAVORABLE",
            }:
                alive = True
                evidence.append(
                    f"HEALTHY_BASE_BUYER_REACTION_DEVELOPING:OB:{getattr(item, 'identity', 'UNKNOWN')}"
                )

    lifecycle = getattr(snapshot, "fvg_engulfing_lifecycle", None)
    if lifecycle is not None:
        for item in getattr(lifecycle, "fvg", ()):
            ref = getattr(item, "ref", None)
            if str(getattr(ref, "timeframe", "")).strip().lower() != normalized:
                continue
            if int(getattr(item, "direction", 0) or 0) != 1:
                continue
            if not _overlap(
                float(getattr(item, "lower_boundary")),
                float(getattr(item, "upper_boundary")),
                defense_low,
                defense_high,
            ):
                continue
            if not _valid_ref(ref, snapshot.as_of):
                unavailable_related_seen = True
                continue
            valid_related_seen = True
            refs.append(ref)
            failed = any(
                bool(getattr(item, field, False))
                for field in ("failed_reaction", "full_fill", "invalid")
            )
            if failed:
                continue
            if bool(getattr(item, "reaction_confirmed", False)):
                alive = True
                evidence.append(
                    f"HEALTHY_BASE_BUYER_REACTION_CONFIRMED:FVG:{getattr(item, 'identity', 'UNKNOWN')}"
                )
            elif getattr(item, "first_test_index", None) is not None:
                alive = True
                evidence.append(
                    f"HEALTHY_BASE_BUYER_REACTION_DEVELOPING:FVG:{getattr(item, 'identity', 'UNKNOWN')}"
                )

    if alive:
        return True, tuple(evidence), _unique_refs(refs)
    if valid_related_seen:
        return False, (), _unique_refs(refs)
    if unavailable_related_seen or (order_blocks is None and lifecycle is None):
        return None, (), _unique_refs(refs)
    return False, (), _unique_refs(refs)


def _participation_controlled(
    snapshot: "DecisionInputSnapshot",
    *,
    timeframe: str,
) -> tuple[bool | None, tuple[str, ...], tuple[FactRef, ...]]:
    projection = getattr(snapshot, "participation_behavior", None)
    assessment = assess_participation(
        StructuralDirection.LONG,
        projection,
        timeframe=timeframe,
    )
    refs = _unique_refs(
        ref for ref in assessment.source_refs if _valid_ref(ref, snapshot.as_of)
    )
    if (
        assessment.state is ParticipationState.UNKNOWN
        or assessment.data_quality is not ContextDataQuality.VALID
    ):
        return None, (), refs
    if assessment.state in {ParticipationState.OPPOSING, ParticipationState.WEAK}:
        return False, (), refs
    if assessment.state is ParticipationState.SUPPORTIVE:
        return True, ("HEALTHY_BASE_PARTICIPATION_SUPPORTIVE",), refs

    row = _timeframe_row(projection, timeframe)
    if row is None:
        return None, (), refs
    controlled = bool(
        getattr(row, "controlled_pullback", False)
        or getattr(row, "controlled_reaction", False)
    )
    absorption = str(
        getattr(getattr(row, "absorption", None), "value", getattr(row, "absorption", ""))
        or ""
    ).strip().upper()
    if controlled:
        return True, ("HEALTHY_BASE_PARTICIPATION_CONTROLLED",), refs
    if absorption == "CONFIRMED":
        return True, ("HEALTHY_BASE_PARTICIPATION_ABSORPTIVE",), refs
    return False, (), refs


_POSITIVE_PATTERN_PHASES = frozenset(
    {
        PatternBehaviorPhase.MATURE_COMPRESSION,
        PatternBehaviorPhase.BREAK_ATTEMPT,
        PatternBehaviorPhase.BREAK_CONFIRMING,
        PatternBehaviorPhase.BREAK_CONFIRMED,
        PatternBehaviorPhase.POST_BREAK_RETEST,
        PatternBehaviorPhase.RETEST_HELD,
    }
)


def _pattern_preparation(
    snapshot: "DecisionInputSnapshot",
    *,
    timeframe: str,
) -> tuple[bool | None, tuple[str, ...], tuple[FactRef, ...]]:
    row = _timeframe_row(getattr(snapshot, "pattern_behavior", None), timeframe)
    ref = None if row is None else getattr(row, "ref", None)
    if row is None or not _valid_ref(ref, snapshot.as_of):
        return None, (), ()
    phase = getattr(row, "phase", PatternBehaviorPhase.UNAVAILABLE)
    direction = int(getattr(row, "classic_direction", 0) or 0)
    if phase in _POSITIVE_PATTERN_PHASES and direction == 1:
        return True, (f"HEALTHY_BASE_PATTERN_PREPARATION:{phase.value}",), (ref,)
    return False, (), (ref,)


def _healthy_base(
    snapshot: "DecisionInputSnapshot",
    state: TradeLifecycleState,
    history: STEconomicHistory,
    *,
    mission: STMissionCompletionMilestone,
    live_episode: STContinuationEpisode | None,
) -> _HealthyBaseAssessment:
    metadata = state.entry_metadata
    memory = None if metadata is None else metadata.st_trade_memory
    anchor = None if memory is None else memory.initial_defended_anchor
    defense = history.active_earned_defense
    if metadata is None or anchor is None or defense is None:
        return _HealthyBaseAssessment(
            STHealthyBaseState.ABSENT,
            ("HEALTHY_BASE_REQUIRES_GAINED_EARNED_DEFENSE",),
            (),
            (),
        )
    if float(defense.low) <= float(anchor.low):
        return _HealthyBaseAssessment(
            STHealthyBaseState.ABSENT,
            ("HEALTHY_BASE_RISK_BOUNDARY_NOT_EARNED_ABOVE_INITIAL_GROUND",),
            (),
            (),
        )
    if float(snapshot.current_price) < float(defense.low):
        return _HealthyBaseAssessment(
            STHealthyBaseState.ABSENT,
            ("HEALTHY_BASE_EARNED_DEFENSE_NOT_PROTECTED",),
            (),
            (),
        )

    evidence: list[str] = [
        "HEALTHY_BASE_GAINED_AREA_PROTECTED",
        "HEALTHY_BASE_EARNED_RISK_BOUNDARY_PRESENT",
    ]
    refs: list[FactRef] = []

    structural = assess_short_term_structure(snapshot.structure)
    refs.extend(
        ref for ref in structural.source_refs if _valid_ref(ref, snapshot.as_of)
    )
    if structural.data_quality is not ContextDataQuality.VALID:
        return _HealthyBaseAssessment(
            STHealthyBaseState.UNRESOLVED,
            ("HEALTHY_BASE_ST_STRUCTURE_UNRESOLVED",),
            tuple(evidence),
            _unique_refs(refs),
        )
    if not (
        structural.direction is StructuralDirection.LONG
        and structural.thesis_state is ThesisState.INTACT
    ):
        return _HealthyBaseAssessment(
            STHealthyBaseState.ABSENT,
            ("HEALTHY_BASE_ST_STRUCTURE_NOT_INTACT_LONG",),
            tuple(evidence),
            _unique_refs(refs),
        )
    evidence.append("HEALTHY_BASE_ST_STRUCTURE_INTACT")

    downside, downside_refs = _current_downside_progress_below_defense(
        snapshot,
        timeframe="1h",
        defense_low=float(defense.low),
        defense_observed_at=defense.observed_at,
    )
    refs.extend(downside_refs)
    if downside is None:
        return _HealthyBaseAssessment(
            STHealthyBaseState.UNRESOLVED,
            ("HEALTHY_BASE_DOWNSIDE_PROGRESS_STATUS_UNRESOLVED",),
            tuple(evidence),
            _unique_refs(refs),
        )
    if downside:
        return _HealthyBaseAssessment(
            STHealthyBaseState.ABSENT,
            ("HEALTHY_BASE_MEANINGFUL_DOWNSIDE_PROGRESS_PRESENT",),
            tuple(evidence),
            _unique_refs(refs),
        )
    evidence.append("HEALTHY_BASE_NO_MEANINGFUL_DOWNSIDE_PROGRESS")

    reaction, reaction_evidence, reaction_refs = _buyer_reaction_at_defense(
        snapshot,
        timeframe="1h",
        defense_low=float(defense.low),
        defense_high=float(defense.high),
    )
    refs.extend(reaction_refs)
    if reaction is None:
        return _HealthyBaseAssessment(
            STHealthyBaseState.UNRESOLVED,
            ("HEALTHY_BASE_BUYER_REACTION_UNRESOLVED",),
            tuple(evidence),
            _unique_refs(refs),
        )
    if reaction is False:
        return _HealthyBaseAssessment(
            STHealthyBaseState.ABSENT,
            ("HEALTHY_BASE_BUYER_REACTION_NOT_ALIVE",),
            tuple(evidence),
            _unique_refs(refs),
        )
    evidence.extend(reaction_evidence)

    participation, participation_evidence, participation_refs = _participation_controlled(
        snapshot,
        timeframe="1h",
    )
    refs.extend(participation_refs)
    if participation is None:
        return _HealthyBaseAssessment(
            STHealthyBaseState.UNRESOLVED,
            ("HEALTHY_BASE_PARTICIPATION_UNRESOLVED",),
            tuple(evidence),
            _unique_refs(refs),
        )
    if participation is False:
        return _HealthyBaseAssessment(
            STHealthyBaseState.ABSENT,
            ("HEALTHY_BASE_PARTICIPATION_NOT_CONTROLLED",),
            tuple(evidence),
            _unique_refs(refs),
        )
    evidence.extend(participation_evidence)

    if (
        live_episode is not None
        and live_episode.state is STContinuationEpisodeState.LIVE
        and pd.Timestamp(live_episode.formed_at) > pd.Timestamp(mission.observed_at)
    ):
        evidence.append(f"HEALTHY_BASE_LIVE_CONTINUATION:{live_episode.episode_id}")
        preparation = True
    else:
        preparation, pattern_evidence, pattern_refs = _pattern_preparation(
            snapshot,
            timeframe="1h",
        )
        refs.extend(pattern_refs)
        if preparation is True:
            evidence.extend(pattern_evidence)

    if preparation is None:
        return _HealthyBaseAssessment(
            STHealthyBaseState.UNRESOLVED,
            ("HEALTHY_BASE_EXPANSION_PREPARATION_UNRESOLVED",),
            tuple(evidence),
            _unique_refs(refs),
        )
    if preparation is False:
        return _HealthyBaseAssessment(
            STHealthyBaseState.ABSENT,
            ("HEALTHY_BASE_NO_CONCRETE_EXPANSION_PREPARATION",),
            tuple(evidence),
            _unique_refs(refs),
        )

    return _HealthyBaseAssessment(
        STHealthyBaseState.CONFIRMED,
        ("HEALTHY_BASE_COHERENT_POSITIVE_PREPARATION",),
        tuple(evidence),
        _unique_refs(refs),
    )


def assess_st_harvest_shadow(
    snapshot: "DecisionInputSnapshot",
    state: TradeLifecycleState,
) -> STHarvestShadowAssessment:
    """Derive Step-6 HOLD/HARVEST interpretation without changing canonical action.

    Maturity, healthy-base status and CONSUMED are derived from immutable entry memory,
    causal economic history and the current causal snapshot. None of those policy
    conclusions are persisted here. Protective invalidation always takes precedence.
    """

    if state.position is not PositionState.OPEN:
        return _result(
            state=STHarvestShadowState.NOT_APPLICABLE,
            family=None,
            mature=False,
            reasons=("ST_HARVEST_SHADOW_REQUIRES_OPEN_POSITION",),
        )

    metadata = state.entry_metadata
    if metadata is None or metadata.entry_horizon is not DecisionHorizon.SHORT_TERM:
        return _result(
            state=STHarvestShadowState.NOT_APPLICABLE,
            family=None,
            mature=False,
            reasons=("ST_HARVEST_SHADOW_REQUIRES_ST_ENTRY_OWNERSHIP",),
        )

    memory = metadata.st_trade_memory
    if memory is None or memory.thesis_family is STThesisFamily.UNRESOLVED:
        return _result(
            state=STHarvestShadowState.UNRESOLVED,
            family=None if memory is None else memory.thesis_family,
            mature=False,
            healthy_base_state=STHealthyBaseState.UNRESOLVED,
            reasons=("ST_HARVEST_THESIS_IDENTITY_UNRESOLVED",),
        )

    history = observe_st_economic_history(snapshot, state)
    if history is None:
        return _result(
            state=STHarvestShadowState.UNRESOLVED,
            family=memory.thesis_family,
            mature=False,
            healthy_base_state=STHealthyBaseState.UNRESOLVED,
            reasons=("ST_HARVEST_ECONOMIC_HISTORY_UNRESOLVED",),
        )

    mature = history.mission_completion is not None
    protective = assess_st_protective_shadow(snapshot, state)
    if protective.state is STProtectiveShadowState.PROTECTIVE_INTENT:
        return _result(
            state=STHarvestShadowState.PROTECTIVE_PRECEDENCE,
            family=memory.thesis_family,
            mature=mature,
            healthy_base_state=STHealthyBaseState.ABSENT,
            reasons=("ST_PROTECTIVE_INVALIDATION_OUTRANKS_HARVEST", *protective.reasons),
            primary=protective.primary_evidence,
            supporting=protective.secondary_evidence,
            refs=protective.source_refs,
        )
    if protective.state is STProtectiveShadowState.UNRESOLVED:
        return _result(
            state=STHarvestShadowState.UNRESOLVED,
            family=memory.thesis_family,
            mature=mature,
            healthy_base_state=STHealthyBaseState.UNRESOLVED,
            reasons=("ST_HARVEST_THESIS_VALIDITY_UNRESOLVED", *protective.reasons),
            refs=protective.source_refs,
        )

    mission = history.mission_completion
    if mission is None:
        return _result(
            state=STHarvestShadowState.HOLD_MISSION_ACTIVE,
            family=memory.thesis_family,
            mature=False,
            reasons=("ST_INITIAL_ECONOMIC_MISSION_NOT_COMPLETED",),
        )

    cutoff, later_progress = _mission_cutoff(history, mission)
    post_progress_episodes = _episodes_after(history, cutoff)
    live_episodes = tuple(
        episode
        for episode in post_progress_episodes
        if episode.state is STContinuationEpisodeState.LIVE
    )
    failed_episodes = tuple(
        episode
        for episode in post_progress_episodes
        if episode.state is STContinuationEpisodeState.FAILED
    )
    live_episode = live_episodes[-1] if live_episodes else None
    failure = failed_episodes[-1] if failed_episodes else None

    healthy = _healthy_base(
        snapshot,
        state,
        history,
        mission=mission,
        live_episode=live_episode,
    )
    primary: list[str] = [
        "INITIAL_MISSION_MATERIALLY_COMPLETED",
        "ST_THESIS_STILL_VALID",
    ]
    supporting: list[str] = list(healthy.evidence)
    refs: list[FactRef] = [*protective.source_refs, *healthy.source_refs]

    if healthy.state is STHealthyBaseState.CONFIRMED:
        return _result(
            state=STHarvestShadowState.HOLD_HEALTHY_BASE,
            family=memory.thesis_family,
            mature=True,
            healthy_base_state=healthy.state,
            reasons=("ST_MATURE_TRADE_BUILDING_HEALTHY_BASE", *healthy.reasons),
            primary=primary,
            supporting=supporting,
            refs=refs,
        )

    if live_episode is not None:
        primary.append(f"CONTINUATION_EPISODE_LIVE:{live_episode.episode_id}")
        return _result(
            state=STHarvestShadowState.HOLD_CONTINUATION,
            family=memory.thesis_family,
            mature=True,
            healthy_base_state=healthy.state,
            reasons=("ST_CONTINUATION_OPPORTUNITY_STILL_LIVE", *healthy.reasons),
            primary=primary,
            supporting=supporting,
            refs=refs,
        )

    if failure is None:
        if later_progress is not None:
            primary.append(f"POST_MISSION_PROGRESS:{later_progress.event_id}")
            reason = "ST_NEW_PROGRESS_NOT_YET_SHOWN_CONSUMED"
            state_value = STHarvestShadowState.HOLD_PROGRESS
        else:
            reason = "ST_CONSUMED_STORY_LACKS_FAILED_CONTINUATION"
            state_value = STHarvestShadowState.HOLD_UNCERTAIN
        return _result(
            state=state_value,
            family=memory.thesis_family,
            mature=True,
            healthy_base_state=healthy.state,
            reasons=(reason, *healthy.reasons),
            primary=primary,
            supporting=supporting,
            refs=refs,
        )

    primary.extend(
        (
            f"CONTINUATION_EPISODE_FAILED:{failure.episode_id}",
            "NO_NEW_ACCEPTED_PROGRESS_AFTER_FAILED_CONTINUATION",
        )
    )
    if healthy.state is STHealthyBaseState.UNRESOLVED:
        return _result(
            state=STHarvestShadowState.HOLD_UNCERTAIN,
            family=memory.thesis_family,
            mature=True,
            healthy_base_state=healthy.state,
            reasons=("ST_HEALTHY_BASE_STATUS_UNRESOLVED", *healthy.reasons),
            primary=primary,
            supporting=supporting,
            refs=refs,
        )

    return _result(
        state=STHarvestShadowState.PROFIT_HARVEST,
        family=memory.thesis_family,
        mature=True,
        healthy_base_state=STHealthyBaseState.ABSENT,
        reasons=("ST_FULL_CONSUMED_ECONOMIC_STORY_PRESENT", *healthy.reasons),
        primary=(
            *primary,
            "NO_CONCRETE_HEALTHY_BASE",
        ),
        supporting=supporting,
        refs=refs,
    )


__all__ = [
    "STHarvestShadowAssessment",
    "STHarvestShadowState",
    "STHealthyBaseState",
    "assess_st_harvest_shadow",
]
