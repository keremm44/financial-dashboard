from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any


class ZoneKind(StrEnum):
    RANGE_BOUNDARY = "RANGE_BOUNDARY"
    ROLE_REVERSAL = "ROLE_REVERSAL"


class ZoneSide(StrEnum):
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"


class ZoneLifecycle(StrEnum):
    FORMING = "FORMING"
    CONFIRMED = "CONFIRMED"
    ACTIVE = "ACTIVE"
    WEAK = "WEAK"
    BREAK_ATTEMPT = "BREAK_ATTEMPT"
    BREAK_CANDIDATE = "BREAK_CANDIDATE"
    BREAK_FAILED = "BREAK_FAILED"
    BROKEN = "BROKEN"
    ARCHIVED = "ARCHIVED"
    INVALIDATED = "INVALIDATED"


_TERMINAL_LIFECYCLES = {
    ZoneLifecycle.BROKEN,
    ZoneLifecycle.ARCHIVED,
    ZoneLifecycle.INVALIDATED,
}
_CONFLUENCE_LIFECYCLES = {
    ZoneLifecycle.CONFIRMED,
    ZoneLifecycle.ACTIVE,
    ZoneLifecycle.WEAK,
    ZoneLifecycle.BREAK_ATTEMPT,
    ZoneLifecycle.BREAK_CANDIDATE,
    ZoneLifecycle.BREAK_FAILED,
}


@dataclass(frozen=True, slots=True)
class SupportResistanceZone:
    """Immutable current view of one stable support/resistance zone identity."""

    zone_uid: str
    source_range_identity: int
    kind: ZoneKind
    side: ZoneSide
    low: float
    high: float
    center: float
    lifecycle: ZoneLifecycle
    range_state: str
    quality: float
    touches: int
    boundary_stability: float
    reference_atr: float
    origin_bar: int | None
    created_bar: int
    created_at: Any
    last_updated_bar: int
    last_updated_at: Any
    last_transition_bar: int
    last_transition_at: Any
    break_direction: int = 0
    symbol: str | None = None
    timeframe: str | None = None

    @property
    def is_active(self) -> bool:
        return self.lifecycle not in _TERMINAL_LIFECYCLES

    @property
    def is_confluence_eligible(self) -> bool:
        return self.lifecycle in _CONFLUENCE_LIFECYCLES

    @property
    def width(self) -> float:
        return max(0.0, self.high - self.low)

    def with_namespace(self, *, symbol: str, timeframe: str) -> "SupportResistanceZone":
        normalized_timeframe = timeframe.strip().lower()
        return replace(
            self,
            zone_uid=f"{symbol}:{normalized_timeframe}:{self.zone_uid}",
            symbol=symbol,
            timeframe=normalized_timeframe,
        )


@dataclass(frozen=True, slots=True)
class ZoneLifecycleEvent:
    """Append-only lifecycle transition; geometry drift does not rewrite this fact."""

    event_uid: str
    zone_uid: str
    source_range_identity: int
    kind: ZoneKind
    side: ZoneSide
    previous_lifecycle: ZoneLifecycle | None
    lifecycle: ZoneLifecycle
    event_bar: int
    event_at: Any
    reason: str
    symbol: str | None = None
    timeframe: str | None = None

    def with_namespace(self, *, symbol: str, timeframe: str) -> "ZoneLifecycleEvent":
        normalized_timeframe = timeframe.strip().lower()
        prefix = f"{symbol}:{normalized_timeframe}:"
        return replace(
            self,
            event_uid=f"{prefix}{self.event_uid}",
            zone_uid=f"{prefix}{self.zone_uid}",
            symbol=symbol,
            timeframe=normalized_timeframe,
        )


def _state_value(state: Any) -> str:
    return str(getattr(state, "value", state))


def _base_lifecycle(state: Any) -> ZoneLifecycle:
    value = _state_value(state)
    if value in {"RANGE_CANDIDATE", "RANGE_GEOMETRY"}:
        return ZoneLifecycle.FORMING
    if value in {"RANGE_DEFINED", "RANGE_STABILIZING"}:
        return ZoneLifecycle.CONFIRMED
    if value == "RANGE_ACTIVE":
        return ZoneLifecycle.ACTIVE
    if value == "RANGE_WEAK":
        return ZoneLifecycle.WEAK
    if value == "RANGE_BREAK_ATTEMPT":
        return ZoneLifecycle.BREAK_ATTEMPT
    if value == "RANGE_BREAK_CANDIDATE":
        return ZoneLifecycle.BREAK_CANDIDATE
    if value == "RANGE_BREAK_FAILED":
        return ZoneLifecycle.BREAK_FAILED
    if value == "RANGE_BREAK_CONFIRMED":
        return ZoneLifecycle.BROKEN
    return ZoneLifecycle.INVALIDATED


def _range_zone_lifecycle(range_snapshot: Any, side: ZoneSide) -> ZoneLifecycle:
    value = _state_value(range_snapshot.state)
    break_direction = int(range_snapshot.break_direction)
    challenged = (
        break_direction == 1 and side is ZoneSide.RESISTANCE
    ) or (
        break_direction == -1 and side is ZoneSide.SUPPORT
    )

    if value == "RANGE_BREAK_CONFIRMED":
        return ZoneLifecycle.BROKEN if challenged else ZoneLifecycle.ARCHIVED
    if value in {
        "RANGE_BREAK_ATTEMPT",
        "RANGE_BREAK_CANDIDATE",
        "RANGE_BREAK_FAILED",
    }:
        if challenged:
            return _base_lifecycle(value)
        return _base_lifecycle(range_snapshot.break_return_state or "RANGE_ACTIVE")
    return _base_lifecycle(value)


class SupportResistanceZoneLedger:
    """Stable zone identities plus append-only lifecycle transitions."""

    def __init__(self, *, role_break_confirm_bars: int = 2, min_tick: float = 0.01) -> None:
        self.role_break_confirm_bars = max(1, int(role_break_confirm_bars))
        self.min_tick = float(min_tick)
        self._zones: dict[str, SupportResistanceZone] = {}
        self._events: list[ZoneLifecycleEvent] = []
        self._role_breach_counts: dict[str, int] = {}
        self._next_event_identity = 1

    def reset(self) -> None:
        self._zones = {}
        self._events = []
        self._role_breach_counts = {}
        self._next_event_identity = 1

    def _transition(
        self,
        zone: SupportResistanceZone,
        *,
        lifecycle: ZoneLifecycle,
        bar_index: int,
        timestamp: Any,
        reason: str,
    ) -> SupportResistanceZone:
        previous = self._zones.get(zone.zone_uid)
        previous_lifecycle = previous.lifecycle if previous is not None else None
        if previous_lifecycle is lifecycle:
            return zone
        transitioned = replace(
            zone,
            lifecycle=lifecycle,
            last_transition_bar=bar_index,
            last_transition_at=timestamp,
        )
        self._events.append(
            ZoneLifecycleEvent(
                event_uid=f"ZONE_EVENT:{self._next_event_identity}",
                zone_uid=zone.zone_uid,
                source_range_identity=zone.source_range_identity,
                kind=zone.kind,
                side=zone.side,
                previous_lifecycle=previous_lifecycle,
                lifecycle=lifecycle,
                event_bar=bar_index,
                event_at=timestamp,
                reason=reason,
            )
        )
        self._next_event_identity += 1
        return transitioned

    def _upsert(
        self,
        *,
        zone_uid: str,
        source_range_identity: int,
        kind: ZoneKind,
        side: ZoneSide,
        low: float,
        high: float,
        lifecycle: ZoneLifecycle,
        range_state: str,
        quality: float,
        touches: int,
        boundary_stability: float,
        reference_atr: float,
        origin_bar: int | None,
        bar_index: int,
        timestamp: Any,
        break_direction: int,
        reason: str,
    ) -> SupportResistanceZone:
        previous = self._zones.get(zone_uid)
        if previous is not None and previous.lifecycle in _TERMINAL_LIFECYCLES:
            return previous
        created_bar = previous.created_bar if previous is not None else bar_index
        created_at = previous.created_at if previous is not None else timestamp
        last_transition_bar = previous.last_transition_bar if previous is not None else bar_index
        last_transition_at = previous.last_transition_at if previous is not None else timestamp
        zone = SupportResistanceZone(
            zone_uid=zone_uid,
            source_range_identity=source_range_identity,
            kind=kind,
            side=side,
            low=float(min(low, high)),
            high=float(max(low, high)),
            center=float((low + high) * 0.5),
            lifecycle=lifecycle,
            range_state=range_state,
            quality=float(quality),
            touches=int(touches),
            boundary_stability=float(boundary_stability),
            reference_atr=max(float(reference_atr), self.min_tick),
            origin_bar=previous.origin_bar if previous is not None else origin_bar,
            created_bar=created_bar,
            created_at=created_at,
            last_updated_bar=bar_index,
            last_updated_at=timestamp,
            last_transition_bar=last_transition_bar,
            last_transition_at=last_transition_at,
            break_direction=int(break_direction),
        )
        zone = self._transition(
            zone,
            lifecycle=lifecycle,
            bar_index=bar_index,
            timestamp=timestamp,
            reason=reason,
        )
        self._zones[zone_uid] = zone
        return zone

    def _observe_range(
        self,
        range_snapshot: Any,
        *,
        bar_index: int,
        timestamp: Any,
        reference_atr: float,
    ) -> set[str]:
        observed: set[str] = set()
        if not bool(range_snapshot.valid) or not int(range_snapshot.identity):
            return observed
        required = (
            range_snapshot.upper_bottom,
            range_snapshot.upper_top,
            range_snapshot.lower_bottom,
            range_snapshot.lower_top,
        )
        if any(value is None for value in required):
            return observed

        range_state = _state_value(range_snapshot.state)
        for suffix, side, low, high, touches in (
            (
                "UPPER",
                ZoneSide.RESISTANCE,
                range_snapshot.upper_bottom,
                range_snapshot.upper_top,
                range_snapshot.upper_touches,
            ),
            (
                "LOWER",
                ZoneSide.SUPPORT,
                range_snapshot.lower_bottom,
                range_snapshot.lower_top,
                range_snapshot.lower_touches,
            ),
        ):
            zone_uid = f"RANGE:{range_snapshot.identity}:{suffix}"
            lifecycle = _range_zone_lifecycle(range_snapshot, side)
            self._upsert(
                zone_uid=zone_uid,
                source_range_identity=range_snapshot.identity,
                kind=ZoneKind.RANGE_BOUNDARY,
                side=side,
                low=low,
                high=high,
                lifecycle=lifecycle,
                range_state=range_state,
                quality=range_snapshot.quality,
                touches=touches,
                boundary_stability=range_snapshot.boundary_stability,
                reference_atr=reference_atr,
                origin_bar=range_snapshot.start_index,
                bar_index=bar_index,
                timestamp=timestamp,
                break_direction=range_snapshot.break_direction,
                reason=f"{range_state}:{side.value}",
            )
            observed.add(zone_uid)
        return observed

    def _observe_role_zone(
        self,
        *,
        source_range_identity: int | None,
        side: ZoneSide,
        bounds: tuple[float, float] | None,
        close: float,
        reference_atr: float,
        quality: float,
        boundary_stability: float,
        bar_index: int,
        timestamp: Any,
    ) -> str | None:
        if source_range_identity is None or bounds is None:
            return None
        low, high = min(bounds), max(bounds)
        suffix = "ROLE_SUPPORT" if side is ZoneSide.SUPPORT else "ROLE_RESISTANCE"
        zone_uid = f"RANGE:{source_range_identity}:{suffix}"
        previous = self._zones.get(zone_uid)
        if previous is not None and previous.lifecycle in _TERMINAL_LIFECYCLES:
            return zone_uid

        buffer = max(reference_atr * 0.07, self.min_tick)
        breached = close < low - buffer if side is ZoneSide.SUPPORT else close > high + buffer
        count = self._role_breach_counts.get(zone_uid, 0)
        if breached:
            count += 1
            lifecycle = (
                ZoneLifecycle.INVALIDATED
                if count >= self.role_break_confirm_bars
                else ZoneLifecycle.BREAK_CANDIDATE
            )
            reason = "ROLE_REVERSAL_INVALIDATED" if lifecycle is ZoneLifecycle.INVALIDATED else "ROLE_REVERSAL_BREAK_CANDIDATE"
        else:
            lifecycle = ZoneLifecycle.ACTIVE
            reason = "ROLE_REVERSAL_BREAK_FAILED" if count else "ROLE_REVERSAL_CREATED"
            count = 0
        self._role_breach_counts[zone_uid] = count
        self._upsert(
            zone_uid=zone_uid,
            source_range_identity=source_range_identity,
            kind=ZoneKind.ROLE_REVERSAL,
            side=side,
            low=low,
            high=high,
            lifecycle=lifecycle,
            range_state="ROLE_REVERSAL",
            quality=previous.quality if previous is not None else quality,
            touches=0,
            boundary_stability=(
                previous.boundary_stability if previous is not None else boundary_stability
            ),
            reference_atr=previous.reference_atr if previous is not None else reference_atr,
            origin_bar=bar_index,
            bar_index=bar_index,
            timestamp=timestamp,
            break_direction=1 if side is ZoneSide.SUPPORT else -1,
            reason=reason,
        )
        return zone_uid

    def observe(
        self,
        range_snapshot: Any,
        *,
        role_support: tuple[float, float] | None,
        role_support_identity: int | None,
        role_resistance: tuple[float, float] | None,
        role_resistance_identity: int | None,
        bar_index: int,
        timestamp: Any,
        close: float,
        reference_atr: float,
    ) -> tuple[SupportResistanceZone, ...]:
        observed = self._observe_range(
            range_snapshot,
            bar_index=bar_index,
            timestamp=timestamp,
            reference_atr=reference_atr,
        )
        role_quality = float(range_snapshot.quality) if bool(range_snapshot.valid) else 50.0
        role_stability = float(range_snapshot.boundary_stability) if bool(range_snapshot.valid) else 50.0
        role_support_uid = self._observe_role_zone(
            source_range_identity=role_support_identity,
            side=ZoneSide.SUPPORT,
            bounds=role_support,
            close=close,
            reference_atr=reference_atr,
            quality=role_quality,
            boundary_stability=role_stability,
            bar_index=bar_index,
            timestamp=timestamp,
        )
        role_resistance_uid = self._observe_role_zone(
            source_range_identity=role_resistance_identity,
            side=ZoneSide.RESISTANCE,
            bounds=role_resistance,
            close=close,
            reference_atr=reference_atr,
            quality=role_quality,
            boundary_stability=role_stability,
            bar_index=bar_index,
            timestamp=timestamp,
        )
        observed.update(uid for uid in (role_support_uid, role_resistance_uid) if uid is not None)

        for zone_uid, zone in tuple(self._zones.items()):
            if not zone.is_active or zone_uid in observed:
                continue
            archived = self._transition(
                zone,
                lifecycle=ZoneLifecycle.ARCHIVED,
                bar_index=bar_index,
                timestamp=timestamp,
                reason=(
                    "RANGE_NO_LONGER_CURRENT"
                    if zone.kind is ZoneKind.RANGE_BOUNDARY
                    else "ROLE_REVERSAL_REPLACED_OR_REMOVED"
                ),
            )
            self._zones[zone_uid] = replace(
                archived,
                last_updated_bar=bar_index,
                last_updated_at=timestamp,
            )
        return self.snapshot()

    def snapshot(self) -> tuple[SupportResistanceZone, ...]:
        return tuple(
            sorted(
                self._zones.values(),
                key=lambda zone: (zone.created_bar, zone.source_range_identity, zone.kind.value, zone.side.value),
            )
        )

    def active(self) -> tuple[SupportResistanceZone, ...]:
        return tuple(zone for zone in self.snapshot() if zone.is_active)

    @property
    def events(self) -> tuple[ZoneLifecycleEvent, ...]:
        return tuple(self._events)
