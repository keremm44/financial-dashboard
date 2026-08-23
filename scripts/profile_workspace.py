from __future__ import annotations

import argparse
import cProfile
import io
import pstats
from pathlib import Path

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.market_workspace import replay_market_workspace_from_cache


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile one full MarketAnalysisWorkspace replay.")
    parser.add_argument("--symbol", default="ASELS")
    parser.add_argument("--cache-root", default=str(Path(".cache") / "live-smoke-15m"))
    parser.add_argument("--profile", default="Dengeli")
    parser.add_argument("--top", type=int, default=40)
    args = parser.parse_args()

    profiler = cProfile.Profile()
    profiler.enable()
    workspace = replay_market_workspace_from_cache(
        args.cache_root,
        symbol=args.symbol,
        timeframes=ANALYSIS_TIMEFRAMES,
        pattern_profile=args.profile,
    )
    profiler.disable()

    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats("cumtime")
    stats.print_stats(max(1, args.top))
    print(stream.getvalue())
    print(
        f"workspace={workspace.symbol} timeframes={','.join(workspace.timeframes)} "
        f"cross_domain={workspace.cross_domain.status.value}"
    )


if __name__ == "__main__":
    main()
