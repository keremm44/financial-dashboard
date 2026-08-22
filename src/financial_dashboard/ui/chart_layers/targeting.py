from __future__ import annotations

import plotly.graph_objects as go

from financial_dashboard.targeting.models import TargetClusterKind, TargetingSnapshot


def _annotation(cluster) -> str:
    prefix = "LQ target" if cluster.kind is TargetClusterKind.LIQUIDITY_TARGET else "Technical zone"
    return (
        f"{prefix} · {cluster.quality.value} · "
        f"{cluster.distance_atr:.2f} ATR · origins={cluster.independent_origin_count}"
    )


def add_targeting_layers(
    figure: go.Figure,
    snapshot: TargetingSnapshot | None,
    *,
    show_nearest: bool = True,
    show_all_clusters: bool = False,
) -> None:
    """Add descriptive target geometry without creating trading instructions.

    Nearest upside/downside Liquidity targets are the compact default surface.
    Full cluster rendering is opt-in because the evidence ledger can be dense.
    """
    if snapshot is None:
        return

    selected = []
    if show_all_clusters:
        selected.extend(snapshot.clusters)
    elif show_nearest:
        for cluster in (
            snapshot.nearest_upside_target,
            snapshot.nearest_downside_target,
        ):
            if cluster is not None:
                selected.append(cluster)

    seen: set[str] = set()
    for cluster in selected:
        if cluster.identity in seen:
            continue
        seen.add(cluster.identity)
        figure.add_hrect(
            y0=cluster.envelope_low,
            y1=cluster.envelope_high,
            line_width=1,
            annotation_text=_annotation(cluster),
            annotation_position="top right",
            layer="below",
        )
        if cluster.liquidity_anchor is not None:
            figure.add_hline(
                y=cluster.liquidity_anchor,
                line_width=1,
                line_dash="dot",
                annotation_text="Liquidity anchor",
                annotation_position="bottom right",
            )
        if cluster.core_low is not None and cluster.core_high is not None:
            figure.add_hrect(
                y0=cluster.core_low,
                y1=cluster.core_high,
                line_width=1,
                annotation_text="Core overlap",
                annotation_position="bottom left",
                layer="below",
            )


__all__ = ["add_targeting_layers"]
