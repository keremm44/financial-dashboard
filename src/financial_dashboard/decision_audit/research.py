from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from financial_dashboard.decision.engine import DecisionEngineConfig, assess_horizon_decision
from financial_dashboard.decision.scenario import assess_entry_scenario
from financial_dashboard.decision.structural import DecisionHorizon

from .models import DecisionAction, DecisionEvent


_EPS = 1e-12
_REQUIRED_BAR_COLUMNS = {"timestamp", "high", "low", "close"}


@dataclass(frozen=True, slots=True)
class ResearchAuditConfig:
    """Hindsight-only diagnostics; never consumed by live/canonical decisions."""

    counterfactual_thresholds_pct: tuple[float, ...] = (1.0, 2.5, 5.0)
    short_lookback_bars: int = 6
    short_lookahead_bars: int = 6
    long_lookback_bars: int = 20
    long_lookahead_bars: int = 20
    fallback_lookback_bars: int = 10
    fallback_lookahead_bars: int = 10
    large_move_min_pct: float = 10.0
    large_move_reversal_pct: float = 5.0
    attribution_top_n: int = 5

    def __post_init__(self) -> None:
        thresholds = tuple(float(value) for value in self.counterfactual_thresholds_pct)
        if not thresholds or any(value <= 0.0 for value in thresholds):
            raise ValueError("counterfactual thresholds must be positive and non-empty")
        if tuple(sorted(set(thresholds))) != thresholds:
            raise ValueError("counterfactual thresholds must be sorted and unique")
        for name in (
            "short_lookback_bars",
            "short_lookahead_bars",
            "long_lookback_bars",
            "long_lookahead_bars",
            "fallback_lookback_bars",
            "fallback_lookahead_bars",
            "attribution_top_n",
        ):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be >= 1")
        if self.long_lookback_bars < self.short_lookback_bars:
            raise ValueError("LONG_TERM lookback must be at least as wide as SHORT_TERM")
        if self.long_lookahead_bars < self.short_lookahead_bars:
            raise ValueError("LONG_TERM lookahead must be at least as wide as SHORT_TERM")
        if self.large_move_min_pct <= 0.0:
            raise ValueError("large_move_min_pct must be positive")
        if self.large_move_reversal_pct <= 0.0:
            raise ValueError("large_move_reversal_pct must be positive")
        object.__setattr__(self, "counterfactual_thresholds_pct", thresholds)


@dataclass(frozen=True, slots=True)
class PatternStateDigest:
    timeframe: str
    quality: str
    native_state: str
    phase: str


@dataclass(frozen=True, slots=True)
class HorizonStateDigest:
    horizon: str
    structural_direction: str
    thesis_state: str
    scenario_presence: str
    scenario_stage: str
    scenario_kind: str
    opportunity_state: str
    target_path_status: str
    eligibility_state: str
    timing_state: str
    reaction_state: str
    conflict_state: str
    no_execution_action: str
    scenario_waiting_for: tuple[str, ...]
    scenario_blockers: tuple[str, ...]
    diagnostic_error: str | None = None


@dataclass(frozen=True, slots=True)
class CounterfactualCheckpoint:
    threshold_pct: float
    relation: str
    checkpoint_time: Any | None
    checkpoint_price: float | None
    distance_from_extreme_pct: float | None
    action: str | None
    lifecycle_phase: str | None
    scenario_stage: str | None
    scenario_kind: str | None
    selected_horizon: str | None
    trade_horizon: str | None
    execution_state: str | None
    exit_stage: str | None
    position_health: str | None
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    waiting_for: tuple[str, ...]
    pattern_1h: PatternStateDigest | None
    pattern_30m: PatternStateDigest | None
    short_term: HorizonStateDigest | None
    long_term: HorizonStateDigest | None


@dataclass(frozen=True, slots=True)
class EventCounterfactualAudit:
    action: str
    horizon: str
    scenario_kind: str
    event_time: Any
    event_price: float
    extreme_kind: str
    extreme_time: Any
    extreme_price: float
    event_distance_from_extreme_pct: float
    event_vs_extreme: str
    checkpoints: tuple[CounterfactualCheckpoint, ...]


@dataclass(frozen=True, slots=True)
class LargeMarketMove:
    direction: str
    classification: str
    start_time: Any
    end_time: Any
    start_price: float
    end_price: float
    move_pct: float
    duration_hours: float
    four_hour_bars: int
    trading_days: int
    move_pct_per_4h_bar: float
    move_pct_per_trading_day: float


@dataclass(frozen=True, slots=True)
class LargeMoveAttribution:
    move: LargeMarketMove
    status: str
    exposed_at_start: bool
    action_time: Any | None
    action_price: float | None
    action_horizon: str | None
    move_elapsed_before_action_pct: float | None
    time_elapsed_before_action_pct: float | None
    remaining_move_after_action_pct: float | None
    dominant_waiting_for: tuple[tuple[str, int], ...]
    dominant_blockers: tuple[tuple[str, int], ...]
    dominant_reasons: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class BuySellResearchAuditReport:
    symbol: str
    audit_timeframe: str
    market_timeframe: str
    thresholds_pct: tuple[float, ...]
    counterfactuals: tuple[EventCounterfactualAudit, ...]
    large_moves: tuple[LargeMoveAttribution, ...]


def _enum_text(value: Any, default: str = "UNKNOWN") -> str:
    if value is None:
        return default
    raw = getattr(value, "value", value)
    text = str(raw).strip()
    return text if text else default


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _prepare_bars(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    missing = _REQUIRED_BAR_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(f"{label} bars missing columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError(f"{label} audit requires bars")
    bars = frame.copy(deep=True)
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], errors="raise")
    bars = (
        bars.sort_values("timestamp", kind="stable")
        .drop_duplicates("timestamp", keep="last")
        .reset_index(drop=True)
    )
    for column in ("high", "low", "close"):
        bars[column] = pd.to_numeric(bars[column], errors="raise").astype(float)
    if (bars["high"] < bars["low"]).any():
        raise ValueError(f"{label} bars contain high < low")
    return bars


def _event_index(bars: pd.DataFrame, timestamp: Any) -> int:
    target = pd.Timestamp(timestamp)
    position = int(bars["timestamp"].searchsorted(target, side="right")) - 1
    if position < 0:
        raise ValueError("decision event occurs before first audit bar")
    return position


def _event_price(event: DecisionEvent, bars: pd.DataFrame, index: int) -> float:
    return float(event.price) if event.price is not None else float(bars.iloc[index]["close"])


def _entry_horizon(event: DecisionEvent) -> str:
    for key in ("trade_horizon", "entry_horizon", "thesis_horizon"):
        value = event.snapshot.get(key)
        if isinstance(value, str) and value:
            return value
    metadata = _mapping(event.snapshot.get("entry_metadata"))
    if metadata is not None:
        value = metadata.get("entry_horizon")
        if isinstance(value, str) and value:
            return value
    return "UNKNOWN"


def _scenario_kind(event: DecisionEvent) -> str:
    value = event.snapshot.get("scenario_kind")
    return str(value) if isinstance(value, str) and value else "UNKNOWN"


def _window_for(horizon: str, config: ResearchAuditConfig) -> tuple[int, int]:
    if horizon == "SHORT_TERM":
        return config.short_lookback_bars, config.short_lookahead_bars
    if horizon == "LONG_TERM":
        return config.long_lookback_bars, config.long_lookahead_bars
    return config.fallback_lookback_bars, config.fallback_lookahead_bars


def _position_state(event: DecisionEvent | None) -> str:
    if event is None:
        return "FLAT"
    lifecycle = _mapping(event.snapshot.get("trade_lifecycle"))
    if lifecycle is None:
        return "UNKNOWN"
    return str(lifecycle.get("position_state", "UNKNOWN")).upper()


def _latest_event_at_or_before(events: Sequence[DecisionEvent], timestamp: Any) -> DecisionEvent | None:
    target = pd.Timestamp(timestamp)
    latest = None
    for event in events:
        event_time = pd.Timestamp(event.timestamp)
        if event_time > target:
            break
        latest = event
    return latest


def _events_between(
    events: Sequence[DecisionEvent],
    start: Any,
    end: Any,
    *,
    include_start: bool = True,
    include_end: bool = True,
) -> tuple[DecisionEvent, ...]:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    rows = []
    for event in events:
        timestamp = pd.Timestamp(event.timestamp)
        if timestamp < start_ts or (timestamp == start_ts and not include_start):
            continue
        if timestamp > end_ts or (timestamp == end_ts and not include_end):
            continue
        rows.append(event)
    return tuple(rows)


def _pattern_digest(snapshot: Any, timeframe: str) -> PatternStateDigest | None:
    projection = getattr(snapshot, "pattern_behavior", None)
    if projection is None:
        return None
    try:
        row = projection.for_timeframe(timeframe)
    except (KeyError, AttributeError, TypeError):
        return None
    ref = getattr(row, "ref", None)
    return PatternStateDigest(
        timeframe=timeframe,
        quality=_enum_text(getattr(ref, "data_quality", None)),
        native_state=str(getattr(row, "native_state", "") or ""),
        phase=_enum_text(getattr(row, "phase", None)),
    )


def _horizon_digest(
    snapshot: Any,
    horizon: DecisionHorizon,
    decision_config: DecisionEngineConfig,
) -> HorizonStateDigest:
    try:
        assessment = assess_horizon_decision(
            snapshot,
            horizon,
            config=decision_config,
            execution_event=None,
        )
        scenario = assess_entry_scenario(
            snapshot,
            horizon,
            config=decision_config,
            assessment=assessment,
        )
        return HorizonStateDigest(
            horizon=horizon.value,
            structural_direction=_enum_text(assessment.structural.direction),
            thesis_state=_enum_text(assessment.structural.thesis_state),
            scenario_presence=_enum_text(scenario.presence),
            scenario_stage=_enum_text(scenario.stage),
            scenario_kind=_enum_text(scenario.kind),
            opportunity_state=_enum_text(assessment.opportunity.state),
            target_path_status=_enum_text(scenario.target_path_status),
            eligibility_state=_enum_text(assessment.eligibility.state),
            timing_state=_enum_text(assessment.timing.state),
            reaction_state=_enum_text(assessment.reaction.state),
            conflict_state=_enum_text(assessment.conflict.state),
            no_execution_action=_enum_text(assessment.final.action),
            scenario_waiting_for=tuple(scenario.waiting_for),
            scenario_blockers=tuple(scenario.blockers),
        )
    except Exception as exc:  # diagnostics must not acquire action authority
        return HorizonStateDigest(
            horizon=horizon.value,
            structural_direction="UNKNOWN",
            thesis_state="UNKNOWN",
            scenario_presence="UNKNOWN",
            scenario_stage="UNKNOWN",
            scenario_kind="UNKNOWN",
            opportunity_state="UNKNOWN",
            target_path_status="UNKNOWN",
            eligibility_state="UNKNOWN",
            timing_state="UNKNOWN",
            reaction_state="UNKNOWN",
            conflict_state="UNKNOWN",
            no_execution_action="UNKNOWN",
            scenario_waiting_for=(),
            scenario_blockers=(),
            diagnostic_error=f"{type(exc).__name__}: {exc}",
        )


def _event_payload(event: DecisionEvent) -> tuple[str | None, ...]:
    entry = _mapping(event.snapshot.get("entry_decision"))
    exit_payload = _mapping(event.snapshot.get("position_exit"))
    phase = event.snapshot.get("lifecycle_phase")
    if entry is not None:
        return (
            None if phase is None else str(phase),
            None if entry.get("scenario_stage") is None else str(entry.get("scenario_stage")),
            None if entry.get("scenario_kind") is None else str(entry.get("scenario_kind")),
            None if entry.get("selected_horizon") is None else str(entry.get("selected_horizon")),
            None if entry.get("trade_horizon") is None else str(entry.get("trade_horizon")),
            None if entry.get("execution_state") is None else str(entry.get("execution_state")),
        )
    execution = None if exit_payload is None else _mapping(exit_payload.get("execution"))
    return (
        None if phase is None else str(phase),
        None,
        None,
        None,
        None if event.snapshot.get("trade_horizon") is None else str(event.snapshot.get("trade_horizon")),
        None if execution is None or execution.get("state") is None else str(execution.get("state")),
    )


def _checkpoint_from_event(
    event: DecisionEvent,
    *,
    threshold_pct: float,
    relation: str,
    extreme_price: float,
    extreme_kind: str,
    snapshot_by_time: Mapping[pd.Timestamp, Any],
    decision_config: DecisionEngineConfig,
) -> CounterfactualCheckpoint:
    price = None if event.price is None else float(event.price)
    if price is None or extreme_price <= _EPS:
        distance = None
    elif extreme_kind == "LOW":
        distance = (price / extreme_price - 1.0) * 100.0
    else:
        distance = (extreme_price - price) / extreme_price * 100.0

    phase, stage, kind, selected_horizon, trade_horizon, execution_state = _event_payload(event)
    exit_payload = _mapping(event.snapshot.get("position_exit"))
    exit_stage = None if exit_payload is None else exit_payload.get("stage")
    position_health = None if exit_payload is None else exit_payload.get("position_health")
    snapshot = snapshot_by_time.get(pd.Timestamp(event.timestamp))

    return CounterfactualCheckpoint(
        threshold_pct=float(threshold_pct),
        relation=relation,
        checkpoint_time=event.timestamp,
        checkpoint_price=price,
        distance_from_extreme_pct=distance,
        action=_enum_text(event.action),
        lifecycle_phase=phase,
        scenario_stage=stage,
        scenario_kind=kind,
        selected_horizon=selected_horizon,
        trade_horizon=trade_horizon,
        execution_state=execution_state,
        exit_stage=None if exit_stage is None else str(exit_stage),
        position_health=None if position_health is None else str(position_health),
        reasons=tuple(event.reasons),
        blockers=tuple(event.blockers),
        waiting_for=tuple(event.waiting_for),
        pattern_1h=None if snapshot is None else _pattern_digest(snapshot, "1h"),
        pattern_30m=None if snapshot is None else _pattern_digest(snapshot, "30m"),
        short_term=None if snapshot is None else _horizon_digest(snapshot, DecisionHorizon.SHORT_TERM, decision_config),
        long_term=None if snapshot is None else _horizon_digest(snapshot, DecisionHorizon.LONG_TERM, decision_config),
    )


def _empty_checkpoint(threshold_pct: float, relation: str) -> CounterfactualCheckpoint:
    return CounterfactualCheckpoint(
        threshold_pct=float(threshold_pct),
        relation=relation,
        checkpoint_time=None,
        checkpoint_price=None,
        distance_from_extreme_pct=None,
        action=None,
        lifecycle_phase=None,
        scenario_stage=None,
        scenario_kind=None,
        selected_horizon=None,
        trade_horizon=None,
        execution_state=None,
        exit_stage=None,
        position_health=None,
        reasons=(),
        blockers=(),
        waiting_for=(),
        pattern_1h=None,
        pattern_30m=None,
        short_term=None,
        long_term=None,
    )


def _threshold_event(
    *,
    event: DecisionEvent,
    events: Sequence[DecisionEvent],
    extreme_time: Any,
    extreme_price: float,
    extreme_kind: str,
    threshold_pct: float,
) -> tuple[str, DecisionEvent | None]:
    event_time = pd.Timestamp(event.timestamp)
    extreme_time = pd.Timestamp(extreme_time)

    if extreme_kind == "LOW":
        threshold_price = extreme_price * (1.0 + threshold_pct / 100.0)
        if extreme_time <= event_time:
            candidates = _events_between(events, extreme_time, event_time, include_end=False)
            for candidate in candidates:
                if candidate.price is not None and float(candidate.price) >= threshold_price:
                    return "BEFORE_EVENT", candidate
            return "EVENT_BEFORE_THRESHOLD", None
        candidates = _events_between(events, event_time, extreme_time, include_start=False)
        for candidate in candidates:
            if candidate.price is not None and float(candidate.price) <= threshold_price:
                return "AFTER_EVENT_TOWARD_EXTREME", candidate
        return "EVENT_PRECEDED_EXTREME_NO_THRESHOLD_SNAPSHOT", None

    threshold_price = extreme_price * (1.0 - threshold_pct / 100.0)
    if extreme_time <= event_time:
        candidates = _events_between(events, extreme_time, event_time, include_end=False)
        for candidate in candidates:
            if candidate.price is not None and float(candidate.price) <= threshold_price:
                return "BEFORE_EVENT", candidate
        return "EVENT_BEFORE_THRESHOLD", None
    candidates = _events_between(events, event_time, extreme_time, include_start=False)
    for candidate in candidates:
        if candidate.price is not None and float(candidate.price) >= threshold_price:
            return "AFTER_EVENT_TOWARD_EXTREME", candidate
    return "EVENT_PRECEDED_EXTREME_NO_THRESHOLD_SNAPSHOT", None


def audit_event_counterfactuals(
    *,
    audit_bars: pd.DataFrame,
    decisions: Iterable[DecisionEvent],
    snapshots: Iterable[Any] = (),
    decision_config: DecisionEngineConfig | None = None,
    config: ResearchAuditConfig | None = None,
) -> tuple[EventCounterfactualAudit, ...]:
    """Explain what the causal system still lacked near hindsight extrema."""

    cfg = config or ResearchAuditConfig()
    engine_cfg = decision_config or DecisionEngineConfig()
    bars = _prepare_bars(audit_bars, "counterfactual")
    events = tuple(sorted(decisions, key=lambda item: pd.Timestamp(item.timestamp)))
    snapshot_by_time = {pd.Timestamp(item.as_of): item for item in snapshots}
    results: list[EventCounterfactualAudit] = []

    for event in events:
        if event.action not in {DecisionAction.BUY, DecisionAction.SELL}:
            continue
        horizon = _entry_horizon(event)
        lookback, lookahead = _window_for(horizon, cfg)
        index = _event_index(bars, event.timestamp)
        event_price = _event_price(event, bars, index)
        start = max(0, index - lookback)
        end = min(len(bars) - 1, index + lookahead)

        if event.action is DecisionAction.BUY:
            extreme_index = int(bars.loc[start:end, "low"].idxmin())
            extreme_price = float(bars.at[extreme_index, "low"])
            extreme_kind = "LOW"
            distance = (event_price / extreme_price - 1.0) * 100.0 if extreme_price > _EPS else 0.0
        else:
            extreme_index = int(bars.loc[start:end, "high"].idxmax())
            extreme_price = float(bars.at[extreme_index, "high"])
            extreme_kind = "HIGH"
            distance = (extreme_price - event_price) / extreme_price * 100.0 if extreme_price > _EPS else 0.0

        extreme_time = bars.at[extreme_index, "timestamp"]
        relation = (
            "AFTER_EXTREME"
            if extreme_index < index
            else "BEFORE_EXTREME"
            if extreme_index > index
            else "SAME_BAR_AS_EXTREME"
        )
        checkpoints: list[CounterfactualCheckpoint] = []
        for threshold in cfg.counterfactual_thresholds_pct:
            checkpoint_relation, checkpoint_event = _threshold_event(
                event=event,
                events=events,
                extreme_time=extreme_time,
                extreme_price=extreme_price,
                extreme_kind=extreme_kind,
                threshold_pct=threshold,
            )
            checkpoints.append(
                _empty_checkpoint(threshold, checkpoint_relation)
                if checkpoint_event is None
                else _checkpoint_from_event(
                    checkpoint_event,
                    threshold_pct=threshold,
                    relation=checkpoint_relation,
                    extreme_price=extreme_price,
                    extreme_kind=extreme_kind,
                    snapshot_by_time=snapshot_by_time,
                    decision_config=engine_cfg,
                )
            )

        results.append(
            EventCounterfactualAudit(
                action=_enum_text(event.action),
                horizon=horizon,
                scenario_kind=_scenario_kind(event),
                event_time=event.timestamp,
                event_price=event_price,
                extreme_kind=extreme_kind,
                extreme_time=extreme_time,
                extreme_price=extreme_price,
                event_distance_from_extreme_pct=distance,
                event_vs_extreme=relation,
                checkpoints=tuple(checkpoints),
            )
        )
    return tuple(results)


def _classify_move(move_pct: float) -> str:
    magnitude = abs(float(move_pct))
    if magnitude >= 25.0:
        return "EXTREME"
    if magnitude >= 15.0:
        return "MAJOR"
    return "LARGE"


def _large_move(
    bars: pd.DataFrame,
    *,
    direction: str,
    start_index: int,
    end_index: int,
    start_price: float,
    end_price: float,
) -> LargeMarketMove:
    move_pct = (end_price / start_price - 1.0) * 100.0
    start_time = bars.at[start_index, "timestamp"]
    end_time = bars.at[end_index, "timestamp"]
    duration_hours = max(0.0, (pd.Timestamp(end_time) - pd.Timestamp(start_time)).total_seconds() / 3600.0)
    bar_count = max(1, end_index - start_index + 1)
    intervals = max(1, end_index - start_index)
    trading_days = max(1, int(bars.loc[start_index:end_index, "timestamp"].dt.normalize().nunique()))
    magnitude = abs(move_pct)
    return LargeMarketMove(
        direction=direction,
        classification=_classify_move(move_pct),
        start_time=start_time,
        end_time=end_time,
        start_price=float(start_price),
        end_price=float(end_price),
        move_pct=float(move_pct),
        duration_hours=duration_hours,
        four_hour_bars=bar_count,
        trading_days=trading_days,
        move_pct_per_4h_bar=magnitude / intervals,
        move_pct_per_trading_day=magnitude / trading_days,
    )


def _detect_up_moves(bars: pd.DataFrame, *, min_move_pct: float, reversal_pct: float) -> list[LargeMarketMove]:
    results: list[LargeMarketMove] = []
    cursor = 0
    count = len(bars)
    while cursor < count - 1:
        anchor_index = cursor
        anchor_price = float(bars.at[cursor, "low"])
        peak_index = cursor
        peak_price = float(bars.at[cursor, "high"])
        qualified = False
        finalized = False
        for index in range(cursor + 1, count):
            low = float(bars.at[index, "low"])
            high = float(bars.at[index, "high"])
            if not qualified and low < anchor_price:
                anchor_index, anchor_price = index, low
                peak_index, peak_price = index, high
            if high > peak_price:
                peak_index, peak_price = index, high
            move_pct = (peak_price / anchor_price - 1.0) * 100.0 if anchor_price > _EPS else 0.0
            qualified = qualified or move_pct >= min_move_pct
            if qualified and index > peak_index:
                drawdown = (peak_price - low) / peak_price * 100.0 if peak_price > _EPS else 0.0
                if drawdown >= reversal_pct:
                    results.append(_large_move(bars, direction="UP", start_index=anchor_index, end_index=peak_index, start_price=anchor_price, end_price=peak_price))
                    cursor = max(peak_index + 1, cursor + 1)
                    finalized = True
                    break
        if finalized:
            continue
        if qualified:
            results.append(_large_move(bars, direction="UP", start_index=anchor_index, end_index=peak_index, start_price=anchor_price, end_price=peak_price))
        break
    return results


def _detect_down_moves(bars: pd.DataFrame, *, min_move_pct: float, reversal_pct: float) -> list[LargeMarketMove]:
    results: list[LargeMarketMove] = []
    cursor = 0
    count = len(bars)
    while cursor < count - 1:
        anchor_index = cursor
        anchor_price = float(bars.at[cursor, "high"])
        trough_index = cursor
        trough_price = float(bars.at[cursor, "low"])
        qualified = False
        finalized = False
        for index in range(cursor + 1, count):
            low = float(bars.at[index, "low"])
            high = float(bars.at[index, "high"])
            if not qualified and high > anchor_price:
                anchor_index, anchor_price = index, high
                trough_index, trough_price = index, low
            if low < trough_price:
                trough_index, trough_price = index, low
            move_pct = (trough_price / anchor_price - 1.0) * 100.0 if anchor_price > _EPS else 0.0
            qualified = qualified or abs(move_pct) >= min_move_pct
            if qualified and index > trough_index:
                rebound = (high - trough_price) / trough_price * 100.0 if trough_price > _EPS else 0.0
                if rebound >= reversal_pct:
                    results.append(_large_move(bars, direction="DOWN", start_index=anchor_index, end_index=trough_index, start_price=anchor_price, end_price=trough_price))
                    cursor = max(trough_index + 1, cursor + 1)
                    finalized = True
                    break
        if finalized:
            continue
        if qualified:
            results.append(_large_move(bars, direction="DOWN", start_index=anchor_index, end_index=trough_index, start_price=anchor_price, end_price=trough_price))
        break
    return results


def detect_large_market_moves(
    market_bars_4h: pd.DataFrame,
    *,
    min_move_pct: float = 10.0,
    reversal_pct: float = 5.0,
) -> tuple[LargeMarketMove, ...]:
    """Find maximal non-overlapping >= threshold 4H excursions per direction."""

    if min_move_pct <= 0.0 or reversal_pct <= 0.0:
        raise ValueError("large-move thresholds must be positive")
    bars = _prepare_bars(market_bars_4h, "large-move")
    moves = [
        *_detect_up_moves(bars, min_move_pct=min_move_pct, reversal_pct=reversal_pct),
        *_detect_down_moves(bars, min_move_pct=min_move_pct, reversal_pct=reversal_pct),
    ]
    return tuple(sorted(moves, key=lambda item: (pd.Timestamp(item.start_time), item.direction)))


def _top_tokens(events: Sequence[DecisionEvent], field: str, *, limit: int) -> tuple[tuple[str, int], ...]:
    counter: Counter[str] = Counter()
    for event in events:
        counter.update(str(value) for value in getattr(event, field) if value)
    return tuple(counter.most_common(limit))


def _market_progress(move: LargeMarketMove, price: float) -> float:
    total = move.end_price - move.start_price
    if abs(total) <= _EPS:
        return 0.0
    return max(0.0, min(100.0, (float(price) - move.start_price) / total * 100.0))


def _time_progress(move: LargeMarketMove, timestamp: Any) -> float:
    start, end = pd.Timestamp(move.start_time), pd.Timestamp(move.end_time)
    total = (end - start).total_seconds()
    if total <= 0.0:
        return 0.0
    elapsed = (pd.Timestamp(timestamp) - start).total_seconds()
    return max(0.0, min(100.0, elapsed / total * 100.0))


def _remaining_move(move: LargeMarketMove, price: float) -> float | None:
    if abs(float(price)) <= _EPS:
        return None
    return (move.end_price / float(price) - 1.0) * 100.0


def attribute_large_market_moves(
    moves: Iterable[LargeMarketMove],
    decisions: Iterable[DecisionEvent],
    *,
    top_n: int = 5,
) -> tuple[LargeMoveAttribution, ...]:
    """Match hindsight 4H legs to the already-frozen long-only decision stream."""

    if top_n < 1:
        raise ValueError("top_n must be >= 1")
    events = tuple(sorted(decisions, key=lambda item: pd.Timestamp(item.timestamp)))
    rows: list[LargeMoveAttribution] = []

    for move in moves:
        start_state_event = _latest_event_at_or_before(events, move.start_time)
        exposed_at_start = _position_state(start_state_event) == "OPEN"
        window = _events_between(events, move.start_time, move.end_time)
        action_event: DecisionEvent | None = None
        evidence_start = pd.Timestamp(move.start_time)

        if move.direction == "UP":
            if exposed_at_start:
                status = "ALREADY_LONG"
            else:
                action_event = next((event for event in window if event.action is DecisionAction.BUY), None)
                status = "BUY_CAPTURED" if action_event is not None else "MISSED_NO_BUY"
            evidence_end = move.end_time if action_event is None else action_event.timestamp
        else:
            exposure_event = None
            if not exposed_at_start:
                exposure_event = next((event for event in window if event.action is DecisionAction.BUY), None)
                if exposure_event is not None:
                    evidence_start = pd.Timestamp(exposure_event.timestamp)
            exposed = exposed_at_start or exposure_event is not None
            if not exposed:
                status = "NOT_EXPOSED"
                evidence_end = move.end_time
            else:
                action_event = next(
                    (
                        event
                        for event in window
                        if event.action is DecisionAction.SELL and pd.Timestamp(event.timestamp) >= evidence_start
                    ),
                    None,
                )
                if action_event is not None:
                    status = "SELL_CAPTURED" if exposed_at_start else "ENTERED_DURING_DOWNSIDE_THEN_SOLD"
                    evidence_end = action_event.timestamp
                else:
                    status = "OPEN_LONG_NO_SELL" if exposed_at_start else "ENTERED_DURING_DOWNSIDE_NO_SELL"
                    evidence_end = move.end_time

        evidence_events = _events_between(
            events,
            evidence_start,
            evidence_end,
            include_end=action_event is None,
        )
        action_price = None if action_event is None or action_event.price is None else float(action_event.price)
        rows.append(
            LargeMoveAttribution(
                move=move,
                status=status,
                exposed_at_start=exposed_at_start,
                action_time=None if action_event is None else action_event.timestamp,
                action_price=action_price,
                action_horizon=None if action_event is None else _entry_horizon(action_event),
                move_elapsed_before_action_pct=None if action_price is None else _market_progress(move, action_price),
                time_elapsed_before_action_pct=None if action_event is None else _time_progress(move, action_event.timestamp),
                remaining_move_after_action_pct=None if action_price is None else _remaining_move(move, action_price),
                dominant_waiting_for=_top_tokens(evidence_events, "waiting_for", limit=top_n),
                dominant_blockers=_top_tokens(evidence_events, "blockers", limit=top_n),
                dominant_reasons=_top_tokens(evidence_events, "reasons", limit=top_n),
            )
        )
    return tuple(rows)


def audit_buy_sell_research(
    *,
    symbol: str,
    audit_timeframe: str,
    audit_bars: pd.DataFrame,
    market_bars_4h: pd.DataFrame,
    decisions: Iterable[DecisionEvent],
    snapshots: Iterable[Any] = (),
    decision_config: DecisionEngineConfig | None = None,
    config: ResearchAuditConfig | None = None,
) -> BuySellResearchAuditReport:
    """Build the combined post-hoc entry/exit and large-move research report."""

    cfg = config or ResearchAuditConfig()
    events = tuple(sorted(decisions, key=lambda item: pd.Timestamp(item.timestamp)))
    market = _prepare_bars(market_bars_4h, "large-move")
    if events:
        start, end = pd.Timestamp(events[0].timestamp), pd.Timestamp(events[-1].timestamp)
        market = market[(market["timestamp"] >= start) & (market["timestamp"] <= end)].reset_index(drop=True)
        if market.empty:
            raise ValueError("no 4H market bars overlap the decision audit period")

    counterfactuals = audit_event_counterfactuals(
        audit_bars=audit_bars,
        decisions=events,
        snapshots=snapshots,
        decision_config=decision_config,
        config=cfg,
    )
    moves = detect_large_market_moves(
        market,
        min_move_pct=cfg.large_move_min_pct,
        reversal_pct=cfg.large_move_reversal_pct,
    )
    return BuySellResearchAuditReport(
        symbol=symbol,
        audit_timeframe=audit_timeframe,
        market_timeframe="4h",
        thresholds_pct=cfg.counterfactual_thresholds_pct,
        counterfactuals=counterfactuals,
        large_moves=attribute_large_market_moves(moves, events, top_n=cfg.attribution_top_n),
    )


__all__ = [
    "BuySellResearchAuditReport",
    "CounterfactualCheckpoint",
    "EventCounterfactualAudit",
    "HorizonStateDigest",
    "LargeMarketMove",
    "LargeMoveAttribution",
    "PatternStateDigest",
    "ResearchAuditConfig",
    "attribute_large_market_moves",
    "audit_buy_sell_research",
    "audit_event_counterfactuals",
    "detect_large_market_moves",
]
