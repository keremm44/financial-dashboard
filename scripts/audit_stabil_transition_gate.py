from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.engine import DecisionEngineConfig, assess_horizon_decision
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.structural import DecisionHorizon
from financial_dashboard.decision.timeline_cache import load_frozen_decision_timeline


def _token(value) -> str:
    return str(getattr(value, "value", value))


def _same_date(value, target: pd.Timestamp) -> bool:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_localize(None)
    return stamp.date() == target.date()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect every ST early-transition gate on selected frozen DecisionInput dates. No domain replay."
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--dates", nargs="+", default=("2026-07-02", "2026-08-06", "2026-08-11"))
    args = parser.parse_args()

    store = ParquetOHLCVStore(args.cache_root)
    symbol = normalize_symbol(args.symbol)
    frozen = load_frozen_decision_timeline(store, symbol, config=HistoricalDecisionInputConfig())
    snapshots = tuple(frozen.replay.snapshots)
    config = DecisionEngineConfig()

    print("STABIL_TRANSITION_GATE_AUDIT")
    print("============================")
    print(f"CACHE\t{frozen.cache_status}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print("DOMAIN_REPLAY\tNOT_RUN")

    for raw_date in args.dates:
        target = pd.Timestamp(raw_date)
        print()
        print(f"TARGET\t{target.date()}")
        found = False
        for snapshot in snapshots:
            if not _same_date(snapshot.as_of, target):
                continue
            found = True
            assessment = assess_horizon_decision(
                snapshot,
                DecisionHorizon.SHORT_TERM,
                config=config,
                execution_event=None,
            )
            transition = assessment.st_transition
            native = assessment.structural_snapshot.short_term
            if transition is None:
                print(
                    f"{snapshot.as_of} price={snapshot.current_price:.2f} | "
                    f"native={_token(native.direction)}/{_token(native.thesis_state)} "
                    "transition=NONE"
                )
                continue
            print(
                f"{snapshot.as_of} price={snapshot.current_price:.2f} | "
                f"native={_token(native.direction)}/{_token(native.thesis_state)} "
                f"stabil={_token(transition.stabil.state)} "
                f"state={_token(transition.state)} "
                f"choch={'YES' if transition.current_bullish_choch else 'NO'} "
                f"reaction={'CONFIRMED' if transition.reaction.confirmation_present else ('DEVELOPING' if transition.reaction.developing_present else 'NO')} "
                f"timing={_token(transition.timing.state)} "
                f"opportunity={_token(transition.opportunity.state)} "
                f"conflict={_token(transition.conflict.state)} "
                f"own={'YES' if transition.can_own_trade_thesis else 'NO'}"
            )
            if transition.blockers:
                print("  blockers=" + ",".join(transition.blockers))
            if transition.reasons:
                print("  reasons=" + ",".join(transition.reasons))
        if not found:
            print("- no snapshot")

    print()
    print("STABIL_TRANSITION_GATE_AUDIT_OK")


if __name__ == "__main__":
    main()
