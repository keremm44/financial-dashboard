from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.timeline_cache import load_frozen_decision_timeline
from financial_dashboard.decision.stabil_authority import assess_stabil_authority


def _token(value) -> str:
    return str(getattr(value, "value", value))


def _near_date(ts, target: pd.Timestamp, days: int = 1) -> bool:
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is None and target.tzinfo is not None:
        stamp = stamp.tz_localize(target.tzinfo)
    elif stamp.tzinfo is not None and target.tzinfo is None:
        target = target.tz_localize(stamp.tzinfo)
    return abs((stamp.normalize() - target.normalize()).days) <= days


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect Stabil data already frozen inside DecisionInput timeline. No domain replay.")
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--dates", nargs="+", default=("2026-07-02", "2026-08-06", "2026-08-11"))
    args = parser.parse_args()

    store = ParquetOHLCVStore(args.cache_root)
    symbol = normalize_symbol(args.symbol)
    frozen = load_frozen_decision_timeline(store, symbol, config=HistoricalDecisionInputConfig())
    snapshots = tuple(frozen.replay.snapshots)

    print("FROZEN_STABIL_DECISION_CACHE_AUDIT")
    print("==================================")
    print(f"CACHE\t{frozen.cache_status}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print("DOMAIN_REPLAY\tNOT_RUN")

    targets = tuple(pd.Timestamp(item) for item in args.dates)
    for target in targets:
        print()
        print(f"TARGET\t{target.date()}")
        found = False
        for snapshot in snapshots:
            if not _near_date(snapshot.as_of, target, days=1):
                continue
            found = True
            stabil = getattr(snapshot, "stabil_support", None)
            behavior = None if stabil is None else getattr(stabil, "behavior", None)
            authority = assess_stabil_authority(stabil)
            print(
                f"{snapshot.as_of} price={snapshot.current_price:.2f} | "
                f"stabil={'NONE' if stabil is None else 'PRESENT'} "
                f"behavior={'NONE' if behavior is None else 'PRESENT'} "
                f"validity={'-' if stabil is None else getattr(stabil, 'validity', '-')} "
                f"motion={'-' if behavior is None else getattr(behavior, 'motion', '-')} "
                f"relation={'-' if behavior is None else getattr(behavior, 'relation', '-')} "
                f"interaction={'-' if behavior is None else getattr(behavior, 'interaction', '-')} "
                f"decision_stabil={_token(authority.state)}"
            )
        if not found:
            print("- no snapshot in +/-1 day window")

    print()
    print("FROZEN_STABIL_DECISION_CACHE_AUDIT_OK")


if __name__ == "__main__":
    main()
