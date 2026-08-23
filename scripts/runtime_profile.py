from __future__ import annotations

import argparse
from pathlib import Path

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.runtime_profile import profile_market_workspace_from_cache


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile real financial-dashboard workspace runtime")
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--profile", default="Dengeli")
    parser.add_argument("--timeframes", nargs="*", default=list(ANALYSIS_TIMEFRAMES))
    args = parser.parse_args()

    result = profile_market_workspace_from_cache(
        args.cache_root,
        symbol=args.symbol,
        timeframes=tuple(args.timeframes),
        pattern_profile=args.profile,
    )

    print(f"TOTAL\t{result.total_seconds:.4f}s")
    for item in result.stages:
        print(f"{item.stage}\t{item.seconds:.4f}s\t{item.calls} call(s)")
    print("RUNTIME_PROFILE_OK")


if __name__ == "__main__":
    main()
