from __future__ import annotations

import pandas as pd

from financial_dashboard.targeting.models import TargetingSnapshot


def targeting_summary_values(snapshot: TargetingSnapshot) -> dict[str, str]:
    def fmt(cluster) -> str:
        if cluster is None:
            return "—"
        anchor = cluster.liquidity_anchor
        price = anchor if anchor is not None else (
            cluster.envelope_low if cluster.side.value == "ABOVE" else cluster.envelope_high
        )
        return f"{price:.4f} · {cluster.distance_atr:.2f} ATR · {cluster.quality.value}"

    return {
        "Nearest upside": fmt(snapshot.nearest_upside_target),
        "Nearest downside": fmt(snapshot.nearest_downside_target),
        "Highest confluence upside": fmt(snapshot.highest_confluence_upside),
        "Highest confluence downside": fmt(snapshot.highest_confluence_downside),
    }


def target_clusters_frame(snapshot: TargetingSnapshot) -> pd.DataFrame:
    rows = []
    for cluster in snapshot.clusters:
        rows.append(
            {
                "Cluster": cluster.identity,
                "Side": cluster.side.value,
                "Kind": cluster.kind.value,
                "Envelope low": cluster.envelope_low,
                "Envelope high": cluster.envelope_high,
                "Core low": cluster.core_low,
                "Core high": cluster.core_high,
                "Liquidity anchor": cluster.liquidity_anchor,
                "Distance %": cluster.distance_percent,
                "Distance ATR": cluster.distance_atr,
                "Raw sources": cluster.raw_source_count,
                "Independent origins": cluster.independent_origin_count,
                "Independent families": cluster.independent_family_count,
                "Quality": cluster.quality.value,
                "Timeframes": ", ".join(cluster.timeframes_present),
                "Roles": ", ".join(role.value for role in cluster.roles_present),
            }
        )
    return pd.DataFrame(rows)


def target_evidence_frame(snapshot: TargetingSnapshot) -> pd.DataFrame:
    rows = []
    seen: set[str] = set()
    for cluster in snapshot.clusters:
        for item in cluster.evidence:
            if item.uid in seen:
                continue
            seen.add(item.uid)
            rows.append(
                {
                    "UID": item.uid,
                    "Type": item.evidence_type.value,
                    "TF": item.timeframe,
                    "State": item.source_state,
                    "Low": item.low,
                    "High": item.high,
                    "Anchor": item.anchor_price,
                    "Origin": item.origin_time,
                    "Confirmed at": item.confirmed_at,
                    "Available at": item.available_at,
                    "Origin event": item.origin_event_id,
                    "Family": item.family.value,
                    "Roles": ", ".join(role.value for role in item.roles),
                    "Liquidity scope": "" if item.liquidity_scope is None else item.liquidity_scope.value,
                }
            )
    return pd.DataFrame(rows)


__all__ = ["target_clusters_frame", "target_evidence_frame", "targeting_summary_values"]
