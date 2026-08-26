from __future__ import annotations

from collections import Counter
from dataclasses import replace
from math import prod
from statistics import mean, median
from typing import Iterable, Mapping

import pandas as pd

from .models import (
    AggregateTradeMetrics,
    CensoredTradeAudit,
    DecisionAction,
    DecisionAuditConfig,
    DecisionAuditReport,
    DecisionEvent,
    DecisionSide,
    LifecycleAudit,
    MissedOpportunity,
    SignalStabilityAudit,
    TradeAudit,
)

_REQUIRED_BAR_COLUMNS = {"timestamp", "high", "low", "close"}
_EPS = 1e-12
_EXIT_STAGES = frozenset({"MONITOR", "EXIT_WATCH", "EXIT_READY"})


def _as_timestamp(value: object) -> pd.Timestamp:
    return pd.Timestamp(value)


def _prepare_bars(frame: pd.DataFrame, *, atr_length: int) -> pd.DataFrame:
    missing = _REQUIRED_BAR_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(f"decision audit bars missing columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError("decision audit requires at least one price bar")

    bars = frame.copy(deep=True)
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], errors="raise")
    bars = bars.sort_values("timestamp", kind="stable").drop_duplicates("timestamp", keep="last")
    bars = bars.reset_index(drop=True)
    for column in ("high", "low", "close"):
        bars[column] = pd.to_numeric(bars[column], errors="raise").astype(float)
    if (bars["high"] < bars["low"]).any():
        raise ValueError("decision audit bars contain high < low")

    if "atr" in bars.columns:
        atr = pd.to_numeric(bars["atr"], errors="coerce").astype(float)
    else:
        atr = pd.Series(float("nan"), index=bars.index, dtype=float)

    prev_close = bars["close"].shift(1)
    true_range = pd.concat(
        (
            bars["high"] - bars["low"],
            (bars["high"] - prev_close).abs(),
            (bars["low"] - prev_close).abs(),
        ),
        axis=1,
    ).max(axis=1)
    audit_atr = true_range.rolling(atr_length, min_periods=1).mean()
    bars["_audit_atr"] = atr.where(atr > _EPS, audit_atr).fillna(audit_atr)
    return bars


def _event_index(bars: pd.DataFrame, timestamp: object) -> int:
    target = _as_timestamp(timestamp)
    positions = bars["timestamp"].searchsorted(target, side="right")
    index = int(positions) - 1
    if index < 0:
        raise ValueError(f"decision event {target} occurs before the first audit bar")
    return index


def _event_price(event: DecisionEvent, bars: pd.DataFrame, index: int) -> float:
    if event.price is not None:
        return float(event.price)
    return float(bars.iloc[index]["close"])


def _event_atr(event: DecisionEvent, bars: pd.DataFrame, index: int) -> float | None:
    if event.atr is not None and float(event.atr) > _EPS:
        return float(event.atr)
    value = float(bars.iloc[index]["_audit_atr"])
    return value if value > _EPS else None


def _pct(numerator: float, denominator: float) -> float | None:
    if abs(float(denominator)) <= _EPS:
        return None
    return float(numerator) / float(denominator) * 100.0


def _atr_units(distance: float, atr: float | None) -> float | None:
    if atr is None or abs(atr) <= _EPS:
        return None
    return float(distance) / float(atr)


def _window_bounds(center: int, lookback: int, lookahead: int, length: int) -> tuple[int, int]:
    return max(0, center - lookback), min(length - 1, center + lookahead)


def _min_with_index(bars: pd.DataFrame, start: int, end: int) -> tuple[float, int]:
    series = bars.loc[start:end, "low"]
    index = int(series.idxmin())
    return float(series.loc[index]), index


def _max_with_index(bars: pd.DataFrame, start: int, end: int) -> tuple[float, int]:
    series = bars.loc[start:end, "high"]
    index = int(series.idxmax())
    return float(series.loc[index]), index


def _mean_or_none(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return None if not clean else mean(clean)


def _audit_long_trade(
    symbol: str,
    entry_event: DecisionEvent,
    exit_event: DecisionEvent,
    entry_index: int,
    exit_index: int,
    bars: pd.DataFrame,
    config: DecisionAuditConfig,
) -> TradeAudit:
    entry_price = _event_price(entry_event, bars, entry_index)
    exit_price = _event_price(exit_event, bars, exit_index)
    entry_atr = _event_atr(entry_event, bars, entry_index)
    exit_atr = _event_atr(exit_event, bars, exit_index)

    trade_slice = bars.loc[entry_index:exit_index]
    trade_peak = float(trade_slice["high"].max())
    trade_low = float(trade_slice["low"].min())
    return_pct = _pct(exit_price - entry_price, entry_price) or 0.0
    mfe_pct = _pct(trade_peak - entry_price, entry_price) or 0.0
    mae_pct = _pct(trade_low - entry_price, entry_price) or 0.0
    capture_ratio = None if mfe_pct <= _EPS else return_pct / mfe_pct

    entry_start, entry_end = _window_bounds(
        entry_index,
        config.extrema_lookback_bars,
        config.extrema_lookahead_bars,
        len(bars),
    )
    entry_low, entry_low_index = _min_with_index(bars, entry_start, entry_end)
    entry_low_miss_pct = _pct(max(0.0, entry_price - entry_low), entry_low)
    entry_low_miss_atr = _atr_units(max(0.0, entry_price - entry_low), entry_atr)
    entry_offset = entry_low_index - entry_index
    entry_early_bars = max(0, entry_offset)
    entry_late_bars = max(0, -entry_offset)

    entry_future_end = min(len(bars) - 1, entry_index + config.extrema_lookahead_bars)
    future_low, _ = _min_with_index(bars, entry_index, entry_future_end)
    post_entry_downside_pct = _pct(min(0.0, future_low - entry_price), entry_price)
    post_entry_downside_atr = _atr_units(min(0.0, future_low - entry_price), entry_atr)

    exit_start, exit_end = _window_bounds(
        exit_index,
        config.extrema_lookback_bars,
        config.extrema_lookahead_bars,
        len(bars),
    )
    exit_high, exit_high_index = _max_with_index(bars, exit_start, exit_end)
    exit_peak_miss_pct = _pct(max(0.0, exit_high - exit_price), exit_high)
    exit_peak_miss_atr = _atr_units(max(0.0, exit_high - exit_price), exit_atr)
    exit_offset = exit_high_index - exit_index
    exit_early_bars = max(0, exit_offset)
    exit_late_bars = max(0, -exit_offset)

    if exit_index + 1 < len(bars):
        post_exit_end = min(len(bars) - 1, exit_index + config.extrema_lookahead_bars)
        post_exit_high, _ = _max_with_index(bars, exit_index + 1, post_exit_end)
        post_exit_missed_pct = _pct(max(0.0, post_exit_high - exit_price), exit_price)
        post_exit_missed_atr = _atr_units(max(0.0, post_exit_high - exit_price), exit_atr)
    else:
        post_exit_missed_pct = None
        post_exit_missed_atr = None

    profit_giveback_pct = _pct(max(0.0, trade_peak - exit_price), trade_peak)
    profit_giveback_atr = _atr_units(max(0.0, trade_peak - exit_price), exit_atr)

    return TradeAudit(
        symbol=symbol,
        side=DecisionSide.LONG,
        entry_time=entry_event.timestamp,
        exit_time=exit_event.timestamp,
        entry_price=entry_price,
        exit_price=exit_price,
        bars_held=max(1, exit_index - entry_index + 1),
        return_pct=return_pct,
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
        move_capture_ratio=capture_ratio,
        entry_local_low=entry_low,
        entry_local_low_miss_pct=entry_low_miss_pct,
        entry_local_low_miss_atr=entry_low_miss_atr,
        entry_early_bars=entry_early_bars,
        entry_late_bars=entry_late_bars,
        post_entry_additional_downside_pct=post_entry_downside_pct,
        post_entry_additional_downside_atr=post_entry_downside_atr,
        exit_local_high=exit_high,
        exit_peak_miss_pct=exit_peak_miss_pct,
        exit_peak_miss_atr=exit_peak_miss_atr,
        exit_early_bars=exit_early_bars,
        exit_late_bars=exit_late_bars,
        post_exit_missed_upside_pct=post_exit_missed_pct,
        post_exit_missed_upside_atr=post_exit_missed_atr,
        profit_giveback_pct=profit_giveback_pct,
        profit_giveback_atr=profit_giveback_atr,
        entry_reasons=entry_event.reasons,
        entry_blockers=entry_event.blockers,
        entry_waiting_for=entry_event.waiting_for,
        entry_source_lineage=entry_event.source_lineage,
        entry_snapshot=entry_event.snapshot,
        exit_reasons=exit_event.reasons,
        exit_blockers=exit_event.blockers,
        exit_waiting_for=exit_event.waiting_for,
        exit_source_lineage=exit_event.source_lineage,
        exit_snapshot=exit_event.snapshot,
    )


def _audit_censored_long_trade(
    symbol: str,
    entry_event: DecisionEvent,
    entry_index: int,
    bars: pd.DataFrame,
    *,
    latest_snapshot: Mapping[str, object],
) -> CensoredTradeAudit:
    entry_price = _event_price(entry_event, bars, entry_index)
    end_index = len(bars) - 1
    end_price = float(bars.iloc[end_index]["close"])
    open_slice = bars.loc[entry_index:end_index]
    peak = float(open_slice["high"].max())
    trough = float(open_slice["low"].min())
    return CensoredTradeAudit(
        symbol=symbol,
        side=DecisionSide.LONG,
        entry_time=entry_event.timestamp,
        sample_end_time=bars.iloc[end_index]["timestamp"],
        entry_price=entry_price,
        sample_end_price=end_price,
        bars_open=max(1, end_index - entry_index + 1),
        unrealized_return_pct=_pct(end_price - entry_price, entry_price) or 0.0,
        mfe_pct=_pct(peak - entry_price, entry_price) or 0.0,
        mae_pct=_pct(trough - entry_price, entry_price) or 0.0,
        entry_reasons=entry_event.reasons,
        entry_source_lineage=entry_event.source_lineage,
        entry_snapshot=entry_event.snapshot,
        latest_snapshot=latest_snapshot,
    )


def _pair_long_trades(
    symbol: str,
    events: tuple[DecisionEvent, ...],
    bars: pd.DataFrame,
    config: DecisionAuditConfig,
) -> tuple[tuple[TradeAudit, ...], tuple[CensoredTradeAudit, ...], int, int]:
    open_entry: tuple[DecisionEvent, int] | None = None
    audits: list[TradeAudit] = []
    censored: list[CensoredTradeAudit] = []
    unmatched_buy = 0
    unmatched_sell = 0

    for event in events:
        index = _event_index(bars, event.timestamp)
        if event.action is DecisionAction.BUY:
            if open_entry is None:
                open_entry = (event, index)
            else:
                unmatched_buy += 1
        elif event.action is DecisionAction.SELL:
            if open_entry is None:
                unmatched_sell += 1
                continue
            entry_event, entry_index = open_entry
            if index < entry_index:
                raise ValueError("SELL event precedes the active BUY event")
            audits.append(
                _audit_long_trade(
                    symbol,
                    entry_event,
                    event,
                    entry_index,
                    index,
                    bars,
                    config,
                )
            )
            open_entry = None

    if open_entry is not None:
        entry_event, entry_index = open_entry
        latest_snapshot = {} if not events else events[-1].snapshot
        censored.append(
            _audit_censored_long_trade(
                symbol,
                entry_event,
                entry_index,
                bars,
                latest_snapshot=latest_snapshot,
            )
        )
    return tuple(audits), tuple(censored), unmatched_buy, unmatched_sell


def _aggregate(trades: tuple[TradeAudit, ...]) -> AggregateTradeMetrics:
    returns = [trade.return_pct for trade in trades]
    winners = [value for value in returns if value > _EPS]
    losers = [value for value in returns if value < -_EPS]
    breakeven = len(returns) - len(winners) - len(losers)
    compounded = None
    if returns:
        compounded = (prod(1.0 + value / 100.0 for value in returns) - 1.0) * 100.0

    capture = [trade.move_capture_ratio * 100.0 for trade in trades if trade.move_capture_ratio is not None]
    early_entry = [trade for trade in trades if trade.entry_early_bars > 0]
    late_entry = [trade for trade in trades if trade.entry_late_bars > 0]
    early_exit = [trade for trade in trades if trade.exit_early_bars > 0]
    late_exit = [trade for trade in trades if trade.exit_late_bars > 0]

    downside = [
        trade.post_entry_additional_downside_pct
        for trade in trades
        if trade.post_entry_additional_downside_pct is not None
    ]
    missed_upside = [
        trade.post_exit_missed_upside_pct
        for trade in trades
        if trade.post_exit_missed_upside_pct is not None
    ]

    return AggregateTradeMetrics(
        completed_trades=len(trades),
        wins=len(winners),
        losses=len(losers),
        breakeven=breakeven,
        win_rate_pct=None if not trades else len(winners) / len(trades) * 100.0,
        average_return_pct=_mean_or_none(returns),
        median_return_pct=None if not returns else median(returns),
        compounded_return_pct=compounded,
        average_winner_pct=_mean_or_none(winners),
        average_loser_pct=_mean_or_none(losers),
        best_trade_pct=None if not returns else max(returns),
        worst_trade_pct=None if not returns else min(returns),
        average_mfe_pct=_mean_or_none(trade.mfe_pct for trade in trades),
        average_mae_pct=_mean_or_none(trade.mae_pct for trade in trades),
        average_move_capture_ratio_pct=_mean_or_none(capture),
        average_entry_local_low_miss_pct=_mean_or_none(trade.entry_local_low_miss_pct for trade in trades),
        average_entry_local_low_miss_atr=_mean_or_none(trade.entry_local_low_miss_atr for trade in trades),
        early_entry_cases=len(early_entry),
        late_entry_cases=len(late_entry),
        average_entry_early_bars=_mean_or_none(trade.entry_early_bars for trade in early_entry),
        average_entry_late_bars=_mean_or_none(trade.entry_late_bars for trade in late_entry),
        average_post_entry_additional_downside_pct=_mean_or_none(downside),
        worst_post_entry_additional_downside_pct=None if not downside else min(downside),
        average_exit_peak_miss_pct=_mean_or_none(trade.exit_peak_miss_pct for trade in trades),
        average_exit_peak_miss_atr=_mean_or_none(trade.exit_peak_miss_atr for trade in trades),
        early_exit_cases=len(early_exit),
        late_exit_cases=len(late_exit),
        average_exit_early_bars=_mean_or_none(trade.exit_early_bars for trade in early_exit),
        average_exit_late_bars=_mean_or_none(trade.exit_late_bars for trade in late_exit),
        average_post_exit_missed_upside_pct=_mean_or_none(missed_upside),
        worst_post_exit_missed_upside_pct=None if not missed_upside else max(missed_upside),
        average_profit_giveback_pct=_mean_or_none(trade.profit_giveback_pct for trade in trades),
    )


def _episodes(mapped: list[tuple[int, DecisionEvent]], action: DecisionAction) -> list[tuple[int, int]]:
    episodes: list[tuple[int, int]] = []
    start: int | None = None
    previous: int | None = None
    for index, event in mapped:
        if event.action is action:
            if start is None or previous is None or index != previous + 1:
                if start is not None and previous is not None:
                    episodes.append((start, previous))
                start = index
            previous = index
        elif start is not None and previous is not None:
            episodes.append((start, previous))
            start = None
            previous = None
    if start is not None and previous is not None:
        episodes.append((start, previous))
    return episodes


def _signal_stability(events: tuple[DecisionEvent, ...], bars: pd.DataFrame) -> SignalStabilityAudit:
    mapped = [(_event_index(bars, event.timestamp), event) for event in events]
    counts = Counter(event.action.value for _, event in mapped)
    waits = _episodes(mapped, DecisionAction.WAIT)
    readies = _episodes(mapped, DecisionAction.READY)
    ready_to_wait = sum(
        1
        for (previous_index, previous), (index, current) in zip(mapped, mapped[1:])
        if previous.action is DecisionAction.READY
        and current.action is DecisionAction.WAIT
        and index >= previous_index
    )

    ready_to_buy_delays: list[int] = []
    ready_start: int | None = None
    for index, event in mapped:
        if event.action is DecisionAction.READY and ready_start is None:
            ready_start = index
        elif event.action is DecisionAction.BUY and ready_start is not None:
            ready_to_buy_delays.append(max(0, index - ready_start))
            ready_start = None
        elif event.action in {DecisionAction.WAIT, DecisionAction.NO_TRADE}:
            ready_start = None

    return SignalStabilityAudit(
        action_counts=dict(sorted(counts.items())),
        ready_to_wait_reversals=ready_to_wait,
        wait_episode_count=len(waits),
        ready_episode_count=len(readies),
        average_wait_duration_bars=_mean_or_none(end - start + 1 for start, end in waits),
        average_ready_duration_bars=_mean_or_none(end - start + 1 for start, end in readies),
        average_ready_to_buy_delay_bars=_mean_or_none(ready_to_buy_delays),
    )


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _lifecycle_metadata(event: DecisionEvent) -> Mapping[str, object] | None:
    return _mapping(event.snapshot.get("trade_lifecycle"))


def _long_exit_metadata(event: DecisionEvent) -> Mapping[str, object] | None:
    return _mapping(event.snapshot.get("long_exit"))


def _stage_episodes(rows: list[tuple[int, str]], stage: str) -> list[tuple[int, int]]:
    episodes: list[tuple[int, int]] = []
    start: int | None = None
    previous: int | None = None
    for index, value in rows:
        if value == stage:
            if start is None or previous is None or index != previous + 1:
                if start is not None and previous is not None:
                    episodes.append((start, previous))
                start = index
            previous = index
        elif start is not None and previous is not None:
            episodes.append((start, previous))
            start = None
            previous = None
    if start is not None and previous is not None:
        episodes.append((start, previous))
    return episodes


def _lifecycle_audit(
    events: tuple[DecisionEvent, ...],
    bars: pd.DataFrame,
    *,
    censored_count: int,
    unmatched_buy: int,
    unmatched_sell: int,
) -> LifecycleAudit:
    metadata_rows: list[tuple[int, DecisionEvent, Mapping[str, object]]] = []
    for event in events:
        metadata = _lifecycle_metadata(event)
        if metadata is not None:
            metadata_rows.append((_event_index(bars, event.timestamp), event, metadata))

    violations: list[str] = []
    if metadata_rows and len(metadata_rows) != len(events):
        violations.append("LIFECYCLE_METADATA_PARTIAL_STREAM")
    if metadata_rows and unmatched_buy:
        violations.append(f"LIFECYCLE_REPEATED_BUY_EVENTS:{unmatched_buy}")
    if metadata_rows and unmatched_sell:
        violations.append(f"LIFECYCLE_FLAT_SELL_EVENTS:{unmatched_sell}")

    previous_current: str | None = None
    previous_trade_id: object | None = None
    completed_cycles = 0
    hold_bars = 0
    protected_hold_bars = 0
    pressured_hold_bars = 0
    stage_rows: list[tuple[int, str]] = []

    for ordinal, (index, event, metadata) in enumerate(metadata_rows):
        previous_position = metadata.get("previous_position")
        current_position = metadata.get("position_state")
        snapshot_action = metadata.get("action")
        exit_stage = metadata.get("exit_stage")
        trade_id = metadata.get("trade_id")
        readiness_proxy = bool(event.snapshot.get("lifecycle_readiness_proxy"))

        if snapshot_action != event.action.value:
            violations.append(f"LIFECYCLE_ACTION_MISMATCH:{ordinal}")
        if previous_position not in {"FLAT", "OPEN"} or current_position not in {"FLAT", "OPEN"}:
            violations.append(f"LIFECYCLE_POSITION_INVALID:{ordinal}")
            continue
        if previous_current is not None and previous_position != previous_current:
            violations.append(f"LIFECYCLE_POSITION_DISCONTINUITY:{ordinal}")

        if current_position == "FLAT":
            if exit_stage is not None or trade_id is not None:
                violations.append(f"LIFECYCLE_FLAT_CARRIES_OPEN_METADATA:{ordinal}")
        else:
            if exit_stage not in _EXIT_STAGES or not trade_id:
                violations.append(f"LIFECYCLE_OPEN_METADATA_INVALID:{ordinal}")
            if previous_position == "OPEN" and previous_trade_id is not None and trade_id != previous_trade_id:
                violations.append(f"LIFECYCLE_TRADE_ID_CHANGED_WHILE_OPEN:{ordinal}")

        if event.action is DecisionAction.BUY:
            if not (previous_position == "FLAT" and current_position == "OPEN"):
                violations.append(f"LIFECYCLE_BUY_TRANSITION_INVALID:{ordinal}")
            execution = _mapping(event.snapshot.get("execution"))
            if (
                not readiness_proxy
                and execution is not None
                and execution.get("state") != "CONFIRMED"
            ):
                violations.append(f"LIFECYCLE_BUY_WITHOUT_CONFIRMED_EXECUTION:{ordinal}")
        elif event.action is DecisionAction.SELL:
            completed_cycles += 1
            if not (previous_position == "OPEN" and current_position == "FLAT"):
                violations.append(f"LIFECYCLE_SELL_TRANSITION_INVALID:{ordinal}")
            long_exit = _long_exit_metadata(event)
            exit_execution = None if long_exit is None else _mapping(long_exit.get("execution"))
            if long_exit is None or long_exit.get("stage") != "EXIT_READY":
                violations.append(f"LIFECYCLE_SELL_WITHOUT_EXIT_READY:{ordinal}")
            if (
                not readiness_proxy
                and (exit_execution is None or exit_execution.get("state") != "CONFIRMED")
            ):
                violations.append(f"LIFECYCLE_SELL_WITHOUT_CONFIRMED_EXIT_EVENT:{ordinal}")
        elif event.action is DecisionAction.HOLD:
            hold_bars += 1
            if not (previous_position == "OPEN" and current_position == "OPEN"):
                violations.append(f"LIFECYCLE_HOLD_OUTSIDE_OPEN_POSITION:{ordinal}")
        elif event.action in {DecisionAction.WAIT, DecisionAction.READY, DecisionAction.NO_TRADE}:
            if current_position == "OPEN":
                violations.append(f"LIFECYCLE_ENTRY_ACTION_LEAKED_WHILE_OPEN:{ordinal}")

        long_exit = _long_exit_metadata(event)
        if long_exit is not None:
            stage = long_exit.get("stage")
            if isinstance(stage, str) and stage in _EXIT_STAGES:
                stage_rows.append((index, stage))
            health = long_exit.get("position_health")
            if event.action is DecisionAction.HOLD and health == "PROTECTED":
                protected_hold_bars += 1
            if event.action is DecisionAction.HOLD and health == "PRESSURED":
                pressured_hold_bars += 1

        previous_current = str(current_position)
        previous_trade_id = trade_id if current_position == "OPEN" else None

    watch_episodes = _stage_episodes(stage_rows, "EXIT_WATCH")
    ready_episodes = _stage_episodes(stage_rows, "EXIT_READY")
    watch_to_monitor = sum(
        1
        for (_, previous), (_, current) in zip(stage_rows, stage_rows[1:])
        if previous == "EXIT_WATCH" and current == "MONITOR"
    )
    ready_to_watch = sum(
        1
        for (_, previous), (_, current) in zip(stage_rows, stage_rows[1:])
        if previous == "EXIT_READY" and current == "EXIT_WATCH"
    )

    ready_to_sell_delays: list[int] = []
    ready_start: int | None = None
    stage_by_index = {index: stage for index, stage in stage_rows}
    for index, event, _ in metadata_rows:
        stage = stage_by_index.get(index)
        if stage == "EXIT_READY" and ready_start is None:
            ready_start = index
        if event.action is DecisionAction.SELL and ready_start is not None:
            ready_to_sell_delays.append(max(0, index - ready_start))
            ready_start = None
        elif ready_start is not None and stage not in {None, "EXIT_READY"}:
            ready_start = None

    return LifecycleAudit(
        metadata_events=len(metadata_rows),
        lifecycle_valid=not violations,
        violations=tuple(violations),
        completed_cycles=completed_cycles,
        censored_open_trades=censored_count,
        hold_bars=hold_bars,
        protected_hold_bars=protected_hold_bars,
        pressured_hold_bars=pressured_hold_bars,
        exit_watch_episode_count=len(watch_episodes),
        exit_ready_episode_count=len(ready_episodes),
        average_exit_watch_duration_bars=_mean_or_none(end - start + 1 for start, end in watch_episodes),
        average_exit_ready_duration_bars=_mean_or_none(end - start + 1 for start, end in ready_episodes),
        average_exit_ready_to_sell_delay_bars=_mean_or_none(ready_to_sell_delays),
        exit_watch_to_monitor_reversions=watch_to_monitor,
        exit_ready_to_watch_reversions=ready_to_watch,
    )


def _meaningful_long_opportunities(
    events: tuple[DecisionEvent, ...],
    bars: pd.DataFrame,
    config: DecisionAuditConfig,
) -> tuple[MissedOpportunity, ...]:
    threshold = config.meaningful_move_atr
    if threshold is None:
        return ()

    buy_indices = [
        (_event_index(bars, event.timestamp), event)
        for event in events
        if event.action is DecisionAction.BUY
    ]
    rows: list[MissedOpportunity] = []
    radius = config.swing_radius_bars
    last_peak_index = -1

    for index in range(radius, len(bars) - radius):
        low = float(bars.iloc[index]["low"])
        local = bars.loc[index - radius:index + radius, "low"]
        if low != float(local.min()) or int(local.idxmin()) != index:
            continue
        horizon_end = min(len(bars) - 1, index + config.opportunity_horizon_bars)
        if horizon_end <= index:
            continue
        peak, peak_index = _max_with_index(bars, index + 1, horizon_end)
        if peak_index <= last_peak_index:
            continue
        atr = float(bars.iloc[index]["_audit_atr"])
        if atr <= _EPS:
            continue
        move = peak - low
        move_atr = move / atr
        if move_atr < threshold:
            continue
        move_pct = _pct(move, low) or 0.0
        capture_end = min(peak_index, index + config.capture_entry_window_bars)
        captured_event = next(
            ((buy_index, event) for buy_index, event in buy_indices if index <= buy_index <= capture_end),
            None,
        )
        nearest = min(
            ((abs(event_index - index), event_index, event) for event_index, event in buy_indices),
            default=None,
            key=lambda item: item[0],
        )
        rows.append(
            MissedOpportunity(
                side=DecisionSide.LONG,
                start_time=bars.iloc[index]["timestamp"],
                extreme_time=bars.iloc[peak_index]["timestamp"],
                end_time=bars.iloc[peak_index]["timestamp"],
                start_price=low,
                extreme_price=peak,
                move_pct=move_pct,
                move_atr=move_atr,
                captured=captured_event is not None,
                nearest_decision_action=None if nearest is None else nearest[2].action.value,
                nearest_decision_time=None if nearest is None else nearest[2].timestamp,
            )
        )
        last_peak_index = peak_index
    return tuple(rows)


def audit_decisions(
    *,
    symbol: str,
    timeframe: str,
    bars: pd.DataFrame,
    decisions: Iterable[DecisionEvent],
    config: DecisionAuditConfig | None = None,
) -> DecisionAuditReport:
    """Run causal-decision/hindsight-quality audit over one decision stream.

    Decisions are immutable causal outputs. Future bars are consulted only after the
    fact to grade timing, MAE/MFE, giveback and missed opportunities. Lifecycle
    metadata is validated rather than reconstructed when present. An entry still open
    at sample end is reported as right-censored, never as a failed/unmatched BUY.

    Audit-only readiness-proxy events remain explicitly marked in the snapshot and
    are validated against their proxy contract rather than falsely requiring a real
    fresh execution event that the proxy deliberately substitutes.
    """

    cfg = config or DecisionAuditConfig()
    prepared = _prepare_bars(bars, atr_length=cfg.atr_length)
    events = tuple(
        sorted(
            (
                replace(event, timestamp=_as_timestamp(event.timestamp))
                for event in decisions
            ),
            key=lambda item: _as_timestamp(item.timestamp),
        )
    )
    trades, censored, unmatched_buy, unmatched_sell = _pair_long_trades(
        symbol,
        events,
        prepared,
        cfg,
    )
    opportunities = _meaningful_long_opportunities(events, prepared, cfg)
    lifecycle = _lifecycle_audit(
        events,
        prepared,
        censored_count=len(censored),
        unmatched_buy=unmatched_buy,
        unmatched_sell=unmatched_sell,
    )
    return DecisionAuditReport(
        symbol=symbol,
        timeframe=timeframe,
        start_time=None if prepared.empty else prepared.iloc[0]["timestamp"],
        end_time=None if prepared.empty else prepared.iloc[-1]["timestamp"],
        metrics=_aggregate(trades),
        signal_stability=_signal_stability(events, prepared),
        lifecycle=lifecycle,
        trades=trades,
        censored_trades=censored,
        missed_opportunities=opportunities,
        unmatched_buy_events=unmatched_buy,
        unmatched_sell_events=unmatched_sell,
    )


__all__ = ["audit_decisions"]
