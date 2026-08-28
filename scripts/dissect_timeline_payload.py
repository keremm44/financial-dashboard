"""Payload dissection for a frozen DecisionInput timeline cache.

Loads a (small) frozen timeline and measures, per DecisionInputSnapshot field,
the pickled byte size and object counts, then reports the aggregate composition.
Used to design a decision-superset-safe snapshot trimming (docs, BÖLÜM 13).

Usage:
    python scripts/dissect_timeline_payload.py <cache_root> <symbol>
"""

from __future__ import annotations

import pickle
import sys
from collections import Counter
from dataclasses import fields, is_dataclass
from pathlib import Path


def _size(obj) -> int:
    return len(pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL))


def _walk(obj, depth: int = 0, max_depth: int = 3):
    """Yield (path, size) for dataclass children up to max_depth."""
    if depth >= max_depth:
        return
    if is_dataclass(obj) and not isinstance(obj, type):
        for f in fields(obj):
            try:
                child = getattr(obj, f.name)
            except Exception:
                continue
            yield f"{type(obj).__name__}.{f.name}", child
            yield from _walk(child, depth + 1, max_depth)


def main() -> int:
    root = Path(sys.argv[1])
    symbol = sys.argv[2].upper()

    from financial_dashboard.data.parquet_store import ParquetOHLCVStore
    from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
    from financial_dashboard.decision.timeline_cache import load_frozen_decision_timeline

    store = ParquetOHLCVStore(root)
    from financial_dashboard.decision.history_replay import HistoricalDecisionInputReplayRunner
    import importlib.util

    _spec = importlib.util.spec_from_file_location(
        "diag", Path(__file__).with_name("diagnose_timeline_cache.py")
    )
    _diag = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_diag)
    _causal_warmup_start = _diag._causal_warmup_start

    config = HistoricalDecisionInputConfig(
        start_at=_causal_warmup_start(store, symbol, None)
    )
    load = load_frozen_decision_timeline(store, symbol, config=config)
    replay = load.replay
    snapshots = list(replay.snapshots)
    print(f"SNAPSHOTS\t{len(snapshots)}")

    field_bytes: Counter[str] = Counter()
    field_counts: Counter[str] = Counter()
    walk_bytes: Counter[str] = Counter()
    walk_counts: Counter[str] = Counter()
    row_counts: Counter[str] = Counter()

    from financial_dashboard.decision_input import DecisionInputSnapshot

    for snap in snapshots:
        total = _size(snap)
        field_bytes["__TOTAL__"] += total
        accounted = 0
        for f in fields(DecisionInputSnapshot):
            value = getattr(snap, f.name)
            size = _size(value)
            field_bytes[f.name] += size
            accounted += size
            field_counts[f.name] += 1
        field_bytes["__OVERHEAD__"] += max(total - accounted, 0)

        for path, child in _walk(snap, 0, 2):
            try:
                walk_bytes[path] += _size(child)
                walk_counts[path] += 1
            except Exception:
                continue

        # row counts for the obvious collection payloads
        ob = snap.order_block_behavior
        if ob is not None:
            row_counts["ob_behavior.observations"] += len(ob.observations)
        fvg = snap.fvg_engulfing_lifecycle
        if fvg is not None:
            row_counts["fvg.rows"] += len(fvg.fvg)
            row_counts["engulfing.rows"] += len(fvg.engulfing)
        zones = snap.qualified_zones
        if zones is not None:
            row_counts["qualified_zones.zones"] += len(getattr(zones, "zones", ()) or ())
        sr = snap.support_resistance
        if sr is not None:
            for attr in ("rows", "levels", "observations"):
                rows = getattr(sr, attr, None)
                if isinstance(rows, (list, tuple)):
                    row_counts[f"sr.{attr}"] += len(rows)
        liq = snap.liquidity
        if liq is not None:
            for attr in ("rows", "pools", "observations", "sweeps"):
                rows = getattr(liq, attr, None)
                if isinstance(rows, (list, tuple)):
                    row_counts[f"liquidity.{attr}"] += len(rows)
        ll = snap.liquidity_landscape
        if ll is not None:
            for attr in ("rows", "levels", "observations"):
                rows = getattr(ll, attr, None)
                if isinstance(rows, (list, tuple)):
                    row_counts[f"liq_landscape.{attr}"] += len(rows)

    n = max(len(snapshots), 1)
    print("\nFIELD COMPOSITION (pickle bytes, aggregate over snapshots)")
    for name, total in field_bytes.most_common():
        share = 100.0 * total / field_bytes["__TOTAL__"]
        print(f"{name:32s} {total/1e6:10.2f} MB {share:6.2f}%  avg/snap={total/n/1e3:9.2f} KB")

    print("\nTOP SUB-STRUCTURES (depth<=2)")
    for name, total in walk_bytes.most_common(25):
        print(f"{name:48s} {total/1e6:10.2f} MB")

    print("\nROW COUNTS (total / avg per snapshot)")
    for name, count in row_counts.most_common():
        print(f"{name:32s} {count:10d} {count/n:9.1f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
