from __future__ import annotations

import pandas as pd

from financial_dashboard.auction_profile_replay import AuctionProfileHistoricalReplay
from financial_dashboard.engines.auction_estimated_profile import EstimatedAuctionSnapshot


def auction_profile_summary_values(snapshot: EstimatedAuctionSnapshot) -> dict[str, str]:
    def fmt(value: float | None, digits: int = 2) -> str:
        return "—" if value is None else f"{value:.{digits}f}"

    return {
        "Source": snapshot.provenance.source.value,
        "Quality": snapshot.data_quality.value,
        "POC": fmt(snapshot.poc),
        "VAH": fmt(snapshot.vah),
        "VAL": fmt(snapshot.val),
        "Reaction": snapshot.export.reaction_state or "—",
        "Migration": snapshot.export.migration_state or "—",
        "Balance": snapshot.export.balance_state or "—",
        "Bars used": str(snapshot.provenance.bars_used),
        "History %": fmt(snapshot.provenance.history_fraction * 100.0, 1),
        "Allocation error %": fmt(snapshot.provenance.allocation_error_pct, 6),
        "VA coverage %": fmt(snapshot.provenance.value_area_coverage_pct, 2),
    }


def auction_profile_replay_frame(replay: AuctionProfileHistoricalReplay) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "As of": point.as_of,
            "Close": point.close,
            "Source": point.snapshot.provenance.source.value,
            "Quality": point.snapshot.data_quality.value,
            "POC": point.snapshot.poc,
            "VAH": point.snapshot.vah,
            "VAL": point.snapshot.val,
            "Reaction": point.snapshot.export.reaction_state,
            "Migration": point.snapshot.export.migration_state,
            "Balance": point.snapshot.export.balance_state,
            "Bars used": point.snapshot.provenance.bars_used,
            "Allocation error %": point.snapshot.provenance.allocation_error_pct,
        }
        for point in replay.points
    ])


def auction_profile_nodes_frame(snapshot: EstimatedAuctionSnapshot) -> pd.DataFrame:
    rows = []
    for node in (*snapshot.export.hvn_nodes, *snapshot.export.lvn_nodes):
        rows.append({
            "Kind": node.kind,
            "Center": node.center_price,
            "Low": node.low_price,
            "High": node.high_price,
            "Score": node.score,
            "Volume ratio": node.volume_ratio,
            "Mean ratio": node.mean_ratio,
            "Local depth": node.local_depth,
            "Inside value area": node.inside_value_area,
        })
    return pd.DataFrame(rows)


def auction_profile_provenance_frame(snapshot: EstimatedAuctionSnapshot) -> pd.DataFrame:
    p = snapshot.provenance
    return pd.DataFrame({
        "Field": [
            "profile_source",
            "method",
            "true_price_at_volume",
            "tick_profile",
            "footprint",
            "bars_used",
            "expected_lookback_bars",
            "history_fraction",
            "source_volume",
            "allocation_error_pct",
            "value_area_coverage_pct",
        ],
        "Value": [
            p.source.value,
            p.method,
            p.is_true_price_at_volume,
            p.is_tick_profile,
            p.is_footprint,
            p.bars_used,
            p.expected_lookback_bars,
            p.history_fraction,
            p.source_volume,
            p.allocation_error_pct,
            p.value_area_coverage_pct,
        ],
    })


__all__ = [
    "auction_profile_nodes_frame",
    "auction_profile_provenance_frame",
    "auction_profile_replay_frame",
    "auction_profile_summary_values",
]
