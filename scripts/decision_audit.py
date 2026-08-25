from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision_audit import (
    DecisionAction,
    DecisionAuditConfig,
    DecisionEvent,
    DecisionSide,
    audit_decisions,
    render_json,
    render_text,
)


def _load_bars(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(path)
    raise ValueError(f"unsupported bars file type: {path.suffix}")


def _tuple_strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _load_decisions(path: Path) -> tuple[DecisionEvent, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("decisions", ()) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("decision JSON must be a list or an object containing a decisions list")

    events: list[DecisionEvent] = []
    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"decision row {position} must be an object")
        try:
            action = DecisionAction(str(row["action"]).upper())
            side = DecisionSide(str(row.get("side", "NONE")).upper())
            timestamp = row["timestamp"]
        except (KeyError, ValueError) as exc:
            raise ValueError(f"invalid decision row {position}: {row}") from exc
        events.append(
            DecisionEvent(
                timestamp=timestamp,
                action=action,
                side=side,
                price=None if row.get("price") is None else float(row["price"]),
                atr=None if row.get("atr") is None else float(row["atr"]),
                reasons=_tuple_strings(row.get("reasons")),
                blockers=_tuple_strings(row.get("blockers")),
                waiting_for=_tuple_strings(row.get("waiting_for")),
                source_lineage=_tuple_strings(row.get("source_lineage")),
                snapshot=row.get("snapshot") or {},
            )
        )
    return tuple(events)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Audit a causal BUY/SELL decision stream against historical OHLCV without "
            "feeding hindsight information back into the decision engine"
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("decisions", type=Path, help="JSON file containing causal decision events")
    parser.add_argument("--timeframe", default="30m", help="OHLCV timeframe used to grade execution quality")
    parser.add_argument("--bars", type=Path, default=None, help="Optional CSV/parquet bars file instead of cache")
    parser.add_argument("--lookback-bars", type=int, default=10)
    parser.add_argument("--lookahead-bars", type=int, default=10)
    parser.add_argument("--meaningful-move-atr", type=float, default=None)
    parser.add_argument("--opportunity-horizon-bars", type=int, default=20)
    parser.add_argument("--swing-radius-bars", type=int, default=3)
    parser.add_argument("--capture-entry-window-bars", type=int, default=5)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--worst-trades", type=int, default=5)
    args = parser.parse_args()

    if args.bars is not None:
        bars = _load_bars(args.bars)
    else:
        bars = ParquetOHLCVStore(args.cache_root).load(args.symbol, args.timeframe)
    if bars.empty:
        raise SystemExit(f"No bars found for {args.symbol} {args.timeframe}")

    decisions = _load_decisions(args.decisions)
    config = DecisionAuditConfig(
        extrema_lookback_bars=args.lookback_bars,
        extrema_lookahead_bars=args.lookahead_bars,
        opportunity_horizon_bars=args.opportunity_horizon_bars,
        swing_radius_bars=args.swing_radius_bars,
        meaningful_move_atr=args.meaningful_move_atr,
        capture_entry_window_bars=args.capture_entry_window_bars,
    )
    report = audit_decisions(
        symbol=args.symbol,
        timeframe=args.timeframe,
        bars=bars,
        decisions=decisions,
        config=config,
    )
    print(render_text(report, worst_trade_limit=max(1, args.worst_trades)))

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(render_json(report), encoding="utf-8")
        print(f"JSON_REPORT\t{args.json_out}")
    print("DECISION_AUDIT_OK")


if __name__ == "__main__":
    main()
