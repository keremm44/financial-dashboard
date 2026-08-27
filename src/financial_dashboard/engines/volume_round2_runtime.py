from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Iterator, Sequence

import pandas as pd

from . import volume_round2 as vr
from .volume_evidence import StructureVolumeLink, VolumeEvidenceSnapshot
from .volume_round2 import (
    LowerTimeframeInflowState,
    LowerTimeframeVolumeInflow,
    VolumeRound2Assessment,
)


@dataclass(frozen=True, slots=True)
class _CachedIterrowsFrame:
    """Read-only frame facade that materializes pandas row Series exactly once."""

    rows: tuple[tuple[Any, pd.Series], ...]

    @classmethod
    def from_frame(cls, frame: pd.DataFrame) -> "_CachedIterrowsFrame":
        return cls(tuple(frame.iterrows()))

    def iterrows(self) -> Iterator[tuple[Any, pd.Series]]:
        return iter(self.rows)


@dataclass(frozen=True, slots=True)
class _AvailabilityIndex:
    values_ns: tuple[int, ...] | None

    @classmethod
    def build(cls, replay: Any, clock: Any) -> "_AvailabilityIndex":
        values = tuple(
            pd.Timestamp(clock.available_at(snapshot.timestamp, snapshot.timeframe)).value
            for snapshot in replay.history
        )
        if any(left > right for left, right in zip(values, values[1:])):
            return cls(None)
        return cls(values)


def _runtime_replay(replay: Any) -> Any:
    cached_frame = _CachedIterrowsFrame.from_frame(replay.input_batch.frame)
    input_batch = SimpleNamespace(frame=cached_frame)
    return SimpleNamespace(
        symbol=getattr(replay, "symbol", None),
        timeframe=replay.timeframe,
        input_batch=input_batch,
        history=replay.history,
        latest=replay.latest,
        event_links=replay.event_links,
        participation_without_structure=replay.participation_without_structure,
    )


def _inflow_indexed(
    link: StructureVolumeLink,
    source_replay: Any,
    index: _AvailabilityIndex,
    *,
    clock: Any,
    final_as_of: pd.Timestamp,
) -> LowerTimeframeVolumeInflow:
    values = index.values_ns
    if values is None:
        return vr._inflow_for_source(
            link,
            source_replay,
            clock=clock,
            final_as_of=final_as_of,
        )

    target_start = pd.Timestamp(link.confirmed_at)
    target_available = clock.available_at(link.confirmed_at, link.timeframe)
    target_duration = dict(clock.durations)[link.timeframe]
    follow_end = min(final_as_of, target_available + (2 * target_duration))

    left = bisect_right(values, target_start.value)
    right = bisect_right(values, pd.Timestamp(follow_end).value)
    selected: Sequence[VolumeEvidenceSnapshot] = source_replay.history[left:right]
    usable = tuple(snapshot for snapshot in selected if snapshot.has_usable_measurement)
    aligned = sum(vr._mature_direction(snapshot) == link.event_direction for snapshot in usable)
    opposed = sum(vr._mature_direction(snapshot) == -link.event_direction for snapshot in usable)
    shocks = sum(vr._is_shock(snapshot) for snapshot in usable)

    if aligned and opposed:
        state = LowerTimeframeInflowState.MIXED
        signal = 0
    elif aligned:
        state = LowerTimeframeInflowState.ALIGNED
        signal = 1
    elif opposed:
        state = LowerTimeframeInflowState.OPPOSED
        signal = -1
    elif shocks:
        state = LowerTimeframeInflowState.SHOCK_UNCONFIRMED
        signal = 0
    elif usable:
        state = LowerTimeframeInflowState.WEAK
        signal = 0
    else:
        state = LowerTimeframeInflowState.UNKNOWN
        signal = 0

    latest_available = (
        None
        if not selected
        else clock.available_at(selected[-1].timestamp, selected[-1].timeframe)
    )
    return LowerTimeframeVolumeInflow(
        event_uid=link.event_uid,
        target_timeframe=link.timeframe,
        source_timeframe=source_replay.timeframe,
        state=state,
        signal=signal,
        observed_count=len(selected),
        usable_count=len(usable),
        aligned_confirmed_count=aligned,
        opposed_confirmed_count=opposed,
        shock_count=shocks,
        latest_available_at=latest_available,
    )


def _build_event_mtf_assessments_indexed(
    timeframe_replays: Sequence[Any],
    *,
    clock: Any,
    final_as_of: pd.Timestamp,
) -> tuple[Any, ...]:
    by_timeframe = {replay.timeframe: replay for replay in timeframe_replays}
    available = set(by_timeframe)
    availability = {
        timeframe: _AvailabilityIndex.build(replay, clock)
        for timeframe, replay in by_timeframe.items()
    }
    assessments: list[Any] = []

    for replay in timeframe_replays:
        for link in replay.event_links:
            lower = vr._lower_timeframes(link.timeframe, available)
            inflows = tuple(
                _inflow_indexed(
                    link,
                    by_timeframe[source_timeframe],
                    availability[source_timeframe],
                    clock=clock,
                    final_as_of=final_as_of,
                )
                for source_timeframe in lower
            )
            denominator = sum(vr._TIMEFRAME_WEIGHT[timeframe] for timeframe in lower)
            score = (
                0.0
                if denominator <= 0
                else sum(
                    vr._TIMEFRAME_WEIGHT[inflow.source_timeframe] * inflow.signal
                    for inflow in inflows
                )
                / denominator
            )
            states = {inflow.state for inflow in inflows}
            if not lower:
                lower_state = LowerTimeframeInflowState.NO_LOWER_TIMEFRAME
            elif (
                LowerTimeframeInflowState.ALIGNED in states
                and LowerTimeframeInflowState.OPPOSED in states
            ) or LowerTimeframeInflowState.MIXED in states:
                lower_state = LowerTimeframeInflowState.MIXED
            elif LowerTimeframeInflowState.ALIGNED in states:
                lower_state = LowerTimeframeInflowState.ALIGNED
            elif LowerTimeframeInflowState.OPPOSED in states:
                lower_state = LowerTimeframeInflowState.OPPOSED
            elif LowerTimeframeInflowState.SHOCK_UNCONFIRMED in states:
                lower_state = LowerTimeframeInflowState.SHOCK_UNCONFIRMED
            elif LowerTimeframeInflowState.WEAK in states:
                lower_state = LowerTimeframeInflowState.WEAK
            else:
                lower_state = LowerTimeframeInflowState.UNKNOWN

            importance = vr._importance_for_relation(link.relation)
            reasons = [
                "Same-timeframe Structure and Volume remain the only authority for their timeframe.",
                "Lower-timeframe Volume is categorical context; raw volumes are not summed.",
            ]
            if importance is not vr.LowerTimeframeImportance.ENRICHMENT_ONLY:
                reasons.append(
                    "Lower-timeframe inspection is elevated because same-timeframe Volume is weak or unavailable."
                )
            assessments.append(
                vr.StructureVolumeMTFAssessment(
                    event_uid=link.event_uid,
                    symbol=link.symbol,
                    timeframe=link.timeframe,
                    scope=link.scope,
                    event_type=link.event_type,
                    event_direction=link.event_direction,
                    confirmed_at=link.confirmed_at,
                    same_timeframe_relation=link.relation,
                    lower_timeframe_importance=importance,
                    lower_timeframe_state=lower_state,
                    lower_timeframe_score=max(-1.0, min(1.0, score)),
                    lower_timeframe_inflows=inflows,
                    reasons=tuple(reasons),
                )
            )
    return tuple(assessments)


def build_volume_round2_assessment_runtime(
    *,
    symbol: str,
    timeframe_replays: Sequence[Any],
    structure_snapshots: Sequence[Any],
    clock: Any,
) -> VolumeRound2Assessment:
    """Canonical Round 2 with indexed windows and shared row materialization."""

    runtime_replays = tuple(_runtime_replay(replay) for replay in timeframe_replays)
    latest_availability = tuple(
        vr._availability(replay.latest, clock)
        for replay in runtime_replays
        if replay.latest is not None
    )
    if not latest_availability:
        raise ValueError("Volume Round 2 assessment requires at least one snapshot")
    final_as_of = max(latest_availability)

    pressure = vr.build_mtf_pressure_context(runtime_replays, clock=clock)
    event_assessments = _build_event_mtf_assessments_indexed(
        runtime_replays,
        clock=clock,
        final_as_of=final_as_of,
    )
    all_events = vr._all_structure_events(structure_snapshots)
    replay_by_timeframe = {replay.timeframe: replay for replay in runtime_replays}
    linked_uids = {
        link.event_uid
        for replay in runtime_replays
        for link in replay.event_links
    }
    risks = tuple(
        vr.build_structure_volume_risk(
            event,
            replay_by_timeframe[event.timeframe or ""],
            same_scope_events=all_events,
            clock=clock,
        )
        for event in all_events
        if event.event_uid in linked_uids and event.timeframe in replay_by_timeframe
    )
    shocks = tuple(
        shock
        for replay in runtime_replays
        for shock in vr.build_shock_lifecycles(replay, clock=clock)
    )
    propagations = vr.build_structural_propagations(
        runtime_replays,
        structure_snapshots,
        clock=clock,
        final_as_of=final_as_of,
    )
    return VolumeRound2Assessment(
        symbol=symbol.strip().upper(),
        as_of=final_as_of,
        pressure=pressure,
        event_assessments=event_assessments,
        risks=risks,
        shocks=shocks,
        structural_propagations=propagations,
        deduplication=vr.build_correlated_volume_deduplication(),
    )


__all__ = ["build_volume_round2_assessment_runtime"]
