from __future__ import annotations

from dataclasses import dataclass, field
from math import prod
from statistics import mean, median
from typing import Any, Iterable, Mapping

import pandas as pd

from .models import DecisionAction, DecisionEvent

_REQUIRED_BAR_COLUMNS = {"timestamp", "high", "low", "close"}
_EPS = 1e-12


@dataclass(frozen=True, slots=True)
class TradeQualityAuditConfig:
    """Hindsight-only horizon-aware grading windows.

    These settings never enter DecisionInput, scenario, entry, exit, or lifecycle
    code. SHORT_TERM deliberately uses a tighter local-extrema window; LONG_TERM gets
    a wider tolerance. Unknown/legacy horizon falls back to the neutral window.
    """

    short_lookback_bars: int = 6
    short_lookahead_bars: int = 6
    long_lookback_bars: int = 20
    long_lookahead_bars: int = 20
    fallback_lookback_bars: int = 10
    fallback_lookahead_bars: int = 10
    atr_length: int = 14
    short_research_targets_pct: tuple[float, ...] = (3.0, 5.0)

    def __post_init__(self) -> None:
        for name in (
            "short_lookback_bars",
            "short_lookahead_bars",
            "long_lookback_bars",
            "long_lookahead_bars",
            "fallback_lookback_bars",
            "fallback_lookahead_bars",
            "atr_length",
        ):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be >= 1")
        if self.long_lookback_bars < self.short_lookback_bars:
            raise ValueError("LONG_TERM lookback must be at least as wide as SHORT_TERM")
        if self.long_lookahead_bars < self.short_lookahead_bars:
            raise ValueError("LONG_TERM lookahead must be at least as wide as SHORT_TERM")
        targets = tuple(float(value) for value in self.short_research_targets_pct)
        if not targets or any(value <= 0.0 for value in targets):
            raise ValueError("short research targets must be positive and non-empty")
        if tuple(sorted(set(targets))) != targets:
            raise ValueError("short research targets must be sorted and unique")


@dataclass(frozen=True, slots=True)
class ShortTargetHit:
    target_pct: float
    reached: bool
    bars_to_reach: int | None


@dataclass(frozen=True, slots=True)
class HorizonTradeQuality:
    symbol: str
    horizon: str
    scenario_kind: str
    entry_time: Any
    exit_time: Any
    entry_price: float
    exit_price: float
    audit_lookback_bars: int
    audit_lookahead_bars: int
    return_pct: float
    mfe_pct: float
    mae_pct: float
    move_capture_ratio: float | None
    entry_local_low: float
    entry_local_low_miss_pct: float | None
    entry_local_low_miss_atr: float | None
    entry_early_bars: int
    entry_late_bars: int
    post_entry_low: float
    post_entry_additional_downside_pct: float | None
    scenario_qualified_time: Any | None
    scenario_qualified_price: float | None
    scenario_to_buy_bars: int | None
    scenario_to_buy_price_change_pct: float | None
    ready_time: Any | None
    ready_price: float | None
    ready_to_buy_bars: int | None
    ready_to_buy_price_change_pct: float | None
    exit_local_high: float
    exit_peak_miss_pct: float | None
    exit_peak_miss_atr: float | None
    exit_early_bars: int
    exit_late_bars: int
    post_exit_high: float | None
    post_exit_missed_upside_pct: float | None
    profit_giveback_pct: float | None
    exit_watch_time: Any | None
    exit_watch_price: float | None
    exit_watch_to_sell_bars: int | None
    exit_ready_time: Any | None
    exit_ready_price: float | None
    exit_ready_to_sell_bars: int | None
    exit_ready_to_sell_giveback_pct: float | None
    target_defended_count: int
    target_cleared_count: int
    first_target_defended_time: Any | None
    target_defended_to_sell_bars: int | None
    short_target_hits: tuple[ShortTargetHit, ...] = ()


@dataclass(frozen=True, slots=True)
class TradeQualityAggregate:
    trade_count: int
    wins: int
    losses: int
    win_rate_pct: float | None
    average_return_pct: float | None
    median_return_pct: float | None
    compounded_return_pct: float | None
    average_mfe_pct: float | None
    average_mae_pct: float | None
    average_move_capture_ratio_pct: float | None
    average_entry_local_low_miss_pct: float | None
    average_scenario_to_buy_bars: float | None
    average_scenario_to_buy_price_change_pct: float | None
    average_ready_to_buy_bars: float | None
    average_ready_to_buy_price_change_pct: float | None
    average_exit_peak_miss_pct: float | None
    average_exit_ready_to_sell_bars: float | None
    average_exit_ready_to_sell_giveback_pct: float | None
    average_profit_giveback_pct: float | None
    average_target_defended_to_sell_bars: float | None
    average_target_cleared_count: float | None
    short_target_reach_rate_pct: Mapping[str, float | None] = field(default_factory=dict)
    average_short_bars_to_target: Mapping[str, float | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HorizonAwareTradeQualityReport:
    symbol: str
    timeframe: str
    trades: tuple[HorizonTradeQuality, ...]
    metrics: TradeQualityAggregate
    metrics_by_horizon: Mapping[str, TradeQualityAggregate]
    metrics_by_scenario: Mapping[str, TradeQualityAggregate]
    censored_open_trades: int
    unmatched_buy_events: int
    unmatched_sell_events: int


def _pct(numerator: float, denominator: float) -> float | None:
    if abs(float(denominator)) <= _EPS:
        return None
    return float(numerator) / float(denominator) * 100.0


def _mean_or_none(values: Iterable[float | int | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return None if not clean else mean(clean)


def _prepare_bars(frame: pd.DataFrame, atr_length: int) -> pd.DataFrame:
    missing = _REQUIRED_BAR_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(f"trade quality bars missing columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError("trade quality audit requires bars")
    bars = frame.copy(deep=True)
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], errors="raise")
    bars = bars.sort_values("timestamp", kind="stable").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    for column in ("high", "low", "close"):
        bars[column] = pd.to_numeric(bars[column], errors="raise").astype(float)
    if (bars["high"] < bars["low"]).any():
        raise ValueError("trade quality bars contain high < low")
    prev_close = bars["close"].shift(1)
    tr = pd.concat(
        (
            bars["high"] - bars["low"],
            (bars["high"] - prev_close).abs(),
            (bars["low"] - prev_close).abs(),
        ),
        axis=1,
    ).max(axis=1)
    bars["_audit_atr"] = tr.rolling(atr_length, min_periods=1).mean()
    return bars


def _event_index(bars: pd.DataFrame, timestamp: Any) -> int:
    target = pd.Timestamp(timestamp)
    pos = int(bars["timestamp"].searchsorted(target, side="right")) - 1
    if pos < 0:
        raise ValueError("decision event occurs before first quality-audit bar")
    return pos


def _event_price(event: DecisionEvent, bars: pd.DataFrame, index: int) -> float:
    return float(event.price) if event.price is not None else float(bars.iloc[index]["close"])


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _entry_horizon(event: DecisionEvent) -> str:
    value = event.snapshot.get("entry_horizon")
    if isinstance(value, str) and value:
        return value
    metadata = _mapping(event.snapshot.get("entry_metadata"))
    if metadata is not None and isinstance(metadata.get("entry_horizon"), str):
        return str(metadata["entry_horizon"])
    return "UNKNOWN"


def _scenario_kind(event: DecisionEvent) -> str:
    value = event.snapshot.get("scenario_kind")
    return str(value) if isinstance(value, str) and value else "UNKNOWN"


def _window_for(horizon: str, config: TradeQualityAuditConfig) -> tuple[int, int]:
    if horizon == "SHORT_TERM":
        return config.short_lookback_bars, config.short_lookahead_bars
    if horizon == "LONG_TERM":
        return config.long_lookback_bars, config.long_lookahead_bars
    return config.fallback_lookback_bars, config.fallback_lookahead_bars


def _marker(event: DecisionEvent, name: str) -> Any | None:
    markers = _mapping(event.snapshot.get("audit_markers"))
    return None if markers is None else markers.get(name)


def _marker_delay(
    event: DecisionEvent,
    *,
    time_name: str,
    price_name: str,
    event_index: int,
    event_price: float,
    bars: pd.DataFrame,
) -> tuple[Any | None, float | None, int | None, float | None]:
    marker_time = _marker(event, time_name)
    marker_price = _marker(event, price_name)
    if marker_time is None:
        return None, None, None, None
    marker_index = _event_index(bars, marker_time)
    price = None if marker_price is None else float(marker_price)
    change = None if price is None else _pct(event_price - price, price)
    return marker_time, price, max(0, event_index - marker_index), change


def _target_progression(events: tuple[DecisionEvent, ...], bars: pd.DataFrame, sell_index: int):
    defended: dict[str, Any] = {}
    cleared: set[str] = set()
    for event in events:
        path = _mapping(event.snapshot.get("target_path"))
        if path is None:
            continue
        nodes = path.get("nodes")
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            if not isinstance(node, Mapping):
                continue
            identity = str(node.get("identity", ""))
            state = str(node.get("state", ""))
            if not identity:
                continue
            if state == "DEFENDED" and identity not in defended:
                defended[identity] = event.timestamp
            elif state == "CLEARED":
                cleared.add(identity)
    first_time = min(defended.values(), default=None, key=pd.Timestamp)
    delay = None if first_time is None else max(0, sell_index - _event_index(bars, first_time))
    return len(defended), len(cleared), first_time, delay


def _short_hits(
    bars: pd.DataFrame,
    entry_index: int,
    exit_index: int,
    entry_price: float,
    targets: tuple[float, ...],
) -> tuple[ShortTargetHit, ...]:
    rows: list[ShortTargetHit] = []
    for target in targets:
        threshold = entry_price * (1.0 + target / 100.0)
        reached_index = None
        for index in range(entry_index, exit_index + 1):
            if float(bars.iloc[index]["high"]) >= threshold:
                reached_index = index
                break
        rows.append(
            ShortTargetHit(
                target_pct=float(target),
                reached=reached_index is not None,
                bars_to_reach=None if reached_index is None else max(0, reached_index - entry_index),
            )
        )
    return tuple(rows)


def _audit_trade(
    symbol: str,
    entry_event: DecisionEvent,
    exit_event: DecisionEvent,
    entry_index: int,
    exit_index: int,
    trade_events: tuple[DecisionEvent, ...],
    bars: pd.DataFrame,
    config: TradeQualityAuditConfig,
) -> HorizonTradeQuality:
    horizon = _entry_horizon(entry_event)
    scenario = _scenario_kind(entry_event)
    lookback, lookahead = _window_for(horizon, config)
    entry_price = _event_price(entry_event, bars, entry_index)
    exit_price = _event_price(exit_event, bars, exit_index)
    entry_atr = float(bars.iloc[entry_index]["_audit_atr"])
    exit_atr = float(bars.iloc[exit_index]["_audit_atr"])

    trade_slice = bars.loc[entry_index:exit_index]
    peak = float(trade_slice["high"].max())
    trough = float(trade_slice["low"].min())
    return_pct = _pct(exit_price - entry_price, entry_price) or 0.0
    mfe = _pct(peak - entry_price, entry_price) or 0.0
    mae = _pct(trough - entry_price, entry_price) or 0.0
    capture = None if mfe <= _EPS else return_pct / mfe

    entry_start = max(0, entry_index - lookback)
    entry_end = min(len(bars) - 1, entry_index + lookahead)
    entry_series = bars.loc[entry_start:entry_end, "low"]
    local_low_index = int(entry_series.idxmin())
    local_low = float(entry_series.loc[local_low_index])
    miss_distance = max(0.0, entry_price - local_low)
    miss_pct = _pct(miss_distance, local_low)
    miss_atr = None if entry_atr <= _EPS else miss_distance / entry_atr
    offset = local_low_index - entry_index

    future_end = min(len(bars) - 1, entry_index + lookahead)
    post_low = float(bars.loc[entry_index:future_end, "low"].min())
    additional_downside = _pct(min(0.0, post_low - entry_price), entry_price)

    exit_start = max(0, exit_index - lookback)
    exit_end = min(len(bars) - 1, exit_index + lookahead)
    exit_series = bars.loc[exit_start:exit_end, "high"]
    local_high_index = int(exit_series.idxmax())
    local_high = float(exit_series.loc[local_high_index])
    exit_miss_distance = max(0.0, local_high - exit_price)
    exit_miss_pct = _pct(exit_miss_distance, local_high)
    exit_miss_atr = None if exit_atr <= _EPS else exit_miss_distance / exit_atr
    exit_offset = local_high_index - exit_index

    post_high = None
    if exit_index + 1 < len(bars):
        post_end = min(len(bars) - 1, exit_index + lookahead)
        if post_end >= exit_index + 1:
            post_high = float(bars.loc[exit_index + 1:post_end, "high"].max())
    missed_upside = None if post_high is None else _pct(max(0.0, post_high - exit_price), exit_price)
    giveback = _pct(max(0.0, peak - exit_price), peak)

    q_time, q_price, q_bars, q_change = _marker_delay(
        entry_event,
        time_name="scenario_qualified_at",
        price_name="scenario_qualified_price",
        event_index=entry_index,
        event_price=entry_price,
        bars=bars,
    )
    ready_time, ready_price, ready_bars, ready_change = _marker_delay(
        entry_event,
        time_name="ready_for_execution_at",
        price_name="ready_for_execution_price",
        event_index=entry_index,
        event_price=entry_price,
        bars=bars,
    )

    watch_time = _marker(exit_event, "exit_watch_at")
    watch_price_raw = _marker(exit_event, "exit_watch_price")
    watch_price = None if watch_price_raw is None else float(watch_price_raw)
    watch_bars = None if watch_time is None else max(0, exit_index - _event_index(bars, watch_time))
    ready_exit_time = _marker(exit_event, "exit_ready_at")
    ready_exit_price_raw = _marker(exit_event, "exit_ready_price")
    ready_exit_price = None if ready_exit_price_raw is None else float(ready_exit_price_raw)
    ready_exit_bars = None if ready_exit_time is None else max(0, exit_index - _event_index(bars, ready_exit_time))
    ready_exit_giveback = None if ready_exit_price is None else _pct(max(0.0, ready_exit_price - exit_price), ready_exit_price)

    defended_count, cleared_count, first_defended, defended_delay = _target_progression(
        trade_events, bars, exit_index
    )

    short_hits = ()
    if horizon == "SHORT_TERM":
        short_hits = _short_hits(
            bars,
            entry_index,
            exit_index,
            entry_price,
            config.short_research_targets_pct,
        )

    return HorizonTradeQuality(
        symbol=symbol,
        horizon=horizon,
        scenario_kind=scenario,
        entry_time=entry_event.timestamp,
        exit_time=exit_event.timestamp,
        entry_price=entry_price,
        exit_price=exit_price,
        audit_lookback_bars=lookback,
        audit_lookahead_bars=lookahead,
        return_pct=return_pct,
        mfe_pct=mfe,
        mae_pct=mae,
        move_capture_ratio=capture,
        entry_local_low=local_low,
        entry_local_low_miss_pct=miss_pct,
        entry_local_low_miss_atr=miss_atr,
        entry_early_bars=max(0, offset),
        entry_late_bars=max(0, -offset),
        post_entry_low=post_low,
        post_entry_additional_downside_pct=additional_downside,
        scenario_qualified_time=q_time,
        scenario_qualified_price=q_price,
        scenario_to_buy_bars=q_bars,
        scenario_to_buy_price_change_pct=q_change,
        ready_time=ready_time,
        ready_price=ready_price,
        ready_to_buy_bars=ready_bars,
        ready_to_buy_price_change_pct=ready_change,
        exit_local_high=local_high,
        exit_peak_miss_pct=exit_miss_pct,
        exit_peak_miss_atr=exit_miss_atr,
        exit_early_bars=max(0, exit_offset),
        exit_late_bars=max(0, -exit_offset),
        post_exit_high=post_high,
        post_exit_missed_upside_pct=missed_upside,
        profit_giveback_pct=giveback,
        exit_watch_time=watch_time,
        exit_watch_price=watch_price,
        exit_watch_to_sell_bars=watch_bars,
        exit_ready_time=ready_exit_time,
        exit_ready_price=ready_exit_price,
        exit_ready_to_sell_bars=ready_exit_bars,
        exit_ready_to_sell_giveback_pct=ready_exit_giveback,
        target_defended_count=defended_count,
        target_cleared_count=cleared_count,
        first_target_defended_time=first_defended,
        target_defended_to_sell_bars=defended_delay,
        short_target_hits=short_hits,
    )


def _aggregate(trades: tuple[HorizonTradeQuality, ...]) -> TradeQualityAggregate:
    returns = [trade.return_pct for trade in trades]
    wins = [value for value in returns if value > _EPS]
    losses = [value for value in returns if value < -_EPS]
    compounded = None if not returns else (prod(1.0 + value / 100.0 for value in returns) - 1.0) * 100.0
    target_names = sorted({f"{hit.target_pct:g}%" for trade in trades for hit in trade.short_target_hits})
    reach_rates: dict[str, float | None] = {}
    bars_to: dict[str, float | None] = {}
    for name in target_names:
        target_hits = [
            hit
            for trade in trades
            for hit in trade.short_target_hits
            if f"{hit.target_pct:g}%" == name
        ]
        reach_rates[name] = None if not target_hits else sum(hit.reached for hit in target_hits) / len(target_hits) * 100.0
        bars_to[name] = _mean_or_none(hit.bars_to_reach for hit in target_hits if hit.reached)
    return TradeQualityAggregate(
        trade_count=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate_pct=None if not trades else len(wins) / len(trades) * 100.0,
        average_return_pct=_mean_or_none(returns),
        median_return_pct=None if not returns else median(returns),
        compounded_return_pct=compounded,
        average_mfe_pct=_mean_or_none(trade.mfe_pct for trade in trades),
        average_mae_pct=_mean_or_none(trade.mae_pct for trade in trades),
        average_move_capture_ratio_pct=_mean_or_none(
            None if trade.move_capture_ratio is None else trade.move_capture_ratio * 100.0 for trade in trades
        ),
        average_entry_local_low_miss_pct=_mean_or_none(trade.entry_local_low_miss_pct for trade in trades),
        average_scenario_to_buy_bars=_mean_or_none(trade.scenario_to_buy_bars for trade in trades),
        average_scenario_to_buy_price_change_pct=_mean_or_none(trade.scenario_to_buy_price_change_pct for trade in trades),
        average_ready_to_buy_bars=_mean_or_none(trade.ready_to_buy_bars for trade in trades),
        average_ready_to_buy_price_change_pct=_mean_or_none(trade.ready_to_buy_price_change_pct for trade in trades),
        average_exit_peak_miss_pct=_mean_or_none(trade.exit_peak_miss_pct for trade in trades),
        average_exit_ready_to_sell_bars=_mean_or_none(trade.exit_ready_to_sell_bars for trade in trades),
        average_exit_ready_to_sell_giveback_pct=_mean_or_none(trade.exit_ready_to_sell_giveback_pct for trade in trades),
        average_profit_giveback_pct=_mean_or_none(trade.profit_giveback_pct for trade in trades),
        average_target_defended_to_sell_bars=_mean_or_none(trade.target_defended_to_sell_bars for trade in trades),
        average_target_cleared_count=_mean_or_none(trade.target_cleared_count for trade in trades),
        short_target_reach_rate_pct=reach_rates,
        average_short_bars_to_target=bars_to,
    )


def audit_trade_quality(
    *,
    symbol: str,
    timeframe: str,
    bars: pd.DataFrame,
    decisions: Iterable[DecisionEvent],
    config: TradeQualityAuditConfig | None = None,
) -> HorizonAwareTradeQualityReport:
    """Grade completed causal trades with bounded, horizon-specific hindsight.

    This function is intentionally downstream of immutable DecisionEvents. It has no
    import or callback into the decision engine and cannot alter any historical or
    live BUY/SELL result.
    """

    cfg = config or TradeQualityAuditConfig()
    prepared = _prepare_bars(bars, cfg.atr_length)
    events = tuple(sorted(decisions, key=lambda event: pd.Timestamp(event.timestamp)))
    open_entry: tuple[int, DecisionEvent, int] | None = None
    trades: list[HorizonTradeQuality] = []
    unmatched_buy = 0
    unmatched_sell = 0

    for ordinal, event in enumerate(events):
        index = _event_index(prepared, event.timestamp)
        if event.action is DecisionAction.BUY:
            if open_entry is None:
                open_entry = (ordinal, event, index)
            else:
                unmatched_buy += 1
        elif event.action is DecisionAction.SELL:
            if open_entry is None:
                unmatched_sell += 1
                continue
            entry_ordinal, entry_event, entry_index = open_entry
            trades.append(
                _audit_trade(
                    symbol,
                    entry_event,
                    event,
                    entry_index,
                    index,
                    events[entry_ordinal : ordinal + 1],
                    prepared,
                    cfg,
                )
            )
            open_entry = None

    values = tuple(trades)
    by_horizon = {
        horizon: _aggregate(tuple(trade for trade in values if trade.horizon == horizon))
        for horizon in sorted({trade.horizon for trade in values})
    }
    by_scenario = {
        scenario: _aggregate(tuple(trade for trade in values if trade.scenario_kind == scenario))
        for scenario in sorted({trade.scenario_kind for trade in values})
    }
    return HorizonAwareTradeQualityReport(
        symbol=symbol,
        timeframe=timeframe,
        trades=values,
        metrics=_aggregate(values),
        metrics_by_horizon=by_horizon,
        metrics_by_scenario=by_scenario,
        censored_open_trades=1 if open_entry is not None else 0,
        unmatched_buy_events=unmatched_buy,
        unmatched_sell_events=unmatched_sell,
    )


__all__ = [
    "HorizonAwareTradeQualityReport",
    "HorizonTradeQuality",
    "ShortTargetHit",
    "TradeQualityAggregate",
    "TradeQualityAuditConfig",
    "audit_trade_quality",
]
