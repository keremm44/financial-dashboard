from __future__ import annotations

import pandas as pd

from financial_dashboard.targeting.models import TargetCluster
from financial_dashboard.targeting.semantic_models import ArrivalContext, SemanticTargetingSnapshot
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
        semantic = point.semantic_snapshot
        rows.append(
            {
                "Point": index,
                "Available at": point.available_at,
                "Price": snapshot.current_price,
                "ATR": snapshot.reference_atr,
                "Legacy clusters": len(snapshot.clusters),
                "Objectives": None if semantic is None else len(semantic.objectives),
                "Reaction zones": None if semantic is None else len(semantic.reaction_zones),
                "Confirmations": None if semantic is None else len(semantic.confirmations),
                "Arrival state": "" if semantic is None else semantic.state.value,
                "Nearest up ATR": None if snapshot.nearest_upside_target is None else snapshot.nearest_upside_target.distance_atr,
                "Nearest down ATR": None if snapshot.nearest_downside_target is None else snapshot.nearest_downside_target.distance_atr,
            }
        )
    return pd.DataFrame(rows)


def semantic_targeting_summary_values(snapshot: SemanticTargetingSnapshot) -> dict[str, object]:
    def objective_text(objective) -> str:
        if objective is None:
            return "—"
        distance = 0.0
        if objective.side.value == "ABOVE":
            distance = max(0.0, objective.low - snapshot.current_price)
        elif objective.side.value == "BELOW":
            distance = max(0.0, snapshot.current_price - objective.high)
        distance_atr = distance / max(snapshot.reference_atr, 1e-12)
        scope = "" if objective.liquidity_scope is None else f" · {objective.liquidity_scope.value}"
        return f"{objective.anchor_price:.4f} · {distance_atr:.2f} ATR{scope}"

    return {
        "Semantic state": snapshot.state.value,
        "Nearest upside objective": objective_text(snapshot.nearest_upside_objective),
        "Nearest downside objective": objective_text(snapshot.nearest_downside_objective),
        "Reaction zones": len(snapshot.reaction_zones),
        "Confirmations": len(snapshot.confirmations),
    }


def objectives_frame(snapshot: SemanticTargetingSnapshot) -> pd.DataFrame:
    rows = []
    for objective in snapshot.objectives:
        source = objective.source
        rows.append(
            {
                "Objective": objective.identity,
                "Kind": objective.kind.value,
                "Side": objective.side.value,
                "Low": objective.low,
                "High": objective.high,
                "Anchor": objective.anchor_price,
                "Scope": "" if objective.liquidity_scope is None else objective.liquidity_scope.value,
                "TF": source.timeframe,
                "State": source.source_state,
                "Origin event": source.origin_event_id,
                "Origin time": source.origin_time,
                "Confirmed at": source.confirmed_at,
                "Available at": source.available_at,
            }
        )
    return pd.DataFrame(rows)


def reaction_zones_frame(snapshot: SemanticTargetingSnapshot) -> pd.DataFrame:
    rows = []
    for zone in snapshot.reaction_zones:
        source = zone.source
        rows.append(
            {
                "Reaction": zone.identity,
                "Kind": zone.kind.value,
                "Side": zone.side.value,
                "Low": zone.low,
                "High": zone.high,
                "Roles": ", ".join(role.value for role in zone.roles),
                "TF": source.timeframe,
                "State": source.source_state,
                "Origin event": source.origin_event_id,
                "Origin time": source.origin_time,
                "Confirmed at": source.confirmed_at,
                "Available at": source.available_at,
            }
        )
    return pd.DataFrame(rows)


def confirmations_frame(snapshot: SemanticTargetingSnapshot) -> pd.DataFrame:
    rows = []
    for confirmation in snapshot.confirmations:
        source = confirmation.source
        rows.append(
            {
                "Confirmation": confirmation.identity,
                "Kind": confirmation.kind.value,
                "Side": confirmation.side.value,
                "Low": confirmation.low,
                "High": confirmation.high,
                "TF": source.timeframe,
                "State": source.source_state,
                "Origin event": source.origin_event_id,
                "Confirmed at": source.confirmed_at,
                "Available at": source.available_at,
            }
        )
    return pd.DataFrame(rows)


def arrival_context_frame(context: ArrivalContext | None) -> pd.DataFrame:
    if context is None:
        return pd.DataFrame()
    rows = []
    for label, members in (
        ("CURRENT", context.current_reactions),
        ("AHEAD", context.reactions_ahead),
        ("AT_OBJECTIVE", context.reactions_at),
        ("BEYOND", context.reactions_beyond),
    ):
        for item in members:
            zone = item.zone
            rows.append(
                {
                    "Position": label,
                    "Kind": zone.kind.value,
                    "Low": zone.low,
                    "High": zone.high,
                    "TF": zone.source.timeframe,
                    "State": zone.source.source_state,
                    "Independent from objective": item.independent_from_objective,
                    "Origin event": zone.source.origin_event_id,
                }
            )
    return pd.DataFrame(rows)


def shadow_comparison_frame(replay: TargetingHistoricalReplay) -> pd.DataFrame:
    rows = []
    for point in replay.points:
        legacy = point.snapshot
        semantic = point.semantic_snapshot
        if semantic is None:
            continue
        rows.append(
            {
                "Available at": point.available_at,
                "Legacy nearest up": None if legacy.nearest_upside_target is None else legacy.nearest_upside_target.liquidity_anchor,
                "Semantic nearest up": None if semantic.nearest_upside_objective is None else semantic.nearest_upside_objective.anchor_price,
                "Legacy nearest down": None if legacy.nearest_downside_target is None else legacy.nearest_downside_target.liquidity_anchor,
                "Semantic nearest down": None if semantic.nearest_downside_objective is None else semantic.nearest_downside_objective.anchor_price,
                "Legacy technical zones": sum(cluster.kind.value == "TECHNICAL_ZONE" for cluster in legacy.clusters),
                "Semantic reaction zones": len(semantic.reaction_zones),
                "Semantic confirmations": len(semantic.confirmations),
                "Arrival state": semantic.state.value,
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
    "arrival_context_frame",
    "cluster_anatomy_frame",
    "cluster_stability_values",
    "confirmations_frame",
    "distance_bands_frame",
    "objectives_frame",
    "reaction_zones_frame",
    "replay_points_frame",
    "semantic_targeting_summary_values",
    "semantic_transitions_frame",
    "shadow_comparison_frame",
]
