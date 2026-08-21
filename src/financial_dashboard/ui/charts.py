from __future__ import annotations

import math
from typing import Iterable

import pandas as pd
import plotly.graph_objects as go

from financial_dashboard.three_domain_replay import ThreeDomainReplayResult


_SIDE_COLORS = {
    "SUPPORT": "rgba(46, 160, 67, 0.14)",
    "RESISTANCE": "rgba(218, 54, 51, 0.14)",
}
_EVENT_COLORS = {
    "UP": "#2ea043",
    "DOWN": "#da3633",
    "NEUTRAL": "#8b949e",
}


def make_market_figure(
    result: ThreeDomainReplayResult,
    *,
    timeframe: str,
    zone_timeframes: Iterable[str] = (),
    bar_limit: int = 300,
    show_events: bool = True,
    show_confluence: bool = True,
    show_conflicts: bool = True,
) -> go.Figure:
    """Build a read-only market inspection chart from replayed facts."""

    replay = result.structure_location.replay_for(timeframe)
    frame = replay.input_batch.frame.tail(max(20, int(bar_limit))).copy()
    figure = go.Figure()
    figure.add_trace(
        go.Candlestick(
            x=frame["timestamp"],
            open=frame["open"],
            high=frame["high"],
            low=frame["low"],
            close=frame["close"],
            name=f"{result.symbol} {timeframe}",
            increasing_line_color="#2ea043",
            decreasing_line_color="#da3633",
        )
    )
    if frame.empty:
        return figure

    first_timestamp = pd.Timestamp(frame.iloc[0]["timestamp"])
    last_timestamp = pd.Timestamp(frame.iloc[-1]["timestamp"])
    selected_zone_timeframes = tuple(zone_timeframes) or (timeframe,)

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
        selected_zone_set = set(selected_zone_timeframes)
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
        selected_zone_set = set(selected_zone_timeframes)
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

    if show_events:
        market = replay.market_structure
        for event in market.events:
            if (
                event.confirmed_at is None
                or event.confirmation_close is None
                or not math.isfinite(event.confirmation_close)
            ):
                continue
            direction = event.direction.name
            symbol = "triangle-up" if direction == "UP" else "triangle-down"
            figure.add_trace(
                go.Scatter(
                    x=[event.confirmed_at],
                    y=[event.confirmation_close],
                    mode="markers",
                    name=f"{event.scope} {event.event_type} {direction}",
                    marker={
                        "symbol": symbol,
                        "size": 11,
                        "color": _EVENT_COLORS[direction],
                        "line": {"width": 1, "color": "white"},
                    },
                    text=[
                        f"{event.event_uid}<br>{event.confirmation_status.value}"
                        f"<br>quality={event.quality:.3f}"
                    ],
                    hovertemplate="%{text}<extra></extra>",
                    showlegend=False,
                )
            )

    figure.update_layout(
        title={
            "text": f"{result.symbol} · {timeframe} · closed + complete replay",
            "x": 0.01,
        },
        height=620,
        margin={"l": 20, "r": 20, "t": 55, "b": 20},
        hovermode="x unified",
        legend={"orientation": "h"},
        xaxis_rangeslider_visible=False,
        template="plotly_white",
    )
    figure.update_xaxes(title_text="Timestamp")
    figure.update_yaxes(title_text="Price", fixedrange=False)
    return figure
