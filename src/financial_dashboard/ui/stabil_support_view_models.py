from __future__ import annotations

import pandas as pd

from financial_dashboard.engines.stabil_support_lifecycle import (
    StabilSupportLifecycleSnapshot,
    SupportDynamics,
    SupportLifecycleEvent,
    SupportLifecycleEventType,
    SupportValidity,
)
from financial_dashboard.stabil_support_replay import StabilSupportHistoricalReplay


def _fmt(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}"


def stabil_support_display_state(snapshot: StabilSupportLifecycleSnapshot) -> str:
    if snapshot.validity is SupportValidity.NO_SUPPORT:
        return "DESTEK YOK"

    latest_event = snapshot.events[-1] if snapshot.events else None
    if (
        latest_event is not None
        and latest_event.event_type is SupportLifecycleEventType.SUPPORT_RECLAIMED
        and latest_event.event_time == snapshot.as_of
    ):
        return "DESTEK GERİ ALINDI"

    if snapshot.validity is SupportValidity.BELOW_FLOOR:
        return "DESTEK TABANI KIRILDI"
    if snapshot.validity is SupportValidity.BREACHED:
        return "DESTEK ALTINDA"
    if snapshot.dynamics is SupportDynamics.AT_SUPPORT:
        return "DESTEK TEST EDİLİYOR"
    if snapshot.dynamics is SupportDynamics.EXPANDING:
        return "DESTEK ÜSTÜNDE GENİŞLEME"
    if snapshot.dynamics is SupportDynamics.CONTRACTING:
        return "DESTEĞE GERİ DÖNÜŞ"
    return "DESTEK KORUNUYOR"


def stabil_support_summary_values(
    snapshot: StabilSupportLifecycleSnapshot,
) -> dict[str, str]:
    return {
        "State": stabil_support_display_state(snapshot),
        "Support": _fmt(snapshot.support_level),
        "Floor": _fmt(snapshot.support_floor),
        "Distance %": (
            "—" if snapshot.distance_pct is None else f"{snapshot.distance_pct:+.2f}%"
        ),
        "Distance ATR": (
            "—" if snapshot.distance_atr is None else f"{snapshot.distance_atr:+.2f} ATR"
        ),
        "Distance direction": snapshot.dynamics.value,
        "Bars above": str(snapshot.bars_above_support),
        "Bars below": str(snapshot.bars_below_support),
        "Reclaims": str(snapshot.reclaim_count),
        "Progression": snapshot.progression.value,
    }


def _event_row(event: SupportLifecycleEvent) -> dict[str, object]:
    return {
        "#": event.sequence,
        "Event": event.event_type.value,
        "Event time": event.event_time,
        "Origin": event.origin_at,
        "Confirmed": event.confirmed_at,
        "Available": event.available_at,
        "Support": event.support_level,
        "Floor": event.support_floor,
        "Previous support": event.previous_support,
        "New support": event.new_support,
        "Price": event.price,
        "Distance %": event.distance_pct,
        "Distance ATR": event.distance_atr,
        "Bars since support": event.bars_since_support,
        "Bars above": event.bars_above_support,
        "Bars below": event.bars_below_support,
        "Reclaim count": event.reclaim_count,
        "Progression": event.progression.value,
    }


def stabil_support_events_frame(
    snapshot: StabilSupportLifecycleSnapshot,
) -> pd.DataFrame:
    columns = (
        "#",
        "Event",
        "Event time",
        "Origin",
        "Confirmed",
        "Available",
        "Support",
        "Floor",
        "Previous support",
        "New support",
        "Price",
        "Distance %",
        "Distance ATR",
        "Bars since support",
        "Bars above",
        "Bars below",
        "Reclaim count",
        "Progression",
    )
    rows = [_event_row(event) for event in snapshot.events]
    return pd.DataFrame(rows, columns=columns)


def stabil_support_replay_frame(
    replay: StabilSupportHistoricalReplay,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for point in replay.points:
        snapshot = point.snapshot
        rows.append(
            {
                "As of": point.as_of,
                "Close": point.close,
                "State": stabil_support_display_state(snapshot),
                "Validity": snapshot.validity.value,
                "Dynamics": snapshot.dynamics.value,
                "Support": snapshot.support_level,
                "Floor": snapshot.support_floor,
                "Distance %": snapshot.distance_pct,
                "Distance ATR": snapshot.distance_atr,
                "Distance Δ ATR": snapshot.distance_delta_atr,
                "Bars above": snapshot.bars_above_support,
                "Bars below": snapshot.bars_below_support,
                "Reclaims": snapshot.reclaim_count,
                "Progression": snapshot.progression.value,
                "Wick below": snapshot.intrabar_below_support,
                "Close below": snapshot.close_below_support,
                "Close below floor": snapshot.close_below_floor,
            }
        )
    return pd.DataFrame(rows)


def _events_by_type(
    snapshot: StabilSupportLifecycleSnapshot,
    event_types: set[SupportLifecycleEventType],
) -> pd.DataFrame:
    rows = [_event_row(event) for event in snapshot.events if event.event_type in event_types]
    return pd.DataFrame(rows)


def stabil_support_reclaim_frame(
    snapshot: StabilSupportLifecycleSnapshot,
) -> pd.DataFrame:
    return _events_by_type(
        snapshot,
        {
            SupportLifecycleEventType.SUPPORT_BREACHED,
            SupportLifecycleEventType.SUPPORT_FLOOR_BROKEN,
            SupportLifecycleEventType.SUPPORT_RECLAIMED,
            SupportLifecycleEventType.SUPPORT_LOST,
        },
    )


def stabil_support_retest_frame(
    snapshot: StabilSupportLifecycleSnapshot,
) -> pd.DataFrame:
    return _events_by_type(
        snapshot,
        {
            SupportLifecycleEventType.SUPPORT_TESTED,
            SupportLifecycleEventType.SUPPORT_HELD,
        },
    )


def stabil_support_rebase_frame(
    snapshot: StabilSupportLifecycleSnapshot,
) -> pd.DataFrame:
    return _events_by_type(
        snapshot,
        {
            SupportLifecycleEventType.SUPPORT_REBASED_HIGHER,
            SupportLifecycleEventType.SUPPORT_REBASED_LOWER,
            SupportLifecycleEventType.SUPPORT_LOST,
        },
    )


def stabil_support_event_counts_frame(
    snapshot: StabilSupportLifecycleSnapshot,
) -> pd.DataFrame:
    counts: dict[str, int] = {}
    for event in snapshot.events:
        counts[event.event_type.value] = counts.get(event.event_type.value, 0) + 1
    return pd.DataFrame(
        ({"Event": event, "Count": count} for event, count in sorted(counts.items())),
        columns=("Event", "Count"),
    )


__all__ = [
    "stabil_support_display_state",
    "stabil_support_event_counts_frame",
    "stabil_support_events_frame",
    "stabil_support_rebase_frame",
    "stabil_support_reclaim_frame",
    "stabil_support_replay_frame",
    "stabil_support_retest_frame",
    "stabil_support_summary_values",
]
