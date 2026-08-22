from __future__ import annotations

from typing import Iterable

import pandas as pd
import plotly.graph_objects as go

from financial_dashboard.three_domain_replay import ThreeDomainReplayResult


_SIDE_COLORS = {
    "SUPPORT": "rgba(46, 160, 67, 0.14)",
    "RESISTANCE": "rgba(218, 54, 51, 0.14)",
}


def add_location_layers(
    figure: go.Figure,
    result: ThreeDomainReplayResult,
    *,
    first_timestamp: pd.Timestamp,
    last_timestamp: pd.Timestamp,
    zone_timeframes: Iterable[str],
    show_confluence: bool,
    show_conflicts: bool,
) -> None:
    selected_zone_timeframes = tuple(zone_timeframes)
    selected_zone_set = set(selected_zone_timeframes)

    for zone_timeframe in selected_zone_timeframes:
        if zone_timeframe not in result.timeframes:
            continue
        zone_replay = result.structure_location.replay_for(zone_timeframe)
        for zone in zone_replay.support_resistance.zones:
            zone_start = max(pd.Timestamp(zone.created_at), first_timestamp)
            zone_end = last_timestamp
            if not zone.is_active and zone.last_transition_at is not None:
                zone_end = min(pd.Timestamp(zone.last_transition_at), last_timestamp)
            if zone_end < first_timestamp:
                continue
            figure.add_shape(
                type="rect",
                x0=zone_start,
                x1=zone_end,
                y0=zone.low,
                y1=zone.high,
                line={"width": 1, "color": _SIDE_COLORS[zone.side.value]},
                fillcolor=_SIDE_COLORS[zone.side.value],
                layer="below",
            )
            figure.add_annotation(
                x=zone_start,
                y=zone.center,
                text=f"{zone_timeframe} {zone.side.value} · {zone.lifecycle.value}",
                showarrow=False,
                xanchor="left",
                font={"size": 9, "color": "#8b949e"},
            )

    if show_confluence:
        for cluster in result.location.confluence:
            if not selected_zone_set.intersection(cluster.timeframes):
                continue
            figure.add_hrect(
                y0=cluster.envelope_low,
                y1=cluster.envelope_high,
                fillcolor="rgba(210, 153, 34, 0.09)",
                line_width=1,
                line_color="rgba(210, 153, 34, 0.5)",
                annotation_text=f"Confluence {cluster.score:.2f}",
                annotation_position="top right",
                layer="below",
            )

    if show_conflicts:
        for conflict in result.location.opposing_conflicts:
            if not selected_zone_set.intersection(
                (conflict.support_timeframe, conflict.resistance_timeframe)
            ):
                continue
            if conflict.overlap_low is None or conflict.overlap_high is None:
                continue
            figure.add_hrect(
                y0=conflict.overlap_low,
                y1=conflict.overlap_high,
                fillcolor="rgba(163, 113, 247, 0.09)",
                line_width=1,
                line_color="rgba(163, 113, 247, 0.5)",
                annotation_text=f"Opposing-zone {conflict.kind.value}",
                annotation_position="bottom right",
                layer="below",
            )


__all__ = ["add_location_layers"]
