from __future__ import annotations

from financial_dashboard.auction_profile_replay import AuctionProfileHistoricalReplayRunner
from financial_dashboard.ui.auction_profile_view_models import (
    auction_profile_nodes_frame,
    auction_profile_provenance_frame,
    auction_profile_replay_frame,
    auction_profile_summary_values,
)
from _ui_test_data import make_ui_store


def test_auction_view_models_are_descriptive_and_expose_estimated_boundary(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    replay = AuctionProfileHistoricalReplayRunner(store).replay(
        "THYAO", timeframe="1h", minimum_bars=20, step=2, max_points=25
    )
    latest = replay.latest
    assert latest is not None
    summary = auction_profile_summary_values(latest)
    assert summary["Source"] == "OHLCV_ESTIMATED"
    forbidden = " ".join(summary.values()).upper()
    assert "BUY" not in forbidden
    assert "SELL" not in forbidden
    assert "STOP" not in forbidden
    assert "TAKE PROFIT" not in forbidden

    timeline = auction_profile_replay_frame(replay)
    assert not timeline.empty
    assert {"As of", "POC", "VAH", "VAL", "Source", "Migration", "Balance"}.issubset(timeline.columns)

    provenance = auction_profile_provenance_frame(latest)
    values = dict(zip(provenance["Field"], provenance["Value"], strict=True))
    assert values["profile_source"] == "OHLCV_ESTIMATED"
    assert values["true_price_at_volume"] is False
    assert values["tick_profile"] is False
    assert values["footprint"] is False

    nodes = auction_profile_nodes_frame(latest)
    if not nodes.empty:
        assert {"Kind", "Center", "Low", "High", "Score"}.issubset(nodes.columns)
