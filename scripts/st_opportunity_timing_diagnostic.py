from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.calibration import load_opportunity_calibration
from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.engine import DecisionEngineConfig
from financial_dashboard.decision.execution_detect import detect_30m_execution_events
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.lifecycle_replay import replay_canonical_trade_lifecycle
from financial_dashboard.decision.scenario import ScenarioStage
from financial_dashboard.decision.timeline_cache import DecisionTimelineCacheMiss, load_frozen_decision_timeline
from financial_dashboard.structure_location_replay import CausalBarClock


@dataclass(frozen=True, slots=True)
class SnapshotEntryRow:
    as_of: pd.Timestamp
    bar_index: int
    action: str
    selected_horizon: str
    selected_stage: str
    eligibility: str
    timing: str
    opportunity: str
    event_same_bar: bool
    reasons: tuple[str, ...]
    waiting_for: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MeaningfulMove:
    start_index: int
    peak_index: int
    low: float
    peak: float
    move_atr: float
    move_pct: float


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _align_requested_start(value: str, reference: pd.Timestamp) -> pd.Timestamp:
    requested = pd.Timestamp(value)
    if reference.tzinfo is not None and requested.tzinfo is None:
        requested = requested.tz_localize(reference.tzinfo)
    elif reference.tzinfo is None and requested.tzinfo is not None:
        requested = requested.tz_localize(None)
    elif reference.tzinfo is not None and requested.tzinfo is not None:
        requested = requested.tz_convert(reference.tzinfo)
    return requested


def _causal_warmup_start(store: ParquetOHLCVStore, *, symbol: str, requested_start: str | None) -> pd.Timestamp:
    clock = CausalBarClock()
    first_available: list[pd.Timestamp] = []
    for timeframe in ANALYSIS_TIMEFRAMES:
        frame = store.load(symbol, timeframe)
        if frame.empty:
            raise SystemExit(f"No historical bars found for {symbol} {timeframe}")
        first_timestamp = pd.Timestamp(frame.iloc[0]["timestamp"])
        first_available.append(pd.Timestamp(clock.available_at(first_timestamp, timeframe)))
    common_cutoff = max(first_available)
    decision_frame = store.load(symbol, "1h")
    for value in decision_frame["timestamp"]:
        timestamp = pd.Timestamp(value)
        if pd.Timestamp(clock.available_at(timestamp, "1h")) >= common_cutoff:
            if requested_start is None:
                return timestamp
            return max(timestamp, _align_requested_start(requested_start, timestamp))
    raise SystemExit("No causally valid 1h decision bar found")


def _load_calibration(cache_root: Path, symbol: str):
    path = cache_root / "calibration" / "opportunity" / f"{normalize_symbol(symbol)}.json"
    if not path.exists():
        raise SystemExit(f"Missing opportunity calibration: {path}")
    return load_opportunity_calibration(path).calibration, path


def _prepare_bars(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "high", "low", "close"}
    missing = required.difference(frame.columns)
    if missing:
        raise SystemExit(f"30m bars missing columns: {sorted(missing)}")
    bars = frame.copy()
    bars["timestamp"] = pd.to_datetime(bars["timestamp"])
    bars = bars.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    prev_close = bars["close"].shift(1)
    tr = pd.concat(
        (
            bars["high"] - bars["low"],
            (bars["high"] - prev_close).abs(),
            (bars["low"] - prev_close).abs(),
        ),
        axis=1,
    ).max(axis=1)
    bars["_atr"] = tr.rolling(14, min_periods=1).mean()
    return bars


def _bars_per_trading_day(bars: pd.DataFrame) -> int:
    dates = bars["timestamp"].dt.date
    counts = dates.value_counts().tolist()
    if not counts:
        return 1
    return max(1, int(round(float(median(counts)))))


def _bar_index(bars: pd.DataFrame, timestamp: Any) -> int:
    target = pd.Timestamp(timestamp)
    position = int(bars["timestamp"].searchsorted(target, side="right")) - 1
    return max(0, min(position, len(bars) - 1))


def _meaningful_moves(
    bars: pd.DataFrame,
    *,
    horizon_bars: int,
    min_move_atr: float,
    swing_radius: int,
) -> tuple[MeaningfulMove, ...]:
    rows: list[MeaningfulMove] = []
    last_peak = -1
    for index in range(swing_radius, len(bars) - swing_radius):
        low = float(bars.at[index, "low"])
        local = bars.loc[index - swing_radius:index + swing_radius, "low"]
        if low != float(local.min()) or int(local.idxmin()) != index:
            continue
        end = min(len(bars) - 1, index + horizon_bars)
        if end <= index:
            continue
        future = bars.loc[index + 1:end, "high"]
        if future.empty:
            continue
        peak_index = int(future.idxmax())
        if peak_index <= last_peak:
            continue
        peak = float(bars.at[peak_index, "high"])
        atr = float(bars.at[index, "_atr"])
        if atr <= 0:
            continue
        move = peak - low
        move_atr = move / atr
        if move_atr < min_move_atr:
            continue
        rows.append(
            MeaningfulMove(
                start_index=index,
                peak_index=peak_index,
                low=low,
                peak=peak,
                move_atr=move_atr,
                move_pct=(peak / low - 1.0) * 100.0,
            )
        )
        last_peak = peak_index
    return tuple(rows)


def _entry_strength(row: SnapshotEntryRow) -> tuple[int, int, int]:
    action_rank = {
        "BUY": 5,
        "READY": 4,
        "WAIT": 2,
        "NO_TRADE": 1,
    }.get(row.action, 0)
    stage_rank = {
        "QUALIFIED": 4,
        "DEVELOPING": 3,
        "BLOCKED": 1,
        "UNAVAILABLE": 0,
        "NONE": 0,
    }.get(row.selected_stage, 0)
    return action_rank, stage_rank, 1 if row.event_same_bar else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Frozen-cache-only ST diagnostic: 3-9 trading-day meaningful moves, missed-entry gates, "
            "entry timing, and exit-click/readiness synchronization."
        )
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
    parser.add_argument("--min-move-atr", type=float, default=3.0)
    parser.add_argument("--capture-days", type=int, default=3)
    parser.add_argument("--horizon-days", type=int, default=9)
    parser.add_argument("--swing-radius-bars", type=int, default=3)
    args = parser.parse_args()

    if args.min_move_atr <= 0:
        raise SystemExit("--min-move-atr must be > 0")
    if args.capture_days < 1 or args.horizon_days < args.capture_days:
        raise SystemExit("Require horizon-days >= capture-days >= 1")

    clean_symbol = normalize_symbol(args.symbol)
    store = ParquetOHLCVStore(args.cache_root)
    effective_start = _causal_warmup_start(store, symbol=clean_symbol, requested_start=args.start)
    history_config = HistoricalDecisionInputConfig(
        pattern_profile=args.pattern_profile,
        max_bars=args.max_bars,
        start_at=effective_start,
        end_at=args.end,
    )
    try:
        frozen = load_frozen_decision_timeline(store, clean_symbol, config=history_config)
    except DecisionTimelineCacheMiss as exc:
        raise SystemExit("FROZEN_DECISION_TIMELINE_CACHE_MISS; domains were NOT replayed") from exc

    calibration, calibration_path = _load_calibration(args.cache_root, clean_symbol)
    cfg = DecisionEngineConfig(opportunity_calibration=calibration)
    snapshots = tuple(frozen.replay.snapshots)
    entry_events, exit_events = detect_30m_execution_events(snapshots)

    bars = _prepare_bars(store.load(clean_symbol, "30m"))
    bars_per_day = _bars_per_trading_day(bars)
    capture_bars = bars_per_day * args.capture_days
    horizon_bars = bars_per_day * args.horizon_days

    entry_rows: list[SnapshotEntryRow] = []
    for snapshot in snapshots:
        event = entry_events.get(snapshot.as_of)
        arbitration = snapshot.entry_arbitration(config=cfg)
        decision = snapshot.entry_decision(config=cfg, execution_event=event)
        selected = arbitration.selected_scenario
        selected_horizon = arbitration.selected_horizon
        if selected is None or selected_horizon is None:
            eligibility = "NONE"
            timing = "NONE"
            opportunity = "NONE"
            stage = "NONE"
        else:
            assessment = __import__(
                "financial_dashboard.decision.engine", fromlist=["assess_horizon_decision"]
            ).assess_horizon_decision(snapshot, selected_horizon, config=cfg, execution_event=event)
            eligibility = _value(assessment.eligibility.state)
            timing = _value(assessment.timing.state)
            opportunity = _value(assessment.opportunity.state)
            stage = _value(selected.stage)
        entry_rows.append(
            SnapshotEntryRow(
                as_of=pd.Timestamp(snapshot.as_of),
                bar_index=_bar_index(bars, snapshot.as_of),
                action=_value(decision.action),
                selected_horizon="NONE" if selected_horizon is None else _value(selected_horizon),
                selected_stage=stage,
                eligibility=eligibility,
                timing=timing,
                opportunity=opportunity,
                event_same_bar=event is not None,
                reasons=tuple(decision.reasons),
                waiting_for=tuple(decision.waiting_for),
            )
        )

    lifecycle = replay_canonical_trade_lifecycle(
        snapshots,
        config=cfg,
        entry_execution_events=entry_events,
        exit_execution_events=exit_events,
        readiness_execution_proxy=False,
    )
    buy_rows = [row for row in lifecycle.rows if row.action is DecisionAction.BUY]
    sell_rows = [row for row in lifecycle.rows if row.action is DecisionAction.SELL]
    buy_indices = [(_bar_index(bars, row.snapshot.as_of), row) for row in buy_rows]

    moves = _meaningful_moves(
        bars,
        horizon_bars=horizon_bars,
        min_move_atr=args.min_move_atr,
        swing_radius=args.swing_radius_bars,
    )
    gate_counter: Counter[str] = Counter()
    captured = 0

    print("=" * 96)
    print("SHORT-TERM OPPORTUNITY / TIMING DIAGNOSTIC")
    print("=" * 96)
    print(f"SYMBOL\t{clean_symbol}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print(f"FROZEN_CACHE_STATUS\t{frozen.cache_status}")
    print("DOMAIN_REPLAY_SECONDS\t0.000")
    print(f"OPPORTUNITY_CALIBRATION\t{calibration_path}")
    print(f"BARS_PER_TRADING_DAY\t{bars_per_day}")
    print(f"ST_CAPTURE_WINDOW\t{args.capture_days}d / {capture_bars} 30m-bars")
    print(f"ST_MOVE_HORIZON\t{args.horizon_days}d / {horizon_bars} 30m-bars")
    print(f"MEANINGFUL_MOVE_ATR\t{args.min_move_atr}")
    print(f"ENTRY_EVENTS\t{len(entry_events)}")
    print(f"EXIT_EVENTS\t{len(exit_events)}")
    print(f"REAL_BUYS\t{len(buy_rows)}")
    print(f"REAL_SELLS\t{len(sell_rows)}")
    print(f"MEANINGFUL_ST_MOVES\t{len(moves)}")

    print("\nMEANINGFUL ST MOVES / CAPTURE")
    print("-----------------------------")
    for move in moves:
        capture_end = min(len(bars) - 1, move.start_index + capture_bars)
        actual = next(
            ((index, row) for index, row in buy_indices if move.start_index <= index <= capture_end),
            None,
        )
        window = [
            row
            for row in entry_rows
            if move.start_index <= row.bar_index <= capture_end
        ]
        strongest = max(window, key=_entry_strength, default=None)
        if actual is not None:
            captured += 1
            status = "CAPTURED"
        else:
            status = "MISSED"
            if strongest is not None:
                gate_counter.update(strongest.waiting_for or strongest.reasons)
        start_time = bars.at[move.start_index, "timestamp"]
        peak_time = bars.at[move.peak_index, "timestamp"]
        if strongest is None:
            strongest_text = "candidate=NONE"
        else:
            strongest_text = (
                f"candidate={strongest.as_of} action={strongest.action} "
                f"horizon={strongest.selected_horizon} stage={strongest.selected_stage} "
                f"elig={strongest.eligibility} timing={strongest.timing} opp={strongest.opportunity} "
                f"event={strongest.event_same_bar} waiting={','.join(strongest.waiting_for) or '-'}"
            )
        print(
            f"{status} low={start_time} peak={peak_time} move={move.move_pct:.2f}%/{move.move_atr:.2f}ATR "
            f"{strongest_text}"
        )

    print("\nMISSED-MOVE GATE REASONS")
    print("------------------------")
    if not gate_counter:
        print("None.")
    else:
        for reason, count in gate_counter.most_common(25):
            print(f"{reason:<64} {count:>5}")
    print(f"\nCAPTURE_RATE\t{captured}/{len(moves)} ({0.0 if not moves else captured / len(moves) * 100.0:.2f}%)")

    print("\nREAL BUY ENTRY TIMING (NEXT 3 TRADING DAYS)")
    print("------------------------------------------")
    for index, row in buy_indices:
        end = min(len(bars) - 1, index + capture_bars)
        future = bars.loc[index:end, "low"]
        low_index = int(future.idxmin())
        entry_price = float(row.snapshot.current_price)
        low = float(bars.at[low_index, "low"])
        downside = (low / entry_price - 1.0) * 100.0
        print(
            f"BUY={row.snapshot.as_of} horizon={row.current_state.entry_metadata.entry_horizon.value if row.current_state.entry_metadata else 'NONE'} "
            f"entry={entry_price:.4f} next_low={bars.at[low_index, 'timestamp']} "
            f"bars_to_low={low_index - index} downside={downside:.2f}%"
        )

    print("\nEXIT READINESS / CLICK SYNCHRONIZATION")
    print("------------------------------------")
    active_buy = None
    previous_raw_exit_event = None
    for row in lifecycle.rows:
        as_of = row.snapshot.as_of
        raw_exit = exit_events.get(as_of)
        if row.action is DecisionAction.BUY:
            active_buy = row
            previous_raw_exit_event = None
            continue
        if active_buy is None:
            continue
        if row.exit_decision is not None and row.exit_decision.stage.value == "EXIT_READY":
            source = (
                "SAME_BAR_EVENT"
                if raw_exit is not None
                else "PREVIOUS_BAR_EVENT_AVAILABLE"
                if previous_raw_exit_event is not None
                else "NO_RECENT_RAW_EVENT"
            )
            print(
                f"READY={as_of} action={row.action.value} source={source} "
                f"reasons={','.join(row.exit_decision.reasons)}"
            )
        if row.action is DecisionAction.SELL:
            active_buy = None
            previous_raw_exit_event = None
            continue
        previous_raw_exit_event = raw_exit

    print("\nST_OPPORTUNITY_TIMING_DIAGNOSTIC_OK")


if __name__ == "__main__":
    main()
