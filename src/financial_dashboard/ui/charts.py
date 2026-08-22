from __future__ import annotations

from typing import Iterable

import pandas as pd
import plotly.graph_objects as go

from financial_dashboard.targeting.models import TargetingSnapshot
from financial_dashboard.three_domain_replay import ThreeDomainReplayResult
from financial_dashboard.ui.chart_layers import (
    add_location_layers,
    add_structure_events,
    add_targeting_layers,
)


def make_market_figure(
    result: ThreeDomainReplayResult,
    *,
    timeframe: str,
    zone_timeframes: Iterable[str] = (),
    bar_limit: int = 300,
    show_events: bool = True,
    show_confluence: bool = False,
    show_conflicts: bool = False,
    targeting: TargetingSnapshot | None = None,
    show_nearest_targets: bool = False,
    show_all_target_clusters: bool = False,
) -> go.Figure:
    """Compose a read-only market chart from independent domain overlays."""

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

    add_location_layers(
        figure,
        result,
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
        zone_timeframes=selected_zone_timeframes,
        show_confluence=show_confluence,
        show_conflicts=show_conflicts,
    )
    if show_events:
        add_structure_events(figure, result, timeframe=timeframe)
    if targeting is not None and (show_nearest_targets or show_all_target_clusters):
        add_targeting_layers(
            figure,
            targeting,
            show_nearest=show_nearest_targets,
            show_all_clusters=show_all_target_clusters,
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
