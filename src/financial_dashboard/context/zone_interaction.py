from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ZoneInteractionState(StrEnum):
    UNTOUCHED = "UNTOUCHED"
    APPROACHING = "APPROACHING"
    TESTING = "TESTING"
    DEFENDED = "DEFENDED"
    WEAKENING = "WEAKENING"
    BEING_CONSUMED = "BEING_CONSUMED"
    ACCEPTED_THROUGH = "ACCEPTED_THROUGH"
    RECLAIMED = "RECLAIMED"
    ROLE_REVERSAL_TEST = "ROLE_REVERSAL_TEST"
    INVALIDATED = "INVALIDATED"
    HISTORICAL_REFERENCE = "HISTORICAL_REFERENCE"


@dataclass(frozen=True, slots=True)
class ZoneInteractionEvent:
    """One deterministic derived transition for a qualified-zone identity."""

    zone_id: str
    previous_state: ZoneInteractionState | None
    state: ZoneInteractionState
    observed_at: Any
    price: float
    reason: str

    def __post_init__(self) -> None:
        if not self.zone_id.strip():
            raise ValueError("zone_id must be non-empty")
        if self.observed_at is None:
            raise ValueError("observed_at must be known")
        if not self.reason.strip():
            raise ValueError("interaction-event reason must be non-empty")


def interval_distance(price: float, low: float, high: float) -> float:
    lower, upper = sorted((float(low), float(high)))
    value = float(price)
    if value < lower:
        return lower - value
    if value > upper:
        return value - upper
    return 0.0


def price_position(price: float, low: float, high: float) -> str:
    lower, upper = sorted((float(low), float(high)))
    value = float(price)
    if value < lower:
        return "BELOW"
    if value > upper:
        return "ABOVE"
    return "INSIDE"


def classify_zone_interaction(
    *,
    side: str,
    low: float,
    high: float,
    current_price: float,
    reference_atr: float,
    native_lifecycle: str | None = None,
    native_event: str | None = None,
    prior_state: ZoneInteractionState | None = None,
    near_atr: float = 0.35,
) -> ZoneInteractionState:
    """Derive a conservative interaction state without rewriting native lifecycle.

    Native lifecycle/event tokens are treated as observations only.  The function is
    intentionally small and deterministic so historical replay can call it for each
    causal prefix.  It does not infer acceptance/defence from a single wick when no
    native or prior-state evidence exists.
    """

    normalized_side = str(side).strip().upper()
    if normalized_side not in {"SUPPORT", "RESISTANCE"}:
        raise ValueError("zone side must be SUPPORT or RESISTANCE")
    if reference_atr <= 0:
        raise ValueError("reference_atr must be positive")
    if near_atr < 0:
        raise ValueError("near_atr must be non-negative")

    lifecycle = "" if native_lifecycle is None else str(native_lifecycle).strip().upper()
    event = "" if native_event is None else str(native_event).strip().upper()
    position = price_position(current_price, low, high)

    if lifecycle in {"ARCHIVED"}:
        return ZoneInteractionState.HISTORICAL_REFERENCE
    if lifecycle in {"INVALIDATED"}:
        return ZoneInteractionState.INVALIDATED
    if lifecycle in {"BROKEN"}:
        challenged_through = (
            normalized_side == "SUPPORT" and position == "BELOW"
        ) or (
            normalized_side == "RESISTANCE" and position == "ABOVE"
        )
        return (
            ZoneInteractionState.ACCEPTED_THROUGH
            if challenged_through
            else ZoneInteractionState.HISTORICAL_REFERENCE
        )

    if "RECLAIM" in event:
        return ZoneInteractionState.RECLAIMED
    if "ROLE_REVERSAL" in event and position == "INSIDE":
        return ZoneInteractionState.ROLE_REVERSAL_TEST
    if "HELD" in event or lifecycle == "BREAK_FAILED":
        return ZoneInteractionState.DEFENDED
    if lifecycle in {"BREAK_CANDIDATE", "BREAK_ATTEMPT"}:
        return ZoneInteractionState.WEAKENING

    if position == "INSIDE":
        if prior_state in {ZoneInteractionState.TESTING, ZoneInteractionState.WEAKENING}:
            return ZoneInteractionState.BEING_CONSUMED
        return ZoneInteractionState.TESTING

    distance_atr = interval_distance(current_price, low, high) / float(reference_atr)
    if distance_atr <= near_atr:
        return ZoneInteractionState.APPROACHING
    return ZoneInteractionState.UNTOUCHED


def transition_event(
    *,
    zone_id: str,
    previous_state: ZoneInteractionState | None,
    state: ZoneInteractionState,
    observed_at: Any,
    price: float,
    reason: str,
) -> ZoneInteractionEvent | None:
    """Return an appendable event only when the derived state actually changes."""

    if previous_state is state:
        return None
    return ZoneInteractionEvent(
        zone_id=zone_id,
        previous_state=previous_state,
        state=state,
        observed_at=observed_at,
        price=float(price),
        reason=reason,
    )


__all__ = [
    "ZoneInteractionEvent",
    "ZoneInteractionState",
    "classify_zone_interaction",
    "interval_distance",
    "price_position",
    "transition_event",
]
