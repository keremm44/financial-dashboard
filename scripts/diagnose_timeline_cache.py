from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.analysis_inputs import cache_fingerprint
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.history_replay import HistoricalDecisionInputReplayRunner
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.persistent_state import PersistentObjectStore
from financial_dashboard.decision.timeline_build import decision_prefix_exists
from financial_dashboard.structure_location_replay import CausalBarClock

DECISION_TIMEFRAME = "1h"


def _align_requested_start(value: str, reference: pd.Timestamp) -> pd.Timestamp:
    requested = pd.Timestamp(value)
    if reference.tzinfo is not None and requested.tzinfo is None:
        requested = requested.tz_localize(reference.tzinfo)
    elif reference.tzinfo is None and requested.tzinfo is not None:
        requested = requested.tz_localize(None)
    elif reference.tzinfo is not None and requested.tzinfo is not None:
        requested = requested.tz_convert(reference.tzinfo)
    return requested


def _causal_warmup_start(store, clean_symbol: str, requested_start: str | None) -> pd.Timestamp:
    clock = CausalBarClock()
    first_available = []
    for timeframe in ANALYSIS_TIMEFRAMES:
        frame = store.load(clean_symbol, timeframe)
        if frame.empty:
            raise SystemExit(f"No historical bars found for {clean_symbol} {timeframe}")
        first_available.append(
            pd.Timestamp(clock.available_at(pd.Timestamp(frame.iloc[0]["timestamp"]), timeframe))
        )
    common_cutoff = max(first_available)
    decision_frame = store.load(clean_symbol, DECISION_TIMEFRAME)
    warmup_start = next(
        pd.Timestamp(value)
        for value in decision_frame["timestamp"]
        if pd.Timestamp(clock.available_at(pd.Timestamp(value), DECISION_TIMEFRAME)) >= common_cutoff
    )
    if requested_start is None:
        return warmup_start
    return max(warmup_start, _align_requested_start(requested_start, warmup_start))


def _describe_row(row) -> str:
    timeframe, size, mtime_ns = row
    try:
        mtime = pd.Timestamp(int(mtime_ns), unit="ns").strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError):
        mtime = str(mtime_ns)
    return f"{timeframe}: size={size} mtime={mtime}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Explain an exact DecisionInput timeline cache HIT/MISS: compare the "
            "current cache identity against every stored sidecar, per timeframe."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
    args = parser.parse_args()

    store = ParquetOHLCVStore(args.cache_root)
    clean_symbol = normalize_symbol(args.symbol)
    effective_start = _causal_warmup_start(store, clean_symbol, args.start)
    config = HistoricalDecisionInputConfig(
        pattern_profile=args.pattern_profile,
        max_bars=args.max_bars,
        start_at=effective_start,
        end_at=args.end,
    )

    runner = HistoricalDecisionInputReplayRunner(store)
    identity = runner._cache_identity(symbol=clean_symbol, config=config)
    persistent = PersistentObjectStore(store.root)
    symbol_dir = persistent._symbol_directory(clean_symbol)
    sidecars = []
    for sidecar_path in sorted(symbol_dir.glob("*.identity.json")):
        try:
            sidecars.append(json.loads(sidecar_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue

    print("=" * 72)
    print("DECISION TIMELINE CACHE DIAGNOSTIC")
    print("=" * 72)
    print(f"SYMBOL\t{clean_symbol}")
    print(f"WARMUP_START\t{effective_start}")
    print(
        f"ARGS\tstart={args.start} end={args.end} max_bars={args.max_bars} "
        f"pattern_profile={args.pattern_profile}"
    )
    print(f"CURRENT_CONFIG_FINGERPRINT\t{identity.config_fingerprint}")
    print(f"CURRENT_DIGEST\t{identity.digest}")
    print("CURRENT_SOURCE_FINGERPRINT (live parquet state)")
    for row in cache_fingerprint(store, symbol=clean_symbol, timeframes=ANALYSIS_TIMEFRAMES):
        print(f"  {_describe_row(row)}")

    exact_path = persistent.path_for(identity)
    print(f"\nEXACT_CACHE_FILE\t{exact_path.name}")
    print(f"EXACT_CACHE_EXISTS\t{'YES' if exact_path.exists() else 'NO'}")
    print(
        "DECISION_APPEND_PREFIX\t"
        + ("EXISTS" if decision_prefix_exists(store, symbol=clean_symbol, config=config) else "MISSING")
    )

    print(f"\nSTORED_SIDEARS\t{len(sidecars)}")
    for record in sidecars:
        print("-" * 60)
        print(f"  DIGEST\t{record.get('digest')}")
        print(f"  NAMESPACE\t{record.get('namespace')}")
        print(f"  CONFIG\t{record.get('config_fingerprint')}")
        stored_source = tuple(tuple(row) for row in record.get("source_fingerprint", ()))
        matches_current = record.get("digest") == identity.digest
        print(f"  MATCHES_CURRENT\t{'YES' if matches_current else 'NO'}")
        if (
            not matches_current
            and record.get("config_fingerprint") == identity.config_fingerprint
            and record.get("namespace") == identity.namespace
        ):
            current_source = dict(
                (row[0], row) for row in cache_fingerprint(store, symbol=clean_symbol, timeframes=ANALYSIS_TIMEFRAMES)
            )
            print("  SOURCE_DIFF_VS_CURRENT")
            for stored_row in stored_source:
                timeframe = stored_row[0]
                current_row = current_source.get(timeframe)
                if current_row is None or tuple(current_row) != tuple(stored_row):
                    print(f"    stored   {_describe_row(stored_row)}")
                    if current_row is not None:
                        print(f"    current  {_describe_row(current_row)}")
                else:
                    print(f"    same     {_describe_row(stored_row)}")

    print("=" * 72)
    if exact_path.exists():
        print("VERDICT\tEXACT_HIT_EXPECTED (identity file exists)")
    else:
        same_config = [
            record
            for record in sidecars
            if record.get("config_fingerprint") == identity.config_fingerprint
            and record.get("namespace") == identity.namespace
        ]
        if sidecars and not same_config:
            print("VERDICT\tMISS_CAUSE_CONFIG (cache exists for DIFFERENT config args; "
                  "check --start/--end/--max-bars/--pattern-profile)")
        elif same_config:
            print("VERDICT\tMISS_CAUSE_SOURCE_DATA (parquet size/mtime changed since the "
                  "cached build — see SOURCE_DIFF_VS_CURRENT above)")
        else:
            print("VERDICT\tMISS_CAUSE_NO_CACHE (no stored timeline for this symbol)")
    print("DIAGNOSTIC_OK")


if __name__ == "__main__":
    main()
