from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from math import isfinite
from typing import TYPE_CHECKING, Any, Iterable, Mapping

import pandas as pd

from financial_dashboard.context.envelope import ContextDataQuality

from .structural import DecisionHorizon

if TYPE_CHECKING:
    from financial_dashboard.decision_input import DecisionInputSnapshot
    from .lifecycle import TradeLifecycleState
    from .position_metadata import PositionEntryMetadata


class STContinuationEpisodeState(StrEnum):
    LIVE = "LIVE"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class STAcceptedAreaEvent:
    event_id: str
    observed_at: Any
    timeframe: str
    low: float
    high: float
    break_boundary: float

    def __post_init__(self) -> None:
        _identity(self.event_id, "accepted-area")
        _identity(self.timeframe, "accepted-area timeframe")
        _timestamp(self.observed_at, "accepted-area observed_at")
        _band(self.low, self.high, "accepted-area")
        _finite(self.break_boundary, "accepted-area break boundary")


@dataclass(frozen=True, slots=True)
class STEarnedDefenseEvent:
    event_id: str
    observed_at: Any
    accepted_area_id: str
    low: float
    high: float

    def __post_init__(self) -> None:
        _identity(self.event_id, "earned-defense")
        _identity(self.accepted_area_id, "earned-defense accepted-area")
        _timestamp(self.observed_at, "earned-defense observed_at")
        _band(self.low, self.high, "earned-defense")


@dataclass(frozen=True, slots=True)
class STProgressEvent:
    event_id: str
    observed_at: Any
    accepted_area_id: str
    accepted_floor: float
    distance_from_entry: float

    def __post_init__(self) -> None:
        _identity(self.event_id, "progress")
        _identity(self.accepted_area_id, "progress accepted-area")
        _timestamp(self.observed_at, "progress observed_at")
        _finite(self.accepted_floor, "progress accepted floor")
        _finite(self.distance_from_entry, "progress distance")
        if float(self.distance_from_entry) <= 0.0:
            raise ValueError("ST progress distance must be positive")


@dataclass(frozen=True, slots=True)
class STMissionCompletionMilestone:
    event_id: str
    observed_at: Any
    target_identity: str
    accepted_area_id: str

    def __post_init__(self) -> None:
        _identity(self.event_id, "mission-completion")
        _identity(self.target_identity, "mission-completion target")
        _identity(self.accepted_area_id, "mission-completion accepted-area")
        _timestamp(self.observed_at, "mission-completion observed_at")


@dataclass(frozen=True, slots=True)
class STContinuationEpisode:
    episode_id: str
    source_identity: str
    timeframe: str
    formed_at: Any
    first_observed_at: Any
    lower_boundary: float
    upper_boundary: float
    state: STContinuationEpisodeState = STContinuationEpisodeState.LIVE
    completed_at: Any | None = None
    accepted_area_id: str | None = None

    def __post_init__(self) -> None:
        _identity(self.episode_id, "continuation episode")
        _identity(self.source_identity, "continuation source")
        _identity(self.timeframe, "continuation timeframe")
        _timestamp(self.formed_at, "continuation formed_at")
        _timestamp(self.first_observed_at, "continuation first_observed_at")
        _band(self.lower_boundary, self.upper_boundary, "continuation")
        terminal = self.state in {
            STContinuationEpisodeState.SUCCEEDED,
            STContinuationEpisodeState.FAILED,
        }
        if terminal != (self.completed_at is not None):
            raise ValueError("terminal ST continuation episode requires completed_at")
        if self.state is STContinuationEpisodeState.SUCCEEDED:
            _identity(self.accepted_area_id, "continuation accepted-area")
        elif self.accepted_area_id is not None:
            raise ValueError("non-success ST continuation episode cannot carry accepted-area identity")


@dataclass(frozen=True, slots=True)
class STEconomicHistory:
    accepted_areas: tuple[STAcceptedAreaEvent, ...] = ()
    earned_defenses: tuple[STEarnedDefenseEvent, ...] = ()
    progress_events: tuple[STProgressEvent, ...] = ()
    mission_completion: STMissionCompletionMilestone | None = None
    continuation_episodes: tuple[STContinuationEpisode, ...] = ()

    def __post_init__(self) -> None:
        _unique((item.event_id for item in self.accepted_areas), "accepted-area")
        _unique((item.event_id for item in self.earned_defenses), "earned-defense")
        _unique((item.event_id for item in self.progress_events), "progress")
        _unique((item.episode_id for item in self.continuation_episodes), "continuation")
        _monotonic(((item.observed_at, item.event_id) for item in self.accepted_areas), "accepted-area")
        _monotonic(((item.observed_at, item.event_id) for item in self.earned_defenses), "earned-defense")
        _monotonic(((item.observed_at, item.event_id) for item in self.progress_events), "progress")

    @property
    def active_earned_defense(self) -> STEarnedDefenseEvent | None:
        return self.earned_defenses[-1] if self.earned_defenses else None


def _identity(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"ST {label} identity must be non-empty")


def _timestamp(value: Any, label: str) -> None:
    if value is None:
        raise ValueError(f"ST {label} must be known")
    pd.Timestamp(value)


def _finite(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"ST {label} must be numeric") from exc
    if not isfinite(result):
        raise ValueError(f"ST {label} must be finite")
    return result


def _band(low: Any, high: Any, label: str) -> None:
    low_value = _finite(low, f"{label} low")
    high_value = _finite(high, f"{label} high")
    if low_value > high_value:
        raise ValueError(f"ST {label} low cannot exceed high")


def _unique(values: Iterable[str], label: str) -> None:
    rows = tuple(values)
    if len(rows) != len(set(rows)):
        raise ValueError(f"ST {label} history identities must be unique")


def _time_key(value: Any) -> str:
    return pd.Timestamp(value).isoformat()


def _monotonic(values: Iterable[tuple[Any, str]], label: str) -> None:
    keys = tuple((_time_key(timestamp), identity) for timestamp, identity in values)
    if keys != tuple(sorted(keys)):
        raise ValueError(f"ST {label} history must be monotonic")


def empty_st_economic_history() -> STEconomicHistory:
    return STEconomicHistory()


def initial_st_economic_history(metadata: "PositionEntryMetadata | None") -> STEconomicHistory | None:
    if (
        metadata is not None
        and metadata.entry_horizon is DecisionHorizon.SHORT_TERM
        and metadata.st_trade_memory is not None
    ):
        return STEconomicHistory()
    return None


def _available_valid(ref: Any, as_of: Any) -> bool:
    return (
        ref is not None
        and getattr(ref, "data_quality", None) is ContextDataQuality.VALID
        and ref.is_available_at(as_of)
    )


def _fact_for_timeframe(projection: Any | None, timeframe: str) -> Any | None:
    if projection is None:
        return None
    wanted = timeframe.strip().lower()
    return next(
        (
            item
            for item in getattr(projection, "timeframe_facts", ())
            if str(getattr(item, "timeframe", "")).strip().lower() == wanted
        ),
        None,
    )


def _fmt(value: float) -> str:
    return f"{float(value):.12g}"


def _accepted_area(snapshot: "DecisionInputSnapshot", state: "TradeLifecycleState") -> STAcceptedAreaEvent | None:
    metadata = state.entry_metadata
    if metadata is None or pd.Timestamp(snapshot.as_of) <= pd.Timestamp(metadata.entry_as_of):
        return None

    row = _fact_for_timeframe(getattr(snapshot, "support_resistance", None), "1h")
    ref = None if row is None else getattr(row, "ref", None)
    if row is None or not _available_valid(ref, snapshot.as_of):
        return None
    if str(getattr(row, "state", "") or "").strip().upper() != "RANGE_BREAK_CONFIRMED":
        return None
    if int(getattr(row, "break_direction", 0) or 0) != 1:
        return None

    confirmed_index = getattr(row, "break_confirmed_index", None)
    boundary = getattr(row, "break_boundary", None)
    low = getattr(row, "role_reversal_support_low", None)
    high = getattr(row, "role_reversal_support_high", None)
    if None in (confirmed_index, boundary, low, high):
        return None

    boundary = _finite(boundary, "accepted-area boundary")
    low = _finite(low, "accepted-area low")
    high = _finite(high, "accepted-area high")
    if low > high or float(snapshot.current_price) <= boundary:
        return None

    # Fail closed: the entire accepted support band must be at/above entry.
    # Raw favorable PnL or a wick/new high never creates economic progress.
    if low < float(metadata.entry_price):
        return None

    event_id = (
        f"SR_ACCEPTED:1h:{getattr(row, 'range_identity', 'UNKNOWN')}:"
        f"{confirmed_index}:{_fmt(boundary)}:{_fmt(low)}:{_fmt(high)}"
    )
    return STAcceptedAreaEvent(
        event_id=event_id,
        observed_at=snapshot.as_of,
        timeframe="1h",
        low=low,
        high=high,
        break_boundary=boundary,
    )


def _append_accepted_area(
    history: STEconomicHistory,
    candidate: STAcceptedAreaEvent | None,
    state: "TradeLifecycleState",
) -> STEconomicHistory:
    if candidate is None or any(item.event_id == candidate.event_id for item in history.accepted_areas):
        return history

    metadata = state.entry_metadata
    assert metadata is not None
    accepted_areas = _append_sorted(history.accepted_areas, candidate, "observed_at", "event_id")

    baseline_low: float | None = None
    if history.active_earned_defense is not None:
        baseline_low = float(history.active_earned_defense.low)
    elif metadata.st_trade_memory is not None and metadata.st_trade_memory.initial_defended_anchor is not None:
        baseline_low = float(metadata.st_trade_memory.initial_defended_anchor.low)

    earned_defenses = history.earned_defenses
    progress_events = history.progress_events
    if baseline_low is None or candidate.low > baseline_low:
        defense = STEarnedDefenseEvent(
            event_id=f"EARNED_DEFENSE:{candidate.event_id}",
            observed_at=candidate.observed_at,
            accepted_area_id=candidate.event_id,
            low=candidate.low,
            high=candidate.high,
        )
        earned_defenses = _append_sorted(earned_defenses, defense, "observed_at", "event_id")
        distance = candidate.low - float(metadata.entry_price)
        if distance > 0.0:
            progress = STProgressEvent(
                event_id=f"PROGRESS:{candidate.event_id}",
                observed_at=candidate.observed_at,
                accepted_area_id=candidate.event_id,
                accepted_floor=candidate.low,
                distance_from_entry=distance,
            )
            progress_events = _append_sorted(progress_events, progress, "observed_at", "event_id")

    mission_completion = history.mission_completion
    memory = metadata.st_trade_memory
    target = None if memory is None else memory.initial_target_context
    if mission_completion is None and target is not None and candidate.low >= float(target.low):
        mission_completion = STMissionCompletionMilestone(
            event_id=f"MISSION_COMPLETED:{target.identity}:{candidate.event_id}",
            observed_at=candidate.observed_at,
            target_identity=target.identity,
            accepted_area_id=candidate.event_id,
        )

    return replace(
        history,
        accepted_areas=accepted_areas,
        earned_defenses=earned_defenses,
        progress_events=progress_events,
        mission_completion=mission_completion,
    )


def _append_sorted(rows: tuple[Any, ...], item: Any, time_field: str, id_field: str) -> tuple[Any, ...]:
    return tuple(
        sorted(
            (*rows, item),
            key=lambda row: (_time_key(getattr(row, time_field)), getattr(row, id_field)),
        )
    )


def _progress_after(
    history: STEconomicHistory,
    *,
    formed_at: Any,
    upper_boundary: float,
) -> STProgressEvent | None:
    areas = {item.event_id: item for item in history.accepted_areas}
    for progress in history.progress_events:
        if pd.Timestamp(progress.observed_at) < pd.Timestamp(formed_at):
            continue
        area = areas.get(progress.accepted_area_id)
        if area is not None and float(area.low) > float(upper_boundary):
            return progress
    return None


def _fold_continuation(
    snapshot: "DecisionInputSnapshot",
    state: "TradeLifecycleState",
    history: STEconomicHistory,
) -> STEconomicHistory:
    metadata = state.entry_metadata
    lifecycle = getattr(snapshot, "fvg_engulfing_lifecycle", None)
    if metadata is None or lifecycle is None:
        return history

    episodes = {item.episode_id: item for item in history.continuation_episodes}
    changed = False
    for item in getattr(lifecycle, "fvg", ()):
        ref = getattr(item, "ref", None)
        if str(getattr(ref, "timeframe", "")).strip().lower() != "1h":
            continue
        if int(getattr(item, "direction", 0) or 0) != 1 or not _available_valid(ref, snapshot.as_of):
            continue
        formed_at = getattr(ref, "origin_time", None)
        if formed_at is None or pd.Timestamp(formed_at) <= pd.Timestamp(metadata.entry_as_of):
            continue
        if getattr(item, "first_test_index", None) is None:
            continue

        episode_id = f"FVG:1h:{getattr(item, 'identity', 'UNKNOWN')}"
        existing = episodes.get(episode_id)
        if existing is not None and existing.state is not STContinuationEpisodeState.LIVE:
            continue

        progress = _progress_after(
            history,
            formed_at=formed_at,
            upper_boundary=float(getattr(item, "upper_boundary")),
        )
        if bool(getattr(item, "reaction_confirmed", False)) and progress is not None:
            next_state = STContinuationEpisodeState.SUCCEEDED
            completed_at = snapshot.as_of
            accepted_area_id = progress.accepted_area_id
        elif bool(getattr(item, "failed_reaction", False)):
            next_state = STContinuationEpisodeState.FAILED
            completed_at = snapshot.as_of
            accepted_area_id = None
        else:
            next_state = STContinuationEpisodeState.LIVE
            completed_at = None
            accepted_area_id = None

        if existing is None:
            episodes[episode_id] = STContinuationEpisode(
                episode_id=episode_id,
                source_identity=str(getattr(item, "identity", "UNKNOWN")),
                timeframe="1h",
                formed_at=formed_at,
                first_observed_at=snapshot.as_of,
                lower_boundary=float(getattr(item, "lower_boundary")),
                upper_boundary=float(getattr(item, "upper_boundary")),
                state=next_state,
                completed_at=completed_at,
                accepted_area_id=accepted_area_id,
            )
            changed = True
        elif next_state is not STContinuationEpisodeState.LIVE:
            episodes[episode_id] = replace(
                existing,
                state=next_state,
                completed_at=completed_at,
                accepted_area_id=accepted_area_id,
            )
            changed = True

    if not changed:
        return history
    return replace(
        history,
        continuation_episodes=tuple(
            sorted(
                episodes.values(),
                key=lambda item: (_time_key(item.formed_at), item.episode_id),
            )
        ),
    )


def observe_st_economic_history(
    snapshot: "DecisionInputSnapshot",
    state: "TradeLifecycleState",
) -> STEconomicHistory | None:
    """Fold one OPEN ST snapshot into factual history without changing actions."""

    metadata = state.entry_metadata
    if (
        metadata is None
        or metadata.entry_horizon is not DecisionHorizon.SHORT_TERM
        or metadata.st_trade_memory is None
    ):
        return state.st_economic_history

    history = state.st_economic_history or STEconomicHistory()
    if pd.Timestamp(snapshot.as_of) <= pd.Timestamp(metadata.entry_as_of):
        return history

    history = _append_accepted_area(history, _accepted_area(snapshot, state), state)
    return _fold_continuation(snapshot, state, history)


def _ts_payload(value: Any | None) -> str | None:
    return None if value is None else pd.Timestamp(value).isoformat()


def _ts_from_payload(value: Any, field: str, *, required: bool = True) -> pd.Timestamp | None:
    if value is None:
        if required:
            raise ValueError(f"{field} must be present")
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO timestamp string")
    return pd.Timestamp(value)


def serialize_st_economic_history(history: STEconomicHistory | None) -> dict[str, Any] | None:
    if history is None:
        return None
    return {
        "accepted_areas": [
            {
                "event_id": row.event_id,
                "observed_at": _ts_payload(row.observed_at),
                "timeframe": row.timeframe,
                "low": row.low,
                "high": row.high,
                "break_boundary": row.break_boundary,
            }
            for row in history.accepted_areas
        ],
        "earned_defenses": [
            {
                "event_id": row.event_id,
                "observed_at": _ts_payload(row.observed_at),
                "accepted_area_id": row.accepted_area_id,
                "low": row.low,
                "high": row.high,
            }
            for row in history.earned_defenses
        ],
        "progress_events": [
            {
                "event_id": row.event_id,
                "observed_at": _ts_payload(row.observed_at),
                "accepted_area_id": row.accepted_area_id,
                "accepted_floor": row.accepted_floor,
                "distance_from_entry": row.distance_from_entry,
            }
            for row in history.progress_events
        ],
        "mission_completion": None if history.mission_completion is None else {
            "event_id": history.mission_completion.event_id,
            "observed_at": _ts_payload(history.mission_completion.observed_at),
            "target_identity": history.mission_completion.target_identity,
            "accepted_area_id": history.mission_completion.accepted_area_id,
        },
        "continuation_episodes": [
            {
                "episode_id": row.episode_id,
                "source_identity": row.source_identity,
                "timeframe": row.timeframe,
                "formed_at": _ts_payload(row.formed_at),
                "first_observed_at": _ts_payload(row.first_observed_at),
                "lower_boundary": row.lower_boundary,
                "upper_boundary": row.upper_boundary,
                "state": row.state.value,
                "completed_at": _ts_payload(row.completed_at),
                "accepted_area_id": row.accepted_area_id,
            }
            for row in history.continuation_episodes
        ],
    }


def deserialize_st_economic_history(payload: Any) -> STEconomicHistory | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise ValueError("persisted ST economic history must be a mapping")

    def rows(key: str) -> list[Mapping[str, Any]]:
        value = payload.get(key)
        if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
            raise ValueError(f"persisted ST economic history {key} must be a mapping list")
        return value

    accepted = tuple(
        STAcceptedAreaEvent(
            event_id=str(row["event_id"]),
            observed_at=_ts_from_payload(row.get("observed_at"), "accepted_area.observed_at"),
            timeframe=str(row["timeframe"]),
            low=float(row["low"]),
            high=float(row["high"]),
            break_boundary=float(row["break_boundary"]),
        )
        for row in rows("accepted_areas")
    )
    defenses = tuple(
        STEarnedDefenseEvent(
            event_id=str(row["event_id"]),
            observed_at=_ts_from_payload(row.get("observed_at"), "earned_defense.observed_at"),
            accepted_area_id=str(row["accepted_area_id"]),
            low=float(row["low"]),
            high=float(row["high"]),
        )
        for row in rows("earned_defenses")
    )
    progress = tuple(
        STProgressEvent(
            event_id=str(row["event_id"]),
            observed_at=_ts_from_payload(row.get("observed_at"), "progress.observed_at"),
            accepted_area_id=str(row["accepted_area_id"]),
            accepted_floor=float(row["accepted_floor"]),
            distance_from_entry=float(row["distance_from_entry"]),
        )
        for row in rows("progress_events")
    )

    raw_mission = payload.get("mission_completion")
    if raw_mission is None:
        mission = None
    elif isinstance(raw_mission, Mapping):
        mission = STMissionCompletionMilestone(
            event_id=str(raw_mission["event_id"]),
            observed_at=_ts_from_payload(raw_mission.get("observed_at"), "mission_completion.observed_at"),
            target_identity=str(raw_mission["target_identity"]),
            accepted_area_id=str(raw_mission["accepted_area_id"]),
        )
    else:
        raise ValueError("persisted ST mission completion must be a mapping")

    episodes = []
    for row in rows("continuation_episodes"):
        accepted_area_id = row.get("accepted_area_id")
        if accepted_area_id is not None and not isinstance(accepted_area_id, str):
            raise ValueError("persisted continuation accepted-area identity must be a string")
        episodes.append(
            STContinuationEpisode(
                episode_id=str(row["episode_id"]),
                source_identity=str(row["source_identity"]),
                timeframe=str(row["timeframe"]),
                formed_at=_ts_from_payload(row.get("formed_at"), "continuation.formed_at"),
                first_observed_at=_ts_from_payload(row.get("first_observed_at"), "continuation.first_observed_at"),
                lower_boundary=float(row["lower_boundary"]),
                upper_boundary=float(row["upper_boundary"]),
                state=STContinuationEpisodeState(str(row["state"])),
                completed_at=_ts_from_payload(
                    row.get("completed_at"),
                    "continuation.completed_at",
                    required=False,
                ),
                accepted_area_id=accepted_area_id,
            )
        )

    return STEconomicHistory(
        accepted_areas=accepted,
        earned_defenses=defenses,
        progress_events=progress,
        mission_completion=mission,
        continuation_episodes=tuple(episodes),
    )


__all__ = [
    "STAcceptedAreaEvent",
    "STContinuationEpisode",
    "STContinuationEpisodeState",
    "STEarnedDefenseEvent",
    "STEconomicHistory",
    "STMissionCompletionMilestone",
    "STProgressEvent",
    "deserialize_st_economic_history",
    "empty_st_economic_history",
    "initial_st_economic_history",
    "observe_st_economic_history",
    "serialize_st_economic_history",
]
