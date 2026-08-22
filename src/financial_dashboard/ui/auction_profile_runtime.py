from __future__ import annotations

from financial_dashboard.auction_profile_replay import (
    AuctionProfileHistoricalReplay,
    replay_auction_profile_history_from_cache,
)


def replay_cached_auction_profile_history(
    cache_root: str,
    *,
    symbol: str,
    timeframe: str,
    minimum_bars: int = 1,
    step: int = 1,
    max_points: int | None = 100,
) -> AuctionProfileHistoricalReplay:
    return replay_auction_profile_history_from_cache(
        cache_root,
        symbol=symbol,
        timeframe=timeframe,
        minimum_bars=minimum_bars,
        step=step,
        max_points=max_points,
    )


__all__ = ["replay_cached_auction_profile_history"]
