from __future__ import annotations

import pandas as pd

from financial_dashboard.targeting.models import TargetCluster
from financial_dashboard.targeting_replay_diagnostics import (
    cluster_stability,
    semantic_transition_ledger,
)
from financial_dashboard.targeting_historical_replay import TargetingHistoricalReplay


_DISTANCE_BANDS = (
    ("0–0.5 ATR", 0.0, 0.5),
    ("0.5–1 ATR", 0.5, 1.0),
    ("1–2 ATR", 1.0, 2.0),
    ("2–4 ATR", 2.0, 4.0),
    ("4+ ATR", 4.0, float("inf")),
)


def replay_points_frame(replay: TargetingHistoricalReplay) -> pd.DataFrame:
    rows = []
    for index, point in enumerate(replay.points):
        snapshot = point.snapshot
        rows.append(
            {
                "Point": index,
                "Available at": point.available_at,
                "Price": snapshot.current_price,
                "ATR": snapshot.reference_atr,
                "Clusters": len(snapshot.clusters),
                "Nearest up ATR": None if snapshot.nearest_upside_target is None else snapshot.nearest_upside_target.distance_atr,
                "Nearest down ATR": None if snapshot.nearest_downside_target is None else snapshot.nearest_downside_target.distance_atr,
            }
        )
    return pd.DataFrame(rows)


def semantic_transitions_frame(replay: TargetingHistoricalReplay) -> pd.DataFrame:
    rows = []
    for transition in semantic_transition_ledger(replay):
        rows.append(
            {
                "Available at": transition.available_at,
                "Field": transition.field,
                "Transition": transition.kind.value,
                "Previous": transition.previous_identity,
                "New": transition.new_identity,
                "Previous envelope": transition.previous_envelope,
                "New envelope": transition.new_envelope,
                "Previous ATR": transition.previous_distance_atr,
                "New ATR": transition.new_distance_atr,
            }
        )
    return pd.DataFrame(rows)


def distance_bands_frame(cluster_list: tuple[TargetCluster, ...]) -> pd.DataFrame:
    rows = []
    for label, lower, upper in _DISTANCE_BANDS:
        members = [
            cluster
            for cluster in cluster_list
            if cluster.distance_atr >= lower and cluster.distance_atr < upper
        ]
        rows.append(
            {
                "Distance band": label,
                "All": len(members),
                "Liquidity targets": sum(cluster.kind.value == "LIQUIDITY_TARGET" for cluster in members),
                "Technical zones": sum(cluster.kind.value == "TECHNICAL_ZONE" for cluster in members),
                "Above": sum(cluster.side.value == "ABOVE" for cluster in members),
                "Below": sum(cluster.side.value == "BELOW" for cluster in members),
            }
        )
    return pd.DataFrame(rows)


def cluster_anatomy_frame(cluster: TargetCluster) -> pd.DataFrame:
    rows = []
    for item in cluster.evidence:
        rows.append(
            {
                "Type": item.evidence_type.value,
                "Family": item.family.value,
                "TF": item.timeframe,
                "State": item.source_state,
                "Low": item.low,
                "High": item.high,
                "Anchor": item.anchor_price,
                "Roles": ", ".join(role.value for role in item.roles),
                "Origin event": item.origin_event_id,
                "Native origin": item.native_origin_id,
                "Origin time": item.origin_time,
                "Confirmed at": item.confirmed_at,
                "Available at": item.available_at,
                "Liquidity scope": "" if item.liquidity_scope is None else item.liquidity_scope.value,
            }
        )
    return pd.DataFrame(rows)


def cluster_stability_values(
    replay: TargetingHistoricalReplay,
    *,
    point_index: int,
    cluster: TargetCluster,
) -> dict[str, object]:
    stability = cluster_stability(replay, point_index=point_index, cluster=cluster)
    return {
        "First seen": stability.first_seen_at,
        "Last seen": stability.last_seen_at,
        "Consecutive snapshots": stability.consecutive_snapshots,
        "Age reference bars": stability.age_reference_bars,
    }


__all__ = [
    "cluster_anatomy_frame",
    "cluster_stability_values",
    "distance_bands_frame",
    "replay_points_frame",
    "semantic_transitions_frame",
]
