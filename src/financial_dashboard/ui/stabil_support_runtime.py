from __future__ import annotations

from pathlib import Path
from typing import Callable

from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.stabil_support_replay import (
    StabilSupportHistoricalReplay,
    replay_stabil_support_history_from_cache,
)


def replay_cached_stabil_support_history(
    cache_root: str | Path,
    *,
    symbol: str,
    minimum_bars: int = 1,
    step: int = 1,
    max_points: int | None = 100,
    progress: Callable[[int, int, object], None] | None = None,
) -> StabilSupportHistoricalReplay:
    return replay_stabil_support_history_from_cache(
        cache_root,
        symbol=normalize_symbol(symbol),
        minimum_bars=minimum_bars,
        step=step,
        max_points=max_points,
        progress=progress,
    )


__all__ = ["replay_cached_stabil_support_history"]
