from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.context.envelope import normalize_context_data_quality
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.arbiter import assess_entry_arbitration
from financial_dashboard.decision.calibration import load_opportunity_calibration
from financial_dashboard.decision.engine import (
    DecisionEngineConfig,
    _execution_channel_quality,
    assess_horizon_decision,
)
from financial_dashboard.decision.execution_detect import detect_30m_execution_events
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.scenario import ScenarioStage
from financial_dashboard.decision.structural import DecisionHorizon
from financial_dashboard.decision.timeline_cache import DecisionTimelineCacheMiss, load_frozen_decision_timeline
from financial_dashboard.structure_location_replay import CausalBarClock


def _value(value) -> str:
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


def _nearest_event_distance(index: int, event_indices: list[int]) -> tuple[int | None, int | None]:
    previous = [item for item in event_indices if item <= index]
    following = [item for item in event_indices if item >= index]
    prev_distance = None if not previous else index - previous[-1]
    next_distance = None if not following else following[0] - index
    return prev_distance, next_distance


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnose why frozen entry scenarios and 30m execution events do not intersect. Domains are never replayed."
    )
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--pattern-profile", default=None)
    args = parser.parse_args()

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
    entry_events, _ = detect_30m_execution_events(snapshots)
    event_indices = [index for index, snapshot in enumerate(snapshots) if snapshot.as_of in entry_events]

    counters: Counter[str] = Counter()
    event_gate_reasons: Counter[str] = Counter()
    qualified_rows: list[dict] = []
    event_rows: list[dict] = []

    for index, snapshot in enumerate(snapshots):
        arbitration = assess_entry_arbitration(snapshot, config=cfg)
        selected = arbitration.selected_scenario
        selected_horizon = arbitration.selected_horizon
        event = entry_events.get(snapshot.as_of)
        raw_q30 = normalize_context_data_quality(snapshot.quality_for_timeframe(cfg.execution_timeframe))
        exec_q30 = _execution_channel_quality(snapshot, cfg.execution_timeframe)

        lt = assess_horizon_decision(snapshot, DecisionHorizon.LONG_TERM, config=cfg)
        st = assess_horizon_decision(snapshot, DecisionHorizon.SHORT_TERM, config=cfg)
        assessments = {DecisionHorizon.LONG_TERM: lt, DecisionHorizon.SHORT_TERM: st}

        if selected is None or selected_horizon is None:
            counters["NO_SELECTED_SCENARIO"] += 1
        else:
            counters[f"SELECTED_STAGE:{_value(selected.stage)}"] += 1
            counters[f"SELECTED_HORIZON:{_value(selected_horizon)}"] += 1

        if event is not None:
            counters["ENTRY_EVENT"] += 1
            if selected is None:
                counters["EVENT_WITHOUT_SELECTED_SCENARIO"] += 1
                event_gate_reasons.update(arbitration.reasons)
            elif selected.stage is not ScenarioStage.QUALIFIED:
                counters[f"EVENT_WITH_SELECTED_{_value(selected.stage)}"] += 1
                event_gate_reasons.update(selected.waiting_for)
                event_gate_reasons.update(selected.reasons)
            else:
                counters["EVENT_WITH_QUALIFIED_SCENARIO"] += 1

        if selected is not None and selected_horizon is not None and selected.stage is ScenarioStage.QUALIFIED:
            assessment = assessments[selected_horizon]
            counters["QUALIFIED_SELECTED"] += 1
            counters[f"QUALIFIED_RAW_Q30:{raw_q30.value}"] += 1
            counters[f"QUALIFIED_EXEC_QUALITY:{exec_q30.value}"] += 1
            counters[f"QUALIFIED_ELIGIBILITY:{_value(assessment.eligibility.state)}"] += 1
            counters[f"QUALIFIED_NO_EVENT_ACTION:{_value(assessment.final.action)}"] += 1
            action_with_event = None
            if event is not None:
                with_event = assess_horizon_decision(
                    snapshot,
                    selected_horizon,
                    config=cfg,
                    execution_event=event,
                )
                action_with_event = _value(with_event.final.action)
                counters[f"QUALIFIED_WITH_EVENT_ACTION:{action_with_event}"] += 1
            prev_distance, next_distance = _nearest_event_distance(index, event_indices)
            qualified_rows.append(
                {
                    "as_of": snapshot.as_of,
                    "horizon": _value(selected_horizon),
                    "raw_quality_30m": raw_q30.value,
                    "execution_quality_30m": exec_q30.value,
                    "eligibility": _value(assessment.eligibility.state),
                    "timing": _value(assessment.timing.state),
                    "opportunity": _value(assessment.opportunity.state),
                    "conflict": _value(assessment.conflict.state),
                    "action_without_event": _value(assessment.final.action),
                    "action_with_event": action_with_event,
                    "entry_event_same_bar": event is not None,
                    "prev_event_distance_bars": prev_distance,
                    "next_event_distance_bars": next_distance,
                    "waiting_for": ",".join(assessment.final.waiting_for),
                }
            )

        if event is not None:
            event_rows.append(
                {
                    "decision_as_of": snapshot.as_of,
                    "event_observed_at": event.observed_at,
                    "event_available_at": event.available_at,
                    "event_reason": event.reason,
                    "raw_quality_30m": raw_q30.value,
                    "execution_quality_30m": exec_q30.value,
                    "selected_horizon": "NONE" if selected_horizon is None else _value(selected_horizon),
                    "selected_stage": "NONE" if selected is None else _value(selected.stage),
                    "lt_stage": _value(snapshot.entry_scenario(DecisionHorizon.LONG_TERM, config=cfg).stage),
                    "st_stage": _value(snapshot.entry_scenario(DecisionHorizon.SHORT_TERM, config=cfg).stage),
                    "lt_eligibility": _value(lt.eligibility.state),
                    "st_eligibility": _value(st.eligibility.state),
                }
            )

    print("=" * 88)
    print("ENTRY / 30M EXECUTION ALIGNMENT DIAGNOSTIC")
    print("=" * 88)
    print(f"SYMBOL\t{clean_symbol}")
    print(f"SNAPSHOTS\t{len(snapshots)}")
    print(f"FROZEN_CACHE_STATUS\t{frozen.cache_status}")
    print("DOMAIN_REPLAY_SECONDS\t0.000")
    print(f"OPPORTUNITY_CALIBRATION\t{calibration_path}")
    print(f"ENTRY_EVENTS\t{len(entry_events)}")

    print("\nSUMMARY")
    print("-------")
    for key, count in counters.most_common():
        print(f"{key:<42} {count:>6}")

    print("\nENTRY EVENT BLOCKING REASONS")
    print("----------------------------")
    if not event_gate_reasons:
        print("None.")
    else:
        for key, count in event_gate_reasons.most_common(25):
            print(f"{key:<58} {count:>6}")

    print("\nQUALIFIED SELECTED SNAPSHOTS")
    print("----------------------------")
    if not qualified_rows:
        print("None.")
    else:
        for row in qualified_rows:
            print(
                f"{row['as_of']} horizon={row['horizon']} raw_q30={row['raw_quality_30m']} "
                f"exec_q30={row['execution_quality_30m']} elig={row['eligibility']} "
                f"timing={row['timing']} opp={row['opportunity']} conflict={row['conflict']} "
                f"action={row['action_without_event']} with_event={row['action_with_event']} "
                f"event_same_bar={row['entry_event_same_bar']} "
                f"prev_event={row['prev_event_distance_bars']} next_event={row['next_event_distance_bars']} "
                f"waiting={row['waiting_for']}"
            )

    print("\nENTRY EVENT WINDOWS")
    print("-------------------")
    for row in event_rows:
        print(
            f"decision={row['decision_as_of']} observed={row['event_observed_at']} available={row['event_available_at']} "
            f"raw_q30={row['raw_quality_30m']} exec_q30={row['execution_quality_30m']} "
            f"selected={row['selected_horizon']}:{row['selected_stage']} "
            f"LT={row['lt_stage']}/{row['lt_eligibility']} ST={row['st_stage']}/{row['st_eligibility']} "
            f"reason={row['event_reason']}"
        )

    print("\nENTRY_EXECUTION_ALIGNMENT_OK")


if __name__ == "__main__":
    main()
