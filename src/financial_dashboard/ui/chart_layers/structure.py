from __future__ import annotations

import math

import plotly.graph_objects as go

from financial_dashboard.three_domain_replay import ThreeDomainReplayResult


_EVENT_COLORS = {
    "UP": "#2ea043",
    "DOWN": "#da3633",
    "NEUTRAL": "#8b949e",
}


def add_structure_events(
    figure: go.Figure,
    result: ThreeDomainReplayResult,
    *,
    timeframe: str,
) -> None:
    market = result.structure_location.replay_for(timeframe).market_structure
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


__all__ = ["add_structure_events"]
