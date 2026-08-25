from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Any, Iterable, Sequence

import pandas as pd

from .market_structure_events import (
    MarketStructureEventRecord,
    StructureEventConfirmation,
)
from .market_structure_state import BosMaturity, EVENT_BOS, EVENT_CHOCH
from .volume_participation_engine import (
    VolumeParticipationConfig,
    VolumeParticipationMetrics,
)
from .volume_participation_final import (
    FinalParticipationState,
    UnifiedParticipationExport,
    VolumeParticipationEngine as FinalVolumeParticipationEngine,
)
from .volume_participation_lifecycle import ParticipationLifecycleConfig


class VolumeEvidenceStatus(StrEnum):
    """Participation meaning of one closed-bar Volume observation."""

    READY = "READY"
    WARMUP = "WARMUP"
    LOW_PARTICIPATION = "LOW_PARTICIPATION"
    VOLUME_UNAVAILABLE = "VOLUME_UNAVAILABLE"


class VolumeEvidenceDataQuality(StrEnum):
    """Data boundary state, kept separate from participation meaning."""

    READY = "READY"
    DATA_LIMITED = "DATA_LIMITED"
    INCOMPLETE_TAIL = "INCOMPLETE_TAIL"


class StructureVolumeTiming(StrEnum):
    PRE_EVENT = "PRE_EVENT"
    AT_EVENT = "AT_EVENT"
    FOLLOW_THROUGH = "FOLLOW_THROUGH"


class StructureVolumeRelation(StrEnum):
    STRUCTURE_SUPPORTED = "STRUCTURE_SUPPORTED"
    STRUCTURE_PARTICIPATION_WEAK = "STRUCTURE_PARTICIPATION_WEAK"
    STRUCTURE_VOLUME_OPPOSED = "STRUCTURE_VOLUME_OPPOSED"
    STRUCTURE_VOLUME_CONFLICT = "STRUCTURE_VOLUME_CONFLICT"
    STRUCTURE_SHOCK_UNCONFIRMED = "STRUCTURE_SHOCK_UNCONFIRMED"
    STRUCTURE_ABSORPTION_RISK = "STRUCTURE_ABSORPTION_RISK"
    PARTICIPATION_WITHOUT_STRUCTURE = "PARTICIPATION_WITHOUT_STRUCTURE"
    STRUCTURE_VOLUME_UNKNOWN = "STRUCTURE_VOLUME_UNKNOWN"


@dataclass(frozen=True, slots=True)
class VolumeEvidenceSnapshot:
    """Neutral, immutable closed-bar evidence copied from the existing Volume engine.

    ``audit_export`` intentionally retains the legacy source fields for mathematical
    parity inspection.  Neither this snapshot nor the export is a trade action.
    """

    symbol: str
    timeframe: str
    bar_index: int
    timestamp: Any
    segment_id: int
    status: VolumeEvidenceStatus
    data_quality: VolumeEvidenceDataQuality
    state: str
    evidence_direction: int
    metrics: VolumeParticipationMetrics
    audit_export: UnifiedParticipationExport
    is_confirmed: bool = True

    @property
    def has_usable_measurement(self) -> bool:
        return self.status in {
            VolumeEvidenceStatus.READY,
            VolumeEvidenceStatus.LOW_PARTICIPATION,
        }


@dataclass(frozen=True, slots=True)
class VolumeWindowEvidence:
    timing: StructureVolumeTiming
    start_bar: int
    end_bar: int
    expected_bar_count: int
    observed_bar_indices: tuple[int, ...]
    states: tuple[str, ...]
    usable_count: int
    warmup_count: int
    unavailable_count: int
    data_limited_count: int
    low_participation_count: int
    aligned_confirmed_count: int
    opposed_confirmed_count: int
    aligned_candidate_count: int
    opposed_candidate_count: int
    conflict_count: int
    shock_count: int
    opposing_absorption_count: int
    same_direction_reclaim_count: int

    @property
    def observed_bar_count(self) -> int:
        return len(self.observed_bar_indices)

    @property
    def is_complete(self) -> bool:
        return self.observed_bar_count == self.expected_bar_count


@dataclass(frozen=True, slots=True)
class StructureVolumeLink:
    event_uid: str
    symbol: str
    timeframe: str
    scope: str
    event_type: str
    bos_maturity: BosMaturity
    event_direction: int
    event_bar: int
    confirmed_at: Any
    broken_level: float | None
    assessed_at: Any
    relation: StructureVolumeRelation
    windows: tuple[VolumeWindowEvidence, ...]
    reasons: tuple[str, ...]

    def window(self, timing: StructureVolumeTiming) -> VolumeWindowEvidence:
        for window in self.windows:
            if window.timing is timing:
                return window
        raise KeyError(f"Volume window not available: {timing}")


@dataclass(frozen=True, slots=True)
class ParticipationWithoutStructure:
    symbol: str
    timeframe: str
    bar_index: int
    timestamp: Any
    state: str
    evidence_direction: int
    status: VolumeEvidenceStatus
    relation: StructureVolumeRelation = StructureVolumeRelation.PARTICIPATION_WITHOUT_STRUCTURE
    event_uid: str | None = None


_MATURE_STATES = {
    FinalParticipationState.UP_CONFIRMED.value,
    FinalParticipationState.DOWN_CONFIRMED.value,
    FinalParticipationState.UP_PROTECTED.value,
    FinalParticipationState.DOWN_PROTECTED.value,
    FinalParticipationState.UP_BREAK_SUPPORTED.value,
    FinalParticipationState.DOWN_BREAK_SUPPORTED.value,
    FinalParticipationState.LOWER_ABSORPTION_CONFIRMED.value,
    FinalParticipationState.UPPER_ABSORPTION_CONFIRMED.value,
}
_CANDIDATE_STATES = {
    FinalParticipationState.UP_CANDIDATE.value,
    FinalParticipationState.DOWN_CANDIDATE.value,
    FinalParticipationState.LOWER_ABSORPTION_CANDIDATE.value,
    FinalParticipationState.UPPER_ABSORPTION_CANDIDATE.value,
}
_ABSORPTION_CONFIRMED_STATES = {
    FinalParticipationState.LOWER_ABSORPTION_CONFIRMED.value,
    FinalParticipationState.UPPER_ABSORPTION_CONFIRMED.value,
}
_ACTIVE_PARTICIPATION_STATES = {
    FinalParticipationState.RISING_PARTICIPATION.value,
    FinalParticipationState.ABNORMAL_VOLUME.value,
    FinalParticipationState.ABNORMAL_CAPITAL.value,
    FinalParticipationState.ONE_BAR_SHOCK.value,
    FinalParticipationState.CONFLICT.value,
    FinalParticipationState.UP_WEAKENING.value,
    FinalParticipationState.DOWN_WEAKENING.value,
    FinalParticipationState.UP_BREAK_UNSUPPORTED.value,
    FinalParticipationState.DOWN_BREAK_UNSUPPORTED.value,
    FinalParticipationState.UP_BREAK_RECLAIMED.value,
    FinalParticipationState.DOWN_BREAK_RECLAIMED.value,
    *_MATURE_STATES,
    *_CANDIDATE_STATES,
}


class VolumeEvidenceEngine:
    """Append-only evidence wrapper; existing Volume math/lifecycle is unchanged."""

    def __init__(
        self,
        *,
        symbol: str,
        timeframe: str,
        config: VolumeParticipationConfig | None = None,
        lifecycle_config: ParticipationLifecycleConfig | None = None,
    ) -> None:
        normalized_symbol = symbol.strip().upper()
        normalized_timeframe = timeframe.strip().lower()
        if not normalized_symbol:
            raise ValueError("symbol must not be empty")
        if not normalized_timeframe:
            raise ValueError("timeframe must not be empty")
        self.symbol = normalized_symbol
        self.timeframe = normalized_timeframe
        self.config = config or VolumeParticipationConfig()
        self.lifecycle_config = lifecycle_config or ParticipationLifecycleConfig()
        self._history: list[VolumeEvidenceSnapshot] = []
        self._snapshot: VolumeEvidenceSnapshot | None = None
        self._segment_id = 0
        self._segment_bar_count = 0
        self._source = self._new_source()

    def _new_source(self) -> FinalVolumeParticipationEngine:
        return FinalVolumeParticipationEngine(self.config, self.lifecycle_config)

    def _reset(self) -> None:
        self._history = []
        self._snapshot = None
        self._segment_id = 0
        self._segment_bar_count = 0
        self._source = self._new_source()

    def _restart_after_gap(self) -> None:
        self._segment_id += 1
        self._segment_bar_count = 0
        self._source = self._new_source()

    def _status(
        self,
        metrics: VolumeParticipationMetrics,
        audit_export: UnifiedParticipationExport,
    ) -> VolumeEvidenceStatus:
        if metrics.data_ready:
            if audit_export.state == FinalParticipationState.LOW_PARTICIPATION.value:
                return VolumeEvidenceStatus.LOW_PARTICIPATION
            return VolumeEvidenceStatus.READY
        if self._segment_bar_count < self.config.volume_average_length:
            return VolumeEvidenceStatus.WARMUP
        if not metrics.volume_usable:
            return VolumeEvidenceStatus.VOLUME_UNAVAILABLE
        return VolumeEvidenceStatus.WARMUP

    @staticmethod
    def _public_state(status: VolumeEvidenceStatus, source_state: str | None) -> str:
        if status is VolumeEvidenceStatus.WARMUP:
            return FinalParticipationState.PENDING.value
        if status is VolumeEvidenceStatus.VOLUME_UNAVAILABLE:
            return FinalParticipationState.VOLUME_UNAVAILABLE.value
        if status is VolumeEvidenceStatus.LOW_PARTICIPATION:
            return FinalParticipationState.LOW_PARTICIPATION.value
        return source_state or FinalParticipationState.NEUTRAL.value

    @staticmethod
    def _evidence_direction(
        status: VolumeEvidenceStatus,
        audit_export: UnifiedParticipationExport,
    ) -> int:
        if status not in {VolumeEvidenceStatus.READY, VolumeEvidenceStatus.LOW_PARTICIPATION}:
            return 0
        if audit_export.support_direction > 0:
            return 1
        if audit_export.support_direction < 0:
            return -1
        return 0

    def _incomplete_snapshot(self, row: dict[str, Any]) -> VolumeEvidenceSnapshot:
        prior = self._snapshot
        return VolumeEvidenceSnapshot(
            symbol=self.symbol,
            timeframe=self.timeframe,
            bar_index=len(self._history),
            timestamp=row.get("timestamp"),
            segment_id=self._segment_id,
            status=prior.status if prior is not None else VolumeEvidenceStatus.WARMUP,
            data_quality=VolumeEvidenceDataQuality.INCOMPLETE_TAIL,
            state=(
                prior.state
                if prior is not None
                else FinalParticipationState.PENDING.value
            ),
            evidence_direction=prior.evidence_direction if prior is not None else 0,
            metrics=prior.metrics if prior is not None else VolumeParticipationMetrics(),
            audit_export=(
                prior.audit_export if prior is not None else UnifiedParticipationExport()
            ),
            is_confirmed=False,
        )

    def _unavailable_snapshot(self, row: dict[str, Any]) -> VolumeEvidenceSnapshot:
        return VolumeEvidenceSnapshot(
            symbol=self.symbol,
            timeframe=self.timeframe,
            bar_index=len(self._history),
            timestamp=row.get("timestamp"),
            segment_id=self._segment_id,
            status=VolumeEvidenceStatus.VOLUME_UNAVAILABLE,
            data_quality=VolumeEvidenceDataQuality.DATA_LIMITED,
            state=FinalParticipationState.VOLUME_UNAVAILABLE.value,
            evidence_direction=0,
            metrics=VolumeParticipationMetrics(),
            audit_export=UnifiedParticipationExport(
                state=FinalParticipationState.VOLUME_UNAVAILABLE.value
            ),
            is_confirmed=True,
        )

    def update(self, bar: Any) -> VolumeEvidenceSnapshot:
        row = dict(bar) if not isinstance(bar, dict) else bar.copy()

        def unsafe_flag(name: str) -> bool:
            if name not in row:
                return False
            value = row[name]
            return bool(pd.isna(value)) or not bool(value)

        if unsafe_flag("is_closed") or unsafe_flag("is_complete"):
            return self._incomplete_snapshot(row)

        required_prices = ("open", "high", "low", "close")
        if any(key not in row or pd.isna(row[key]) for key in required_prices):
            raise ValueError("Volume evidence requires complete OHLC bars")
        if "volume" not in row:
            raise ValueError("Volume evidence requires a volume field")

        try:
            volume = float(row["volume"])
        except (TypeError, ValueError):
            volume = float("nan")
        if not isfinite(volume):
            snapshot = self._unavailable_snapshot(row)
            self._history.append(snapshot)
            self._snapshot = snapshot
            self._restart_after_gap()
            return snapshot
        if volume < 0.0:
            raise ValueError("Volume evidence does not accept negative volume")

        source_result = self._source.update(row)
        if source_result is None:
            raise RuntimeError("Volume source engine did not produce a closed-bar result")
        self._segment_bar_count += 1
        metrics = self._source.metrics_history[-1]
        audit_export = self._source.final_export
        status = self._status(metrics, audit_export)
        snapshot = VolumeEvidenceSnapshot(
            symbol=self.symbol,
            timeframe=self.timeframe,
            bar_index=len(self._history),
            timestamp=row.get("timestamp"),
            segment_id=self._segment_id,
            status=status,
            data_quality=VolumeEvidenceDataQuality.READY,
            state=self._public_state(status, audit_export.state),
            evidence_direction=self._evidence_direction(status, audit_export),
            metrics=metrics,
            audit_export=audit_export,
            is_confirmed=True,
        )
        self._history.append(snapshot)
        self._snapshot = snapshot
        return snapshot

    def replay(self, data: pd.DataFrame) -> tuple[VolumeEvidenceSnapshot, ...]:
        self._reset()
        for row in data.to_dict("records"):
            self.update(row)
        return self.history

    @property
    def history(self) -> tuple[VolumeEvidenceSnapshot, ...]:
        return tuple(self._history)

    @property
    def snapshot(self) -> VolumeEvidenceSnapshot | None:
        return self._snapshot


def _eligible_structure_event(event: MarketStructureEventRecord) -> bool:
    return (
        event.confirmation_status is StructureEventConfirmation.CONFIRMED
        and event.event_type in {EVENT_BOS, EVENT_CHOCH}
    )


def _validate_namespace(
    event: MarketStructureEventRecord,
    *,
    symbol: str,
    timeframe: str,
) -> None:
    if event.symbol is not None and event.symbol.strip().upper() != symbol:
        raise ValueError(
            f"Structure/Volume symbol mismatch: {event.symbol!r} != {symbol!r}"
        )
    if event.timeframe is not None and event.timeframe.strip().lower() != timeframe:
        raise ValueError(
            f"Structure/Volume timeframe mismatch: {event.timeframe!r} != {timeframe!r}"
        )


def _timestamps_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return True
    try:
        return bool(pd.Timestamp(left) == pd.Timestamp(right))
    except (TypeError, ValueError):
        return left == right


def _summarize_window(
    *,
    timing: StructureVolumeTiming,
    start_bar: int,
    end_bar: int,
    expected_bar_count: int,
    history_by_bar: dict[int, VolumeEvidenceSnapshot],
    event_direction: int,
) -> VolumeWindowEvidence:
    observed = tuple(
        history_by_bar[index]
        for index in range(max(0, start_bar), end_bar + 1)
        if index in history_by_bar
    )
    usable = tuple(snapshot for snapshot in observed if snapshot.has_usable_measurement)

    aligned_confirmed = opposed_confirmed = 0
    aligned_candidate = opposed_candidate = 0
    opposing_absorption = same_direction_reclaim = 0
    non_shock_usable = tuple(
        snapshot
        for snapshot in usable
        if not snapshot.audit_export.one_bar_shock
    )
    for snapshot in non_shock_usable:
        direction = snapshot.evidence_direction
        if snapshot.state in _MATURE_STATES:
            if direction == event_direction:
                aligned_confirmed += 1
            elif direction == -event_direction:
                opposed_confirmed += 1
        if snapshot.state in _CANDIDATE_STATES:
            if direction == event_direction:
                aligned_candidate += 1
            elif direction == -event_direction:
                opposed_candidate += 1
        if (
            snapshot.state in _ABSORPTION_CONFIRMED_STATES
            and direction == -event_direction
        ):
            opposing_absorption += 1
        if (
            (event_direction > 0 and snapshot.state == FinalParticipationState.UP_BREAK_RECLAIMED.value)
            or (event_direction < 0 and snapshot.state == FinalParticipationState.DOWN_BREAK_RECLAIMED.value)
        ):
            same_direction_reclaim += 1

    return VolumeWindowEvidence(
        timing=timing,
        start_bar=start_bar,
        end_bar=end_bar,
        expected_bar_count=expected_bar_count,
        observed_bar_indices=tuple(snapshot.bar_index for snapshot in observed),
        states=tuple(snapshot.state for snapshot in observed),
        usable_count=len(usable),
        warmup_count=sum(
            snapshot.status is VolumeEvidenceStatus.WARMUP for snapshot in observed
        ),
        unavailable_count=sum(
            snapshot.status is VolumeEvidenceStatus.VOLUME_UNAVAILABLE
            for snapshot in observed
        ),
        data_limited_count=sum(
            snapshot.data_quality is VolumeEvidenceDataQuality.DATA_LIMITED
            for snapshot in observed
        ),
        low_participation_count=sum(
            snapshot.status is VolumeEvidenceStatus.LOW_PARTICIPATION
            for snapshot in observed
        ),
        aligned_confirmed_count=aligned_confirmed,
        opposed_confirmed_count=opposed_confirmed,
        aligned_candidate_count=aligned_candidate,
        opposed_candidate_count=opposed_candidate,
        conflict_count=sum(
            snapshot.state == FinalParticipationState.CONFLICT.value
            for snapshot in non_shock_usable
        ),
        shock_count=sum(
            snapshot.audit_export.one_bar_shock
            for snapshot in usable
        ),
        opposing_absorption_count=opposing_absorption,
        same_direction_reclaim_count=same_direction_reclaim,
    )


def _sum(windows: Sequence[VolumeWindowEvidence], field: str) -> int:
    return sum(int(getattr(window, field)) for window in windows)


def _resolve_relation(
    windows: tuple[VolumeWindowEvidence, ...],
) -> StructureVolumeRelation:
    pre = (windows[0],)
    active = windows[1:]
    active_aligned = _sum(active, "aligned_confirmed_count")
    active_opposed = _sum(active, "opposed_confirmed_count")
    active_conflict = _sum(active, "conflict_count")
    active_risk = _sum(active, "opposing_absorption_count") + _sum(
        active, "same_direction_reclaim_count"
    )
    active_shock = _sum(active, "shock_count")

    if active_conflict or (active_aligned and active_opposed):
        return StructureVolumeRelation.STRUCTURE_VOLUME_CONFLICT
    if active_risk:
        return StructureVolumeRelation.STRUCTURE_ABSORPTION_RISK
    if active_opposed:
        return StructureVolumeRelation.STRUCTURE_VOLUME_OPPOSED
    if active_aligned:
        return StructureVolumeRelation.STRUCTURE_SUPPORTED
    if active_shock:
        return StructureVolumeRelation.STRUCTURE_SHOCK_UNCONFIRMED

    active_usable = _sum(active, "usable_count")
    active_observed = _sum(active, "observed_bar_count")
    if active_usable:
        pre_aligned = _sum(pre, "aligned_confirmed_count")
        pre_opposed = _sum(pre, "opposed_confirmed_count")
        pre_conflict = _sum(pre, "conflict_count")
        pre_risk = _sum(pre, "opposing_absorption_count") + _sum(
            pre, "same_direction_reclaim_count"
        )
        pre_shock = _sum(pre, "shock_count")
        if pre_conflict or (pre_aligned and pre_opposed):
            return StructureVolumeRelation.STRUCTURE_VOLUME_CONFLICT
        if pre_risk:
            return StructureVolumeRelation.STRUCTURE_ABSORPTION_RISK
        if pre_opposed:
            return StructureVolumeRelation.STRUCTURE_VOLUME_OPPOSED
        if pre_shock:
            return StructureVolumeRelation.STRUCTURE_SHOCK_UNCONFIRMED
        return StructureVolumeRelation.STRUCTURE_PARTICIPATION_WEAK

    if active_observed:
        return StructureVolumeRelation.STRUCTURE_VOLUME_UNKNOWN
    return StructureVolumeRelation.STRUCTURE_VOLUME_UNKNOWN


def link_structure_event_to_volume(
    event: MarketStructureEventRecord,
    history: Sequence[VolumeEvidenceSnapshot],
    *,
    pre_event_bars: int = 2,
    follow_through_bars: int = 2,
    as_of_bar: int | None = None,
) -> StructureVolumeLink:
    """Causally relate one authoritative same-timeframe event to Volume history."""

    if not _eligible_structure_event(event):
        raise ValueError("only confirmed BOS/CHoCH records can be linked to Volume")
    if pre_event_bars < 0 or follow_through_bars < 0:
        raise ValueError("Structure/Volume window lengths must be non-negative")

    selected = tuple(
        snapshot
        for snapshot in history
        if snapshot.is_confirmed
        and (as_of_bar is None or snapshot.bar_index <= as_of_bar)
    )
    if selected:
        symbol = selected[0].symbol
        timeframe = selected[0].timeframe
        if any(snapshot.symbol != symbol for snapshot in selected):
            raise ValueError("Volume history contains multiple symbols")
        if any(snapshot.timeframe != timeframe for snapshot in selected):
            raise ValueError("Volume history contains multiple timeframes")
        _validate_namespace(event, symbol=symbol, timeframe=timeframe)
        if selected[-1].bar_index < event.event_bar:
            raise ValueError("Volume as-of boundary precedes Structure confirmation")
        at_event = next(
            (snapshot for snapshot in selected if snapshot.bar_index == event.event_bar),
            None,
        )
        if at_event is None:
            raise ValueError("Volume history has no bar aligned to Structure confirmation")
        if not _timestamps_equal(at_event.timestamp, event.confirmed_at):
            raise ValueError("Structure confirmation timestamp is not aligned to Volume history")
    else:
        symbol = (event.symbol or "").strip().upper()
        timeframe = (event.timeframe or "").strip().lower()

    event_direction = int(event.direction)
    history_by_bar = {snapshot.bar_index: snapshot for snapshot in selected}
    last_available = selected[-1].bar_index if selected else -1
    follow_end = min(event.event_bar + follow_through_bars, last_available)
    windows = (
        _summarize_window(
            timing=StructureVolumeTiming.PRE_EVENT,
            start_bar=event.event_bar - pre_event_bars,
            end_bar=event.event_bar - 1,
            expected_bar_count=pre_event_bars,
            history_by_bar=history_by_bar,
            event_direction=event_direction,
        ),
        _summarize_window(
            timing=StructureVolumeTiming.AT_EVENT,
            start_bar=event.event_bar,
            end_bar=event.event_bar,
            expected_bar_count=1,
            history_by_bar=history_by_bar,
            event_direction=event_direction,
        ),
        _summarize_window(
            timing=StructureVolumeTiming.FOLLOW_THROUGH,
            start_bar=event.event_bar + 1,
            end_bar=follow_end,
            expected_bar_count=follow_through_bars,
            history_by_bar=history_by_bar,
            event_direction=event_direction,
        ),
    )
    relation = _resolve_relation(windows)
    reasons = (
        f"event={event.event_uid}",
        f"aligned_confirmed={_sum(windows, 'aligned_confirmed_count')}",
        f"opposed_confirmed={_sum(windows, 'opposed_confirmed_count')}",
        f"shock={_sum(windows, 'shock_count')}",
        f"unavailable={_sum(windows, 'unavailable_count')}",
    )
    return StructureVolumeLink(
        event_uid=event.event_uid,
        symbol=symbol,
        timeframe=timeframe,
        scope=event.scope,
        event_type=event.event_type,
        bos_maturity=event.bos_maturity,
        event_direction=event_direction,
        event_bar=event.event_bar,
        confirmed_at=event.confirmed_at,
        broken_level=event.broken_level,
        assessed_at=selected[-1].timestamp if selected else None,
        relation=relation,
        windows=windows,
        reasons=reasons,
    )


def link_structure_events_to_volume(
    events: Iterable[MarketStructureEventRecord],
    history: Sequence[VolumeEvidenceSnapshot],
    *,
    pre_event_bars: int = 2,
    follow_through_bars: int = 2,
) -> tuple[StructureVolumeLink, ...]:
    return tuple(
        link_structure_event_to_volume(
            event,
            history,
            pre_event_bars=pre_event_bars,
            follow_through_bars=follow_through_bars,
        )
        for event in events
        if _eligible_structure_event(event)
    )


def find_participation_without_structure(
    history: Sequence[VolumeEvidenceSnapshot],
    events: Iterable[MarketStructureEventRecord],
) -> tuple[ParticipationWithoutStructure, ...]:
    if not history:
        return ()
    symbol = history[0].symbol
    timeframe = history[0].timeframe
    event_bars: set[int] = set()
    for event in events:
        if not _eligible_structure_event(event):
            continue
        _validate_namespace(event, symbol=symbol, timeframe=timeframe)
        event_bars.add(event.event_bar)

    return tuple(
        ParticipationWithoutStructure(
            symbol=snapshot.symbol,
            timeframe=snapshot.timeframe,
            bar_index=snapshot.bar_index,
            timestamp=snapshot.timestamp,
            state=snapshot.state,
            evidence_direction=snapshot.evidence_direction,
            status=snapshot.status,
        )
        for snapshot in history
        if snapshot.is_confirmed
        and snapshot.has_usable_measurement
        and snapshot.state in _ACTIVE_PARTICIPATION_STATES
        and snapshot.bar_index not in event_bars
    )


__all__ = [
    "ParticipationWithoutStructure",
    "StructureVolumeLink",
    "StructureVolumeRelation",
    "StructureVolumeTiming",
    "VolumeEvidenceDataQuality",
    "VolumeEvidenceEngine",
    "VolumeEvidenceSnapshot",
    "VolumeEvidenceStatus",
    "VolumeWindowEvidence",
    "find_participation_without_structure",
    "link_structure_event_to_volume",
    "link_structure_events_to_volume",
]
