from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from math import ceil, isfinite
from typing import Any

import numpy as np
import pandas as pd


class TrendReason(IntEnum):
    CONFIRMED = 0
    DATA_WAIT = 1
    NET_MOVE_LOW = 2
    CONSISTENCY_LOW = 3
    SLOPE_LOW = 4
    RECENT_CONFLICT = 5
    SPIKE_PENDING = 6
    DIRECTION_HELD = 7
    MIXED = 8
    DIRECTION_LOST = 9


class VolumeQuality(IntEnum):
    WAITING = -1
    MISSING = 0
    LIMITED = 1
    ADEQUATE = 2


class RawDataQuality(StrEnum):
    OK = "OK"
    WARMUP = "WARMUP"
    INCOMPLETE_BAR = "INCOMPLETE_BAR"
    SOURCE_GAP = "SOURCE_GAP"


class TrendProfile(StrEnum):
    MANUAL = "manual"
    XAG_30M = "30m"
    XAG_1H = "1h"
    XAG_2H = "2h"
    XAG_4H = "4h"
    XAG_1D = "1d"


@dataclass(frozen=True, slots=True)
class RawIndicatorConfig:
    profile: TrendProfile = TrendProfile.MANUAL
    trend_lookback: int = 5
    recent_lookback: int = 3
    minimum_consistency: float = 60.0
    step_dead_zone_percent: float = 35.0
    hysteresis_hold_percent: float = 65.0
    use_spike_filter: bool = True
    spike_dominance: float = 70.0
    dynamic_threshold_length: int = 20
    dynamic_step_cap_multiplier: float = 3.0

    volume_quality_length: int = 50
    minimum_volume_coverage: float = 85.0
    minimum_calculable_volume_coverage: float = 25.0
    minimum_volume_variation: float = 20.0
    minimum_meaningful_volume_change: float = 1.0
    limited_volume_evidence_weight: float = 0.50

    cmf_length: int = 20
    cmf_minimum_move: float = 0.020
    obv_dynamic_multiplier: float = 0.35
    cci_length: int = 20
    cci_source: str = "hlc3"
    cci_minimum_move: float = 10.0
    rsi_length: int = 14
    rsi_source: str = "close"
    rsi_minimum_move: float = 1.0
    macd_fast_length: int = 12
    macd_slow_length: int = 26
    macd_signal_length: int = 9
    macd_source: str = "close"
    macd_displayed_value: str = "MACD"
    macd_dynamic_multiplier: float = 0.35
    momentum_length: int = 10
    momentum_source: str = "close"
    momentum_dynamic_multiplier: float = 0.35
    atr_length: int = 14
    stochastic_length: int = 14
    stochastic_k_smoothing: int = 3
    stochastic_d_smoothing: int = 3
    stochastic_displayed_value: str = "%K"
    stochastic_minimum_move: float = 3.0
    stoch_rsi_rsi_length: int = 14
    stoch_rsi_length: int = 14
    stoch_rsi_k_smoothing: int = 3
    stoch_rsi_d_smoothing: int = 3
    stoch_rsi_source: str = "close"
    stoch_rsi_displayed_value: str = "%K"
    stoch_rsi_minimum_move: float = 3.0
    smi_length: int = 14
    smi_first_smoothing: int = 3
    smi_second_smoothing: int = 3
    smi_signal_length: int = 3
    smi_displayed_value: str = "Ana SMI"
    smi_minimum_move: float = 3.0

    context_fast_ema_length: int = 8
    context_slow_ema_length: int = 21
    context_slope_length: int = 5
    minimum_tick: float = 0.01
    pending_evidence_weight: float = 0.40

    weak_evidence_threshold: float = 0.15
    medium_evidence_threshold: float = 0.35
    strong_evidence_threshold: float = 0.60

    def __post_init__(self) -> None:
        if self.trend_lookback < 3:
            raise ValueError("trend_lookback must be >= 3")
        if self.recent_lookback < 1:
            raise ValueError("recent_lookback must be >= 1")
        if self.minimum_tick <= 0.0:
            raise ValueError("minimum_tick must be positive")
        if self.macd_displayed_value not in {"MACD", "Sinyal", "Histogram"}:
            raise ValueError("invalid macd_displayed_value")
        if self.stochastic_displayed_value not in {"%K", "%D"}:
            raise ValueError("invalid stochastic_displayed_value")
        if self.stoch_rsi_displayed_value not in {"%K", "%D"}:
            raise ValueError("invalid stoch_rsi_displayed_value")
        if self.smi_displayed_value not in {"Ana SMI", "Sinyal"}:
            raise ValueError("invalid smi_displayed_value")
        for name in (self.cci_source, self.rsi_source, self.macd_source, self.momentum_source, self.stoch_rsi_source):
            if name not in {"open", "high", "low", "close", "hl2", "hlc3", "ohlc4"}:
                raise ValueError(f"unsupported source: {name}")


@dataclass(frozen=True, slots=True)
class EffectiveTrendSettings:
    lookback: int
    recent_lookback: int
    minimum_consistency: float
    step_dead_zone_percent: float
    hysteresis_hold_percent: float
    spike_dominance: float
    dynamic_threshold_length: int
    dynamic_step_cap_multiplier: float


@dataclass(frozen=True, slots=True)
class IndicatorEvidence:
    value: float | None
    valid: bool
    direction: int
    pending_direction: int
    reason: TrendReason
    consistency: float | None
    movement_strength: float
    signed_zone: float
    evidence: float | None
    relative_evidence: float | None


@dataclass(frozen=True, slots=True)
class RawIndicatorSnapshot:
    timestamp: Any | None = None
    data_quality: RawDataQuality = RawDataQuality.WARMUP
    volume_quality: VolumeQuality = VolumeQuality.WAITING
    volume_coverage: float | None = None
    volume_variation: float | None = None
    volume_calculable: bool = False
    volume_reliable: bool = False
    volume_trust: float = 0.0
    atr: float | None = None
    atr_ratio: float | None = None
    price_context: float | None = None
    price_context_valid: bool = False
    indicators: dict[str, IndicatorEvidence] = field(default_factory=dict)
    valid_evidence_count: int = 0
    up_evidence_count: int = 0
    down_evidence_count: int = 0
    strong_up_count: int = 0
    strong_down_count: int = 0
    net_evidence_score: float | None = None


@dataclass(frozen=True, slots=True)
class _TrendResult:
    direction: int
    reason: TrendReason
    pending: int
    consistency: float | None


def _finite(value: Any) -> bool:
    try:
        return value is not None and isfinite(float(value))
    except (TypeError, ValueError):
        return False


class _EmaState:
    """Incremental mirror of _ema: identical arithmetic, O(1) per bar."""

    __slots__ = ("alpha", "prev")

    def __init__(self, length: int) -> None:
        self.alpha = 2.0 / (length + 1.0)
        self.prev: float | None = None

    def next(self, raw: float) -> float:
        if not isfinite(raw):
            return float("nan")
        self.prev = float(raw) if self.prev is None else self.alpha * float(raw) + (1.0 - self.alpha) * self.prev
        return self.prev


class _RmaState:
    """Incremental mirror of _rma: seeds on the first full finite window."""

    __slots__ = ("length", "alpha", "prev", "values")

    def __init__(self, length: int) -> None:
        self.length = length
        self.alpha = 1.0 / float(length)
        self.prev: float | None = None
        self.values: list[float] = []

    def next(self, raw: float) -> float:
        i = len(self.values)
        self.values.append(raw)
        if self.prev is None:
            if i < self.length - 1:
                return float("nan")
            window = np.asarray(self.values[i - self.length + 1 : i + 1], dtype=float)
            if not np.isfinite(window).all():
                return float("nan")
            self.prev = float(window.mean())
            return self.prev
        if not isfinite(raw):
            return float("nan")
        self.prev = self.alpha * float(raw) + (1.0 - self.alpha) * self.prev
        return self.prev


class _RsiState:
    """Incremental mirror of _rsi over a growing source series."""

    __slots__ = ("gain", "loss", "prev_source")

    def __init__(self, length: int) -> None:
        self.gain = _RmaState(length)
        self.loss = _RmaState(length)
        self.prev_source: float | None = None

    def next(self, source: float) -> float:
        if self.prev_source is None:
            # The first bar has no change; like _rsi it feeds nothing to the RMAs.
            self.prev_source = source
            return float("nan")
        change = source - self.prev_source
        self.prev_source = source
        if not isfinite(change):
            gain = loss = float("nan")
        else:
            gain = change if change > 0.0 else 0.0
            loss = -change if change < 0.0 else 0.0
        avg_gain = self.gain.next(gain)
        avg_loss = self.loss.next(loss)
        if not (isfinite(avg_gain) and isfinite(avg_loss)):
            return float("nan")
        if avg_loss == 0.0:
            return 100.0 if avg_gain > 0.0 else 50.0
        rs = avg_gain / avg_loss
        return 100.0 - 100.0 / (1.0 + rs)


class _DynamicThresholdState:
    """Incremental mirror of _dynamic_threshold for one source series."""

    __slots__ = ("step_ema", "robust_ema", "scale_ema", "prev_source", "prev_raw_avg")

    def __init__(self, average_length: int, *, cumulative: bool) -> None:
        self.step_ema = _EmaState(average_length)
        self.robust_ema = _EmaState(average_length)
        self.scale_ema = None if cumulative else _EmaState(average_length)
        self.prev_source = float("nan")
        self.prev_raw_avg = float("nan")

    def next(
        self,
        source: float,
        *,
        lookback: int,
        multiplier: float,
        cap_multiplier: float,
        cumulative: bool,
    ) -> float:
        step = abs(source - self.prev_source) if isfinite(self.prev_source) and isfinite(source) else float("nan")
        self.prev_source = source
        raw_avg = self.step_ema.next(step)
        previous = self.prev_raw_avg
        self.prev_raw_avg = raw_avg
        cap_base = previous if isfinite(previous) and previous > 0.0 else raw_avg
        if not isfinite(step):
            capped = float("nan")
        elif isfinite(cap_base) and cap_base > 0.0:
            capped = min(step, cap_base * cap_multiplier)
        else:
            capped = step
        robust = self.robust_ema.next(capped)
        if not isfinite(robust):
            return float("nan")
        threshold = robust * float(lookback) * multiplier
        if cumulative:
            floor = 1e-10
        else:
            scale = self.scale_ema.next(abs(source))
            floor = scale * 1e-6 if scale is not None and isfinite(scale) else 0.0
        return max(threshold, floor)


def _window(values: list[float], length: int) -> np.ndarray | None:
    i = len(values) - 1
    if i < length - 1:
        return None
    return np.asarray(values[i - length + 1 : i + 1], dtype=float)


def _window_sma(values: list[float], length: int) -> float:
    window = _window(values, length)
    if window is None or not np.isfinite(window).all():
        return float("nan")
    return float(window.mean())


def _window_sum(values: list[float], length: int) -> float:
    window = _window(values, length)
    if window is None or not np.isfinite(window).all():
        return float("nan")
    return float(window.sum())


def _window_min(values: list[float], length: int) -> float:
    window = _window(values, length)
    if window is None or not np.isfinite(window).all():
        return float("nan")
    return float(window.min())


def _window_max(values: list[float], length: int) -> float:
    window = _window(values, length)
    if window is None or not np.isfinite(window).all():
        return float("nan")
    return float(window.max())


def _opt(value: Any) -> float | None:
    return float(value) if _finite(value) else None


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _safe_divide(numerator: np.ndarray, denominator: np.ndarray, mask: np.ndarray) -> np.ndarray:
    out = np.full(len(numerator), np.nan, dtype=float)
    np.divide(numerator, denominator, out=out, where=mask)
    return out


def _sma(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=float)
    if length <= 0:
        return out
    for i in range(length - 1, len(values)):
        window = values[i - length + 1 : i + 1]
        if np.isfinite(window).all():
            out[i] = float(window.mean())
    return out


def _sum(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=float)
    if length <= 0:
        return out
    for i in range(length - 1, len(values)):
        window = values[i - length + 1 : i + 1]
        if np.isfinite(window).all():
            out[i] = float(window.sum())
    return out


def _ema(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    prev = np.nan
    for i, raw in enumerate(values):
        if not np.isfinite(raw):
            continue
        prev = float(raw) if not np.isfinite(prev) else alpha * float(raw) + (1.0 - alpha) * prev
        out[i] = prev
    return out


def _rma(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=float)
    if length <= 0:
        return out
    seed_index: int | None = None
    for i in range(length - 1, len(values)):
        window = values[i - length + 1 : i + 1]
        if np.isfinite(window).all():
            seed_index = i
            out[i] = float(window.mean())
            break
    if seed_index is None:
        return out
    prev = out[seed_index]
    alpha = 1.0 / float(length)
    for i in range(seed_index + 1, len(values)):
        raw = values[i]
        if not np.isfinite(raw):
            continue
        prev = alpha * float(raw) + (1.0 - alpha) * prev
        out[i] = prev
    return out


def _rolling_min(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=float)
    for i in range(length - 1, len(values)):
        window = values[i - length + 1 : i + 1]
        if np.isfinite(window).all():
            out[i] = float(window.min())
    return out


def _rolling_max(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=float)
    for i in range(length - 1, len(values)):
        window = values[i - length + 1 : i + 1]
        if np.isfinite(window).all():
            out[i] = float(window.max())
    return out


def _rsi(values: np.ndarray, length: int) -> np.ndarray:
    changes = np.full(len(values), np.nan, dtype=float)
    if len(values) > 1:
        changes[1:] = values[1:] - values[:-1]
    gains = np.where(np.isnan(changes), np.nan, np.maximum(changes, 0.0))
    losses = np.where(np.isnan(changes), np.nan, np.maximum(-changes, 0.0))
    avg_gain = _rma(gains[1:], length)
    avg_loss = _rma(losses[1:], length)
    out = np.full(len(values), np.nan, dtype=float)
    for j in range(len(avg_gain)):
        i = j + 1
        if not np.isfinite(avg_gain[j]) or not np.isfinite(avg_loss[j]):
            continue
        if avg_loss[j] == 0.0:
            out[i] = 100.0 if avg_gain[j] > 0.0 else 50.0
        else:
            rs = avg_gain[j] / avg_loss[j]
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def _true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    out = np.full(len(close), np.nan, dtype=float)
    for i in range(len(close)):
        if not (np.isfinite(high[i]) and np.isfinite(low[i])):
            continue
        out[i] = high[i] - low[i] if i == 0 or not np.isfinite(close[i - 1]) else max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    return out


def _cci(source: np.ndarray, length: int) -> np.ndarray:
    basis = _sma(source, length)
    out = np.full(len(source), np.nan, dtype=float)
    for i in range(length - 1, len(source)):
        window = source[i - length + 1 : i + 1]
        if not np.isfinite(window).all() or not np.isfinite(basis[i]):
            continue
        mean_dev = float(np.mean(np.abs(window - basis[i])))
        if mean_dev != 0.0:
            out[i] = (source[i] - basis[i]) / (0.015 * mean_dev)
    return out


def _dynamic_threshold(source: np.ndarray, average_length: int, lookback: int, multiplier: float, cap_multiplier: float, *, cumulative: bool) -> np.ndarray:
    step = np.full(len(source), np.nan, dtype=float)
    if len(source) > 1:
        step[1:] = np.abs(source[1:] - source[:-1])
    raw_avg = _ema(step, average_length)
    capped = np.full(len(source), np.nan, dtype=float)
    for i in range(len(source)):
        if not np.isfinite(step[i]):
            continue
        previous = raw_avg[i - 1] if i > 0 else np.nan
        cap_base = previous if np.isfinite(previous) and previous > 0.0 else raw_avg[i]
        capped[i] = min(step[i], cap_base * cap_multiplier) if np.isfinite(cap_base) and cap_base > 0.0 else step[i]
    robust = _ema(capped, average_length)
    source_scale = None if cumulative else _ema(np.abs(source), average_length)
    out = np.full(len(source), np.nan, dtype=float)
    for i in range(len(source)):
        if not np.isfinite(robust[i]):
            continue
        threshold = robust[i] * float(lookback) * multiplier
        floor = 1e-10 if cumulative else source_scale[i] * 1e-6 if source_scale is not None and np.isfinite(source_scale[i]) else 0.0
        out[i] = max(threshold, floor)
    return out


def _source_array(name: str, open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    if name == "open":
        return open_
    if name == "high":
        return high
    if name == "low":
        return low
    if name == "close":
        return close
    if name == "hl2":
        return (high + low) / 2.0
    if name == "hlc3":
        return (high + low + close) / 3.0
    if name == "ohlc4":
        return (open_ + high + low + close) / 4.0
    raise ValueError(f"unsupported source: {name}")


def _effective_settings(config: RawIndicatorConfig) -> EffectiveTrendSettings:
    p = config.profile
    lookback = {TrendProfile.XAG_30M: 6, TrendProfile.XAG_1H: 6, TrendProfile.XAG_2H: 5, TrendProfile.XAG_4H: 5, TrendProfile.XAG_1D: 5}.get(p, config.trend_lookback)
    recent = {TrendProfile.XAG_30M: 2, TrendProfile.XAG_1H: 3, TrendProfile.XAG_2H: 2, TrendProfile.XAG_4H: 2, TrendProfile.XAG_1D: 2}.get(p, config.recent_lookback)
    consistency = {TrendProfile.XAG_30M: 66.0, TrendProfile.XAG_1H: 66.0, TrendProfile.XAG_2H: 60.0, TrendProfile.XAG_4H: 60.0, TrendProfile.XAG_1D: 60.0}.get(p, config.minimum_consistency)
    dead_zone = {TrendProfile.XAG_30M: 40.0, TrendProfile.XAG_1H: 40.0, TrendProfile.XAG_2H: 35.0, TrendProfile.XAG_4H: 35.0, TrendProfile.XAG_1D: 30.0}.get(p, config.step_dead_zone_percent)
    hold = {TrendProfile.XAG_30M: 65.0, TrendProfile.XAG_1H: 65.0, TrendProfile.XAG_2H: 65.0, TrendProfile.XAG_4H: 70.0, TrendProfile.XAG_1D: 70.0}.get(p, config.hysteresis_hold_percent)
    spike = {TrendProfile.XAG_30M: 72.0, TrendProfile.XAG_1H: 72.0, TrendProfile.XAG_2H: 70.0, TrendProfile.XAG_4H: 72.0, TrendProfile.XAG_1D: 75.0}.get(p, config.spike_dominance)
    dynamic_length = {TrendProfile.XAG_30M: 24, TrendProfile.XAG_1H: 24, TrendProfile.XAG_2H: 20, TrendProfile.XAG_4H: 20, TrendProfile.XAG_1D: 20}.get(p, config.dynamic_threshold_length)
    cap = 3.5 if p == TrendProfile.XAG_1D else config.dynamic_step_cap_multiplier if p == TrendProfile.MANUAL else 3.0
    return EffectiveTrendSettings(lookback, min(recent, lookback), consistency, dead_zone, hold, spike, dynamic_length, cap)


def _calculate_trend(source: np.ndarray, index: int, movement_threshold: float, settings: EffectiveTrendSettings, previous_direction: int, filter_spikes: bool) -> _TrendResult:
    lookback = settings.lookback
    if index < lookback or not np.isfinite(movement_threshold):
        return _TrendResult(0, TrendReason.DATA_WAIT, 0, None)
    window = source[index - lookback : index + 1]
    if not np.isfinite(window).all():
        return _TrendResult(0, TrendReason.DATA_WAIT, 0, None)

    full_threshold = max(abs(float(movement_threshold)), 1e-10)
    hold_threshold = full_threshold * settings.hysteresis_hold_percent / 100.0
    step_dead_zone = full_threshold / float(lookback) * settings.step_dead_zone_percent / 100.0
    steps = np.diff(window)
    up_count = int(np.sum(steps > step_dead_zone))
    down_count = int(np.sum(steps < -step_dead_zone))
    abs_steps = np.abs(steps)
    total_abs = float(abs_steps.sum())
    max_pos = int(np.argmax(abs_steps)) if len(abs_steps) else 0
    maximum_step = float(abs_steps[max_pos]) if len(abs_steps) else 0.0
    maximum_direction = 1 if steps[max_pos] > 0 else -1 if steps[max_pos] < 0 else 0
    net_move = float(window[-1] - window[0])

    x = np.arange(lookback + 1, dtype=float)
    y = window.astype(float)
    denominator = float(len(x)) * float(np.dot(x, x)) - float(x.sum()) ** 2
    slope = (float(len(x)) * float(np.dot(x, y)) - float(x.sum()) * float(y.sum())) / denominator if denominator else 0.0
    required = int(ceil(float(lookback) * settings.minimum_consistency / 100.0))
    hold_required = max(1, required - 1)
    recent_bars = min(settings.recent_lookback, lookback)
    recent_net = float(window[-1] - window[-1 - recent_bars])
    recent_tolerance = full_threshold * float(recent_bars) / float(lookback) * 0.50
    recent_conflict_rise = recent_net < -recent_tolerance
    recent_conflict_fall = recent_net > recent_tolerance
    minimum_slope = full_threshold / float(lookback) * 0.20
    hold_minimum_slope = minimum_slope * settings.hysteresis_hold_percent / 100.0
    dominant_single = total_abs > 0.0 and maximum_step / total_abs >= settings.spike_dominance / 100.0
    stronger_support = min(lookback, required + 1)

    up_spike = filter_spikes and dominant_single and maximum_direction == 1 and net_move > full_threshold and not recent_conflict_rise and up_count < stronger_support
    down_spike = filter_spikes and dominant_single and maximum_direction == -1 and net_move < -full_threshold and not recent_conflict_fall and down_count < stronger_support
    pending = 1 if up_spike else -1 if down_spike else 0
    rising_structure = up_count >= required and up_count > down_count and slope > minimum_slope and not recent_conflict_rise
    falling_structure = down_count >= required and down_count > up_count and slope < -minimum_slope and not recent_conflict_fall
    rising_strong = net_move > full_threshold and rising_structure and not up_spike
    falling_strong = net_move < -full_threshold and falling_structure and not down_spike
    rising_hold = net_move > hold_threshold and up_count >= hold_required and up_count >= down_count and slope > hold_minimum_slope and not recent_conflict_rise
    falling_hold = net_move < -hold_threshold and down_count >= hold_required and down_count >= up_count and slope < -hold_minimum_slope and not recent_conflict_fall

    result = 0
    reason = TrendReason.DATA_WAIT
    if previous_direction == 1:
        if falling_strong:
            result, reason = -1, TrendReason.CONFIRMED
        elif rising_strong:
            result, reason = 1, TrendReason.CONFIRMED
        elif pending == -1:
            reason = TrendReason.SPIKE_PENDING
        elif pending == 1:
            result, reason = 1, TrendReason.SPIKE_PENDING
        elif rising_hold:
            result, reason = 1, TrendReason.DIRECTION_HELD
    elif previous_direction == -1:
        if rising_strong:
            result, reason = 1, TrendReason.CONFIRMED
        elif falling_strong:
            result, reason = -1, TrendReason.CONFIRMED
        elif pending == 1:
            reason = TrendReason.SPIKE_PENDING
        elif pending == -1:
            result, reason = -1, TrendReason.SPIKE_PENDING
        elif falling_hold:
            result, reason = -1, TrendReason.DIRECTION_HELD
    else:
        if rising_strong:
            result, reason = 1, TrendReason.CONFIRMED
        elif falling_strong:
            result, reason = -1, TrendReason.CONFIRMED

    if result == 0:
        if pending != 0:
            reason = TrendReason.SPIKE_PENDING
        elif (net_move > 0.0 and recent_conflict_rise) or (net_move < 0.0 and recent_conflict_fall):
            reason = TrendReason.RECENT_CONFLICT
        elif abs(net_move) <= full_threshold:
            reason = TrendReason.NET_MOVE_LOW
        elif (net_move > 0.0 and (up_count < required or up_count <= down_count)) or (net_move < 0.0 and (down_count < required or down_count <= up_count)):
            reason = TrendReason.CONSISTENCY_LOW
        elif (net_move > 0.0 and slope <= minimum_slope) or (net_move < 0.0 and slope >= -minimum_slope):
            reason = TrendReason.SLOPE_LOW
        elif previous_direction != 0:
            reason = TrendReason.DIRECTION_LOST
        else:
            reason = TrendReason.MIXED

    consistency_direction = result if result != 0 else pending
    consistency_count = up_count if consistency_direction > 0 else down_count if consistency_direction < 0 else max(up_count, down_count)
    return _TrendResult(result, reason, pending, float(consistency_count) / float(lookback) * 100.0)


def _normalized_signed(value: float, scale: float) -> float:
    if not (_finite(value) and _finite(scale)) or abs(scale) <= 1e-7:
        return 0.0
    return _clamp(float(value) / abs(float(scale)), -1.0, 1.0)


def _movement_strength(source: np.ndarray, index: int, lookback: int, threshold: float) -> float:
    if index < lookback or not np.isfinite(source[index]) or not np.isfinite(source[index - lookback]) or not np.isfinite(threshold):
        return 0.0
    return _clamp(abs(source[index] - source[index - lookback]) / max(abs(threshold), 1e-7) / 2.0, 0.0, 1.0)


def _evidence_state_factor(direction: int, pending: int, reason: TrendReason, pending_weight: float) -> float:
    if direction == 0 and pending != 0:
        return pending_weight
    if direction == 0:
        return 0.0
    if reason == TrendReason.CONFIRMED:
        return 1.0
    if reason == TrendReason.DIRECTION_HELD:
        return 0.85
    if reason == TrendReason.SPIKE_PENDING:
        return 0.65
    return 0.75


def _evidence_strength(direction: int, pending: int, reason: TrendReason, valid: bool, consistency: float | None, signed_zone: float, movement: float, pending_weight: float, trust: float, maximum: float) -> float | None:
    if not valid:
        return None
    evidence_direction = direction if direction != 0 else pending
    if evidence_direction == 0:
        return 0.0
    consistency_factor = _clamp((consistency or 0.0) / 100.0, 0.0, 1.0)
    alignment = float(evidence_direction) * _clamp(signed_zone, -1.0, 1.0)
    zone_factor = 0.55 + 0.45 * alignment if alignment >= 0.0 else 0.15 + 0.40 * (alignment + 1.0)
    state_factor = _evidence_state_factor(direction, pending, reason, pending_weight)
    core = consistency_factor * 0.60 + _clamp(movement, 0.0, 1.0) * 0.40
    modifier = 0.40 + zone_factor * 0.60
    return float(evidence_direction) * _clamp(state_factor * core * modifier * trust, 0.0, maximum)


class RawIndicatorDashboardEngine:
    """Ham Dashboard v2.3.7 Tur-1 raw/evidence engine.

    Family aggregation, quorum, local SYS_* decisions and Contract v1 exports are
    intentionally Tur-2. Only closed, source-complete bars advance confirmed state.
    """

    EVIDENCE_MAX_STANDARD = 1.0
    EVIDENCE_MAX_TIMING = 0.65

    _WEIGHTS = {
        "PRICE_CONTEXT": 1.50,
        "MACD": 1.30,
        "MOMENTUM": 1.30,
        "RSI": 1.05,
        "CCI": 0.90,
        "SMI": 0.80,
        "CMF": 0.75,
        "OBV": 0.75,
        "STOCHASTIC": 0.55,
        "STOCH_RSI": 0.55,
    }

    _SERIES_ORDER = (
        "CMF",
        "OBV",
        "CCI",
        "RSI",
        "MACD",
        "MOMENTUM",
        "STOCHASTIC",
        "STOCH_RSI",
        "SMI",
    )

    def __init__(self, config: RawIndicatorConfig | None = None) -> None:
        self.config = config or RawIndicatorConfig()
        self._rows: list[dict[str, Any]] = []
        self._snapshot = RawIndicatorSnapshot()
        self._init_incremental_state()

    @property
    def snapshot(self) -> RawIndicatorSnapshot:
        return self._snapshot

    @property
    def effective_settings(self) -> EffectiveTrendSettings:
        return _effective_settings(self.config)

    def _init_incremental_state(self) -> None:
        cfg = self.config
        settings = self.effective_settings
        dyn = settings.dynamic_threshold_length
        self._inc: dict[str, Any] = {
            "n": 0,
            "o": [],
            "h": [],
            "l": [],
            "c": [],
            "v": [],
            "safe_v": [],
            "cv_f": [],
            "vp_f": [],
            "mf_f": [],
            "mfv": [],
            "obv": [],
            "cci_src": [],
            "rsi_src": [],
            "macd_src": [],
            "mom_src": [],
            "srsi_src": [],
            "values": {name: [] for name in self._SERIES_ORDER},
            "price_slow": [],
            "stoch_raw": [],
            "stoch_k": [],
            "srsi_base": [],
            "srsi_raw": [],
            "srsi_k": [],
            "atr_tr": [],
            "obv_baseline": [],
            "atr_series": [],
            "atr_ratio": [],
            "price_context": [],
            "price_valid": [],
            "thresholds": {name: [] for name in self._SERIES_ORDER},
            "trend_state": {name: 0 for name in self._SERIES_ORDER},
            # recursive states
            "obv_ema": _EmaState(dyn),
            "rsi": _RsiState(cfg.rsi_length),
            "macd_fast": _EmaState(cfg.macd_fast_length),
            "macd_slow": _EmaState(cfg.macd_slow_length),
            "macd_signal": _EmaState(cfg.macd_signal_length),
            "atr_rma": _RmaState(cfg.atr_length),
            "atr_base_ema": _EmaState(dyn),
            "ctx_fast": _EmaState(cfg.context_fast_ema_length),
            "ctx_slow": _EmaState(cfg.context_slow_ema_length),
            "smi_num1": _EmaState(cfg.smi_first_smoothing),
            "smi_num2": _EmaState(cfg.smi_second_smoothing),
            "smi_rng1": _EmaState(cfg.smi_first_smoothing),
            "smi_rng2": _EmaState(cfg.smi_second_smoothing),
            "smi_signal": _EmaState(cfg.smi_signal_length),
            "srsi": _RsiState(cfg.stoch_rsi_rsi_length),
            "obv_thr": _DynamicThresholdState(dyn, cumulative=True),
            "macd_thr": _DynamicThresholdState(dyn, cumulative=False),
            "mom_thr": _DynamicThresholdState(dyn, cumulative=False),
        }

    def reset(self) -> None:
        self._rows.clear()
        self._snapshot = RawIndicatorSnapshot()
        self._init_incremental_state()

    def update(self, bar: pd.Series | dict[str, Any]) -> RawIndicatorSnapshot:
        row = dict(bar)
        if not bool(row.get("is_closed", True)):
            return self._with_quality(self._snapshot, RawDataQuality.INCOMPLETE_BAR)
        if not bool(row.get("is_complete", True)):
            return self._with_quality(self._snapshot, RawDataQuality.SOURCE_GAP)
        self._rows.append(row)
        self._snapshot = self._compute_incremental_row()
        return self._snapshot

    def replay(self, frame: pd.DataFrame) -> list[RawIndicatorSnapshot]:
        # Incremental per-bar path: identical snapshots to the closed-form frame
        # computation while keeping update()/replay() on one shared causal core.
        self.reset()
        return [self.update(row) for row in frame.to_dict("records")]

    def _compute_incremental_row(self) -> RawIndicatorSnapshot:
        """Append exactly one causal row, mirroring _compute_frame element-for-element.

        Window aggregates use the same numpy reductions on the same slices and the
        recursive series (EMA/RMA/RSI) reuse the same arithmetic, so per-bar results
        stay bit-identical to the vectorized full-history computation.
        """
        cfg = self.config
        settings = self.effective_settings
        inc = self._inc
        i = inc["n"]
        row = self._rows[-1]

        if i == 0:
            for column in ("open", "high", "low", "close", "volume"):
                if column not in row:
                    raise ValueError(f"missing required column: {column}")

        def _num(value: Any) -> float:
            try:
                result = float(value)
            except (TypeError, ValueError):
                return float("nan")
            return result

        o = _num(row["open"])
        h = _num(row["high"])
        l = _num(row["low"])
        c = _num(row["close"])
        v = _num(row["volume"])
        inc["o"].append(o)
        inc["h"].append(h)
        inc["l"].append(l)
        inc["c"].append(c)
        inc["v"].append(v)
        safe_v = v if isfinite(v) else 0.0
        inc["safe_v"].append(safe_v)

        # volume quality
        current_valid = isfinite(v) and v > 0.0
        inc["cv_f"].append(1.0 if current_valid else 0.0)
        coverage = _window_sma(inc["cv_f"], cfg.volume_quality_length) * 100.0
        if i >= 1 and current_valid and inc["cv_f"][i - 1] == 1.0:
            valid_pair = True
            change_pct = abs(v - inc["v"][i - 1]) / inc["v"][i - 1] * 100.0
        else:
            valid_pair = False
            change_pct = float("nan")
        inc["vp_f"].append(1.0 if valid_pair else 0.0)
        pair_count = _window_sum(inc["vp_f"], cfg.volume_quality_length)
        meaningful = valid_pair and (-1.0 if not isfinite(change_pct) else change_pct) >= cfg.minimum_meaningful_volume_change
        inc["mf_f"].append(1.0 if meaningful else 0.0)
        meaningful_count = _window_sum(inc["mf_f"], cfg.volume_quality_length)
        if isfinite(pair_count) and pair_count > 0.0 and isfinite(meaningful_count):
            variation = meaningful_count * 100.0 / pair_count
        else:
            variation = float("nan")

        if not isfinite(coverage):
            quality = VolumeQuality.WAITING
        elif coverage <= 0.0:
            quality = VolumeQuality.MISSING
        elif not isfinite(variation):
            quality = VolumeQuality.LIMITED
        elif coverage >= cfg.minimum_volume_coverage and variation >= cfg.minimum_volume_variation:
            quality = VolumeQuality.ADEQUATE
        else:
            quality = VolumeQuality.LIMITED
        effective_calc = min(cfg.minimum_calculable_volume_coverage, cfg.minimum_volume_coverage)
        volume_calculable = quality >= VolumeQuality.LIMITED and coverage >= effective_calc
        volume_reliable = quality == VolumeQuality.ADEQUATE

        # flow: CMF + OBV
        candle_range = h - l
        if isfinite(candle_range) and candle_range != 0.0:
            mfm = ((c - l) - (h - c)) / candle_range
        else:
            mfm = 0.0
        mfv = mfm * safe_v
        inc["mfv"].append(mfv)
        cmf_vsum = _window_sum(inc["safe_v"], cfg.cmf_length)
        cmf_mfsum = _window_sum(inc["mfv"], cfg.cmf_length)
        if isfinite(cmf_vsum) and cmf_vsum != 0.0 and isfinite(cmf_mfsum):
            cmf = cmf_mfsum / cmf_vsum
        else:
            cmf = float("nan")

        if i >= 1:
            prev_close = inc["c"][i - 1]
            if c > prev_close:
                signed_v = safe_v
            elif c < prev_close:
                signed_v = -safe_v
            else:
                signed_v = 0.0
            obv = inc["obv"][-1] + signed_v
        else:
            obv = 0.0
        inc["obv"].append(obv)
        obv_baseline = inc["obv_ema"].next(obv)
        inc["obv_baseline"].append(obv_baseline)

        def _source(name: str) -> float:
            if name == "open":
                return o
            if name == "high":
                return h
            if name == "low":
                return l
            if name == "close":
                return c
            if name == "hl2":
                return (h + l) / 2.0
            if name == "hlc3":
                return (h + l + c) / 3.0
            return (o + h + l + c) / 4.0

        cci_src = _source(cfg.cci_source)
        rsi_src = _source(cfg.rsi_source)
        macd_src = _source(cfg.macd_source)
        mom_src = _source(cfg.momentum_source)
        srsi_src = _source(cfg.stoch_rsi_source)
        inc["cci_src"].append(cci_src)
        inc["rsi_src"].append(rsi_src)
        inc["macd_src"].append(macd_src)
        inc["mom_src"].append(mom_src)
        inc["srsi_src"].append(srsi_src)

        # CCI
        cci_basis = _window_sma(inc["cci_src"], cfg.cci_length)
        cci_window = _window(inc["cci_src"], cfg.cci_length)
        if cci_window is None or not np.isfinite(cci_window).all() or not isfinite(cci_basis):
            cci = float("nan")
        else:
            mean_dev = float(np.mean(np.abs(cci_window - cci_basis)))
            cci = (cci_src - cci_basis) / (0.015 * mean_dev) if mean_dev != 0.0 else float("nan")

        rsi = inc["rsi"].next(rsi_src)

        fast = inc["macd_fast"].next(macd_src)
        slow = inc["macd_slow"].next(macd_src)
        macd_line = fast - slow
        macd_signal = inc["macd_signal"].next(macd_line)
        macd_hist = macd_line - macd_signal
        if cfg.macd_displayed_value == "Sinyal":
            macd = macd_signal
        elif cfg.macd_displayed_value == "Histogram":
            macd = macd_hist
        else:
            macd = macd_line

        momentum = mom_src - inc["mom_src"][i - cfg.momentum_length] if i >= cfg.momentum_length else float("nan")

        # ATR
        if not (isfinite(h) and isfinite(l)):
            tr = float("nan")
        elif i == 0 or not isfinite(inc["c"][i - 1]):
            tr = h - l
        else:
            prev_close = inc["c"][i - 1]
            tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        inc["atr_tr"].append(tr)
        atr = inc["atr_rma"].next(tr)
        atr_baseline = inc["atr_base_ema"].next(atr)
        inc["atr_series"].append(atr)
        if isfinite(atr) and isfinite(atr_baseline) and atr_baseline != 0.0:
            atr_ratio = atr / atr_baseline
        else:
            atr_ratio = float("nan")
        inc["atr_ratio"].append(atr_ratio)

        # price context
        ctx_fast = inc["ctx_fast"].next(c)
        ctx_slow = inc["ctx_slow"].next(c)
        inc["price_slow"].append(ctx_slow)
        if (
            i >= cfg.context_slope_length
            and isfinite(ctx_fast)
            and isfinite(ctx_slow)
            and isfinite(inc["price_slow"][i - cfg.context_slope_length])
            and isfinite(atr)
        ):
            safe_atr = max(atr, cfg.minimum_tick)
            position = _clamp((c - ctx_slow) / (safe_atr * 1.50), -1.0, 1.0)
            order = _clamp((ctx_fast - ctx_slow) / (safe_atr * 0.75), -1.0, 1.0)
            slope = _clamp(
                (ctx_slow - inc["price_slow"][i - cfg.context_slope_length]) / (safe_atr * float(cfg.context_slope_length)),
                -1.0,
                1.0,
            )
            price_context = position * 0.35 + order * 0.35 + slope * 0.30
            price_context_valid = cfg.context_fast_ema_length < cfg.context_slow_ema_length
        else:
            price_context = float("nan")
            price_context_valid = False
        inc["price_context"].append(price_context)
        inc["price_valid"].append(price_context_valid)

        # stochastic
        stoch_low = _window_min(inc["l"], cfg.stochastic_length)
        stoch_high = _window_max(inc["h"], cfg.stochastic_length)
        stoch_range = stoch_high - stoch_low
        if isfinite(stoch_range) and stoch_range != 0.0:
            stoch_raw = (c - stoch_low) * 100.0 / stoch_range
        else:
            stoch_raw = float("nan")
        inc["stoch_raw"].append(stoch_raw)
        stoch_k = _window_sma(inc["stoch_raw"], cfg.stochastic_k_smoothing)
        inc["stoch_k"].append(stoch_k)
        stoch_d = _window_sma(inc["stoch_k"], cfg.stochastic_d_smoothing)
        stochastic = stoch_d if cfg.stochastic_displayed_value == "%D" else stoch_k

        # stoch rsi
        srsi_base = inc["srsi"].next(srsi_src)
        inc["srsi_base"].append(srsi_base)
        srsi_low = _window_min(inc["srsi_base"], cfg.stoch_rsi_length)
        srsi_high = _window_max(inc["srsi_base"], cfg.stoch_rsi_length)
        srsi_range = srsi_high - srsi_low
        if isfinite(srsi_range) and srsi_range != 0.0:
            srsi_raw = (srsi_base - srsi_low) * 100.0 / srsi_range
        else:
            srsi_raw = float("nan")
        inc["srsi_raw"].append(srsi_raw)
        srsi_k = _window_sma(inc["srsi_raw"], cfg.stoch_rsi_k_smoothing)
        inc["srsi_k"].append(srsi_k)
        srsi_d = _window_sma(inc["srsi_k"], cfg.stoch_rsi_d_smoothing)
        stoch_rsi = srsi_d if cfg.stoch_rsi_displayed_value == "%D" else srsi_k

        # smi
        smi_high = _window_max(inc["h"], cfg.smi_length)
        smi_low = _window_min(inc["l"], cfg.smi_length)
        smi_distance = c - (smi_high + smi_low) / 2.0
        smi_range = smi_high - smi_low
        smi_num = inc["smi_num2"].next(inc["smi_num1"].next(smi_distance))
        smi_rng = inc["smi_rng2"].next(inc["smi_rng1"].next(smi_range))
        if isfinite(smi_rng) and smi_rng != 0.0:
            smi_main = smi_num * 100.0 / (smi_rng / 2.0)
        else:
            smi_main = float("nan")
        smi_signal = inc["smi_signal"].next(smi_main)
        smi = smi_signal if cfg.smi_displayed_value == "Sinyal" else smi_main

        obv_threshold = inc["obv_thr"].next(
            obv,
            lookback=settings.lookback,
            multiplier=cfg.obv_dynamic_multiplier,
            cap_multiplier=settings.dynamic_step_cap_multiplier,
            cumulative=True,
        )
        macd_threshold = inc["macd_thr"].next(
            macd,
            lookback=settings.lookback,
            multiplier=cfg.macd_dynamic_multiplier,
            cap_multiplier=settings.dynamic_step_cap_multiplier,
            cumulative=False,
        )
        mom_threshold = inc["mom_thr"].next(
            momentum,
            lookback=settings.lookback,
            multiplier=cfg.momentum_dynamic_multiplier,
            cap_multiplier=settings.dynamic_step_cap_multiplier,
            cumulative=False,
        )

        values = inc["values"]
        values["CMF"].append(cmf)
        values["OBV"].append(obv)
        values["CCI"].append(cci)
        values["RSI"].append(rsi)
        values["MACD"].append(macd)
        values["MOMENTUM"].append(momentum)
        values["STOCHASTIC"].append(stochastic)
        values["STOCH_RSI"].append(stoch_rsi)
        values["SMI"].append(smi)
        thresholds = inc["thresholds"]
        thresholds["CMF"].append(cfg.cmf_minimum_move)
        thresholds["OBV"].append(obv_threshold)
        thresholds["CCI"].append(cfg.cci_minimum_move)
        thresholds["RSI"].append(cfg.rsi_minimum_move)
        thresholds["MACD"].append(macd_threshold)
        thresholds["MOMENTUM"].append(mom_threshold)
        thresholds["STOCHASTIC"].append(cfg.stochastic_minimum_move)
        thresholds["STOCH_RSI"].append(cfg.stoch_rsi_minimum_move)
        thresholds["SMI"].append(cfg.smi_minimum_move)

        zone_scales = {
            "CMF": 0.10,
            "OBV": None,
            "CCI": 100.0,
            "RSI": 20.0,
            "MACD": None,
            "MOMENTUM": None,
            "STOCHASTIC": 30.0,
            "STOCH_RSI": 30.0,
            "SMI": 40.0,
        }
        maximums = {
            "CMF": self.EVIDENCE_MAX_STANDARD,
            "OBV": self.EVIDENCE_MAX_STANDARD,
            "CCI": self.EVIDENCE_MAX_STANDARD,
            "RSI": self.EVIDENCE_MAX_STANDARD,
            "MACD": self.EVIDENCE_MAX_STANDARD,
            "MOMENTUM": self.EVIDENCE_MAX_STANDARD,
            "STOCHASTIC": self.EVIDENCE_MAX_TIMING,
            "STOCH_RSI": self.EVIDENCE_MAX_TIMING,
            "SMI": self.EVIDENCE_MAX_STANDARD,
        }
        eligibility = {
            "CMF": volume_calculable,
            "OBV": volume_calculable,
            "CCI": True,
            "RSI": True,
            "MACD": cfg.macd_fast_length < cfg.macd_slow_length,
            "MOMENTUM": True,
            "STOCHASTIC": True,
            "STOCH_RSI": True,
            "SMI": True,
        }

        lookback = settings.lookback
        state_memory = inc["trend_state"]
        trend_results: dict[str, _TrendResult] = {}
        for name in self._SERIES_ORDER:
            eligible = bool(eligibility[name])
            previous = state_memory[name] if eligible else 0
            if eligible:
                series_values = values[name]
                threshold_i = thresholds[name][i]
                if i >= lookback:
                    window = np.asarray(series_values[i - lookback : i + 1], dtype=float)
                    trend = _calculate_trend(window, lookback, threshold_i, settings, previous, cfg.use_spike_filter)
                else:
                    trend = _TrendResult(0, TrendReason.DATA_WAIT, 0, None)
            else:
                trend = _TrendResult(0, TrendReason.DATA_WAIT, 0, None)
            trend_results[name] = trend
            if not eligible:
                state_memory[name] = 0
            elif trend.direction != 0:
                state_memory[name] = trend.direction
            elif trend.pending == 0:
                state_memory[name] = 0

        weak = _clamp(cfg.weak_evidence_threshold, 0.05, 0.85)
        medium = _clamp(max(cfg.medium_evidence_threshold, weak + 0.05), weak + 0.05, 0.90)
        strong = _clamp(max(cfg.strong_evidence_threshold, medium + 0.05), medium + 0.05, 0.95)

        coverage_trust = 0.0 if not isfinite(coverage) else _clamp(coverage / max(cfg.minimum_volume_coverage, 1e-6), 0.0, 1.0)
        variation_trust = 1.0 if cfg.minimum_volume_variation <= 0.0 else 0.0 if not isfinite(variation) else _clamp(variation / cfg.minimum_volume_variation, 0.0, 1.0)
        limited_trust = min(coverage_trust, variation_trust) * cfg.limited_volume_evidence_weight
        volume_trust = 1.0 if volume_reliable else limited_trust if volume_calculable else 0.0

        indicators: dict[str, IndicatorEvidence] = {}
        for name in self._SERIES_ORDER:
            series_values = values[name]
            value_i = series_values[i]
            value_lb = series_values[i - lookback] if i >= lookback else float("nan")
            trend = trend_results[name]
            threshold_i = thresholds[name][i]
            valid = bool(eligibility[name]) and i >= lookback and isfinite(value_i) and isfinite(value_lb) and trend.consistency is not None
            if name in {"OBV", "MACD", "MOMENTUM"}:
                valid = valid and isfinite(threshold_i)
            if name == "OBV":
                valid = valid and isfinite(obv_baseline)
            threshold = float(threshold_i) if isfinite(threshold_i) else float("nan")
            if i < lookback or not isfinite(value_i) or not isfinite(value_lb) or not isfinite(threshold):
                movement = 0.0
            else:
                movement = _clamp(abs(value_i - value_lb) / max(abs(threshold), 1e-7) / 2.0, 0.0, 1.0)
            if name == "OBV":
                signed_zone = _normalized_signed(value_i - obv_baseline, max(abs(threshold), 1e-7)) if isfinite(obv_baseline) else 0.0
            elif name in {"MACD", "MOMENTUM"}:
                signed_zone = _normalized_signed(value_i, max(abs(threshold), 1e-7))
            elif name == "RSI":
                signed_zone = _normalized_signed(value_i - 50.0, 20.0)
            elif name in {"STOCHASTIC", "STOCH_RSI"}:
                signed_zone = _normalized_signed(value_i - 50.0, 30.0)
            else:
                signed_zone = _normalized_signed(value_i, float(zone_scales[name] or 1.0))
            evidence = _evidence_strength(
                trend.direction,
                trend.pending,
                trend.reason,
                bool(valid),
                trend.consistency,
                signed_zone,
                movement,
                cfg.pending_evidence_weight,
                1.0,
                maximums[name],
            )
            relative = None if evidence is None else _clamp(evidence / max(maximums[name], 1e-6), -1.0, 1.0)
            indicators[name] = IndicatorEvidence(_opt(value_i), bool(valid), trend.direction, trend.pending, trend.reason, trend.consistency, movement, signed_zone, evidence, relative)

        price_value = _opt(price_context) if price_context_valid else None
        indicators["PRICE_CONTEXT"] = IndicatorEvidence(
            price_value,
            bool(price_context_valid),
            1 if (price_value or 0.0) > 0.0 else -1 if (price_value or 0.0) < 0.0 else 0,
            0,
            TrendReason.CONFIRMED if price_context_valid else TrendReason.DATA_WAIT,
            None,
            abs(price_value or 0.0),
            price_value or 0.0,
            price_value,
            price_value,
        )

        valid_count = sum(int(e.valid) for e in indicators.values())
        up_count = sum(int((e.relative_evidence or 0.0) >= weak) for e in indicators.values())
        down_count = sum(int((e.relative_evidence or 0.0) <= -weak) for e in indicators.values())
        strong_up = sum(int((e.relative_evidence or 0.0) >= strong) for e in indicators.values())
        strong_down = sum(int((e.relative_evidence or 0.0) <= -strong) for e in indicators.values())

        effective_weights = dict(self._WEIGHTS)
        effective_weights["CMF"] = self._WEIGHTS["CMF"] * volume_trust if indicators["CMF"].valid else 0.0
        effective_weights["OBV"] = self._WEIGHTS["OBV"] * volume_trust if indicators["OBV"].valid else 0.0
        valid_weight = 0.0
        weighted_up = 0.0
        weighted_down = 0.0
        for name, e in indicators.items():
            weight = effective_weights[name] if name in {"CMF", "OBV"} else self._WEIGHTS[name] if e.valid else 0.0
            if name in {"CMF", "OBV"}:
                pass
            elif not e.valid:
                weight = 0.0
            valid_weight += weight
            raw_evidence = e.evidence or 0.0
            weighted_up += max(raw_evidence, 0.0) * weight
            weighted_down += max(-raw_evidence, 0.0) * weight
        net_score = (weighted_up - weighted_down) / valid_weight * 100.0 if valid_weight > 0.0 else None

        data_quality = RawDataQuality.OK if valid_count >= 6 else RawDataQuality.WARMUP
        inc["n"] = i + 1
        return RawIndicatorSnapshot(
            timestamp=row.get("timestamp"),
            data_quality=data_quality,
            volume_quality=VolumeQuality(int(quality)),
            volume_coverage=_opt(coverage),
            volume_variation=_opt(variation),
            volume_calculable=bool(volume_calculable),
            volume_reliable=bool(volume_reliable),
            volume_trust=float(volume_trust),
            atr=_opt(atr),
            atr_ratio=_opt(atr_ratio),
            price_context=price_value,
            price_context_valid=bool(price_context_valid),
            indicators=indicators,
            valid_evidence_count=valid_count,
            up_evidence_count=up_count,
            down_evidence_count=down_count,
            strong_up_count=strong_up,
            strong_down_count=strong_down,
            net_evidence_score=net_score,
        )

    @staticmethod
    def _with_quality(snapshot: RawIndicatorSnapshot, quality: RawDataQuality) -> RawIndicatorSnapshot:
        return RawIndicatorSnapshot(
            timestamp=snapshot.timestamp,
            data_quality=quality,
            volume_quality=snapshot.volume_quality,
            volume_coverage=snapshot.volume_coverage,
            volume_variation=snapshot.volume_variation,
            volume_calculable=snapshot.volume_calculable,
            volume_reliable=snapshot.volume_reliable,
            volume_trust=snapshot.volume_trust,
            atr=snapshot.atr,
            atr_ratio=snapshot.atr_ratio,
            price_context=snapshot.price_context,
            price_context_valid=snapshot.price_context_valid,
            indicators=snapshot.indicators,
            valid_evidence_count=snapshot.valid_evidence_count,
            up_evidence_count=snapshot.up_evidence_count,
            down_evidence_count=snapshot.down_evidence_count,
            strong_up_count=snapshot.strong_up_count,
            strong_down_count=snapshot.strong_down_count,
            net_evidence_score=snapshot.net_evidence_score,
        )

    def _compute_frame(self, frame: pd.DataFrame) -> list[RawIndicatorSnapshot]:
        if frame.empty:
            return []
        for column in ("open", "high", "low", "close", "volume"):
            if column not in frame.columns:
                raise ValueError(f"missing required column: {column}")

        cfg = self.config
        settings = self.effective_settings
        n = len(frame)
        o = pd.to_numeric(frame["open"], errors="coerce").to_numpy(float)
        h = pd.to_numeric(frame["high"], errors="coerce").to_numpy(float)
        l = pd.to_numeric(frame["low"], errors="coerce").to_numpy(float)
        c = pd.to_numeric(frame["close"], errors="coerce").to_numpy(float)
        v = pd.to_numeric(frame["volume"], errors="coerce").to_numpy(float)
        safe_v = np.where(np.isfinite(v), v, 0.0)

        current_valid = np.isfinite(v) & (v > 0.0)
        coverage = _sma(current_valid.astype(float), cfg.volume_quality_length) * 100.0
        valid_pair = np.zeros(n, dtype=bool)
        change_pct = np.full(n, np.nan, dtype=float)
        if n > 1:
            valid_pair[1:] = current_valid[1:] & current_valid[:-1]
            idx = np.where(valid_pair)[0]
            change_pct[idx] = np.abs(v[idx] - v[idx - 1]) / v[idx - 1] * 100.0
        pair_count = _sum(valid_pair.astype(float), cfg.volume_quality_length)
        meaningful = valid_pair & (np.nan_to_num(change_pct, nan=-1.0) >= cfg.minimum_meaningful_volume_change)
        meaningful_count = _sum(meaningful.astype(float), cfg.volume_quality_length)
        variation_mask = np.isfinite(pair_count) & (pair_count > 0.0) & np.isfinite(meaningful_count)
        variation = _safe_divide(meaningful_count * 100.0, pair_count, variation_mask)

        volume_quality = np.full(n, int(VolumeQuality.WAITING), dtype=int)
        volume_calculable = np.zeros(n, dtype=bool)
        volume_reliable = np.zeros(n, dtype=bool)
        effective_calc = min(cfg.minimum_calculable_volume_coverage, cfg.minimum_volume_coverage)
        for i in range(n):
            if not np.isfinite(coverage[i]):
                q = VolumeQuality.WAITING
            elif coverage[i] <= 0.0:
                q = VolumeQuality.MISSING
            elif not np.isfinite(variation[i]):
                q = VolumeQuality.LIMITED
            elif coverage[i] >= cfg.minimum_volume_coverage and variation[i] >= cfg.minimum_volume_variation:
                q = VolumeQuality.ADEQUATE
            else:
                q = VolumeQuality.LIMITED
            volume_quality[i] = int(q)
            volume_calculable[i] = q >= VolumeQuality.LIMITED and coverage[i] >= effective_calc
            volume_reliable[i] = q == VolumeQuality.ADEQUATE

        candle_range = h - l
        mfm = np.zeros(n, dtype=float)
        range_mask = np.isfinite(candle_range) & (candle_range != 0.0)
        np.divide((c - l) - (h - c), candle_range, out=mfm, where=range_mask)
        mfv = mfm * safe_v
        cmf_vsum = _sum(safe_v, cfg.cmf_length)
        cmf_mfsum = _sum(mfv, cfg.cmf_length)
        cmf_mask = np.isfinite(cmf_vsum) & (cmf_vsum != 0.0) & np.isfinite(cmf_mfsum)
        cmf = _safe_divide(cmf_mfsum, cmf_vsum, cmf_mask)

        signed_v = np.zeros(n, dtype=float)
        for i in range(1, n):
            signed_v[i] = safe_v[i] if c[i] > c[i - 1] else -safe_v[i] if c[i] < c[i - 1] else 0.0
        obv = np.cumsum(signed_v)
        obv_baseline = _ema(obv, settings.dynamic_threshold_length)

        cci_source = _source_array(cfg.cci_source, o, h, l, c)
        rsi_source = _source_array(cfg.rsi_source, o, h, l, c)
        macd_source = _source_array(cfg.macd_source, o, h, l, c)
        momentum_source = _source_array(cfg.momentum_source, o, h, l, c)
        srsi_source = _source_array(cfg.stoch_rsi_source, o, h, l, c)

        cci = _cci(cci_source, cfg.cci_length)
        rsi = _rsi(rsi_source, cfg.rsi_length)
        macd_line = _ema(macd_source, cfg.macd_fast_length) - _ema(macd_source, cfg.macd_slow_length)
        macd_signal = _ema(macd_line, cfg.macd_signal_length)
        macd_hist = macd_line - macd_signal
        macd = macd_signal if cfg.macd_displayed_value == "Sinyal" else macd_hist if cfg.macd_displayed_value == "Histogram" else macd_line
        momentum = np.full(n, np.nan, dtype=float)
        if n > cfg.momentum_length:
            momentum[cfg.momentum_length :] = momentum_source[cfg.momentum_length :] - momentum_source[: -cfg.momentum_length]

        atr = _rma(_true_range(h, l, c), cfg.atr_length)
        atr_baseline = _ema(atr, settings.dynamic_threshold_length)
        atr_mask = np.isfinite(atr) & np.isfinite(atr_baseline) & (atr_baseline != 0.0)
        atr_ratio = _safe_divide(atr, atr_baseline, atr_mask)

        fast_context = _ema(c, cfg.context_fast_ema_length)
        slow_context = _ema(c, cfg.context_slow_ema_length)
        price_context = np.full(n, np.nan, dtype=float)
        price_context_valid = np.zeros(n, dtype=bool)
        for i in range(n):
            if i < cfg.context_slope_length or not (np.isfinite(fast_context[i]) and np.isfinite(slow_context[i]) and np.isfinite(slow_context[i - cfg.context_slope_length]) and np.isfinite(atr[i])):
                continue
            safe_atr = max(float(atr[i]), cfg.minimum_tick)
            position = _clamp((c[i] - slow_context[i]) / (safe_atr * 1.50), -1.0, 1.0)
            order = _clamp((fast_context[i] - slow_context[i]) / (safe_atr * 0.75), -1.0, 1.0)
            slope = _clamp((slow_context[i] - slow_context[i - cfg.context_slope_length]) / (safe_atr * float(cfg.context_slope_length)), -1.0, 1.0)
            price_context[i] = position * 0.35 + order * 0.35 + slope * 0.30
            price_context_valid[i] = cfg.context_fast_ema_length < cfg.context_slow_ema_length

        stoch_low = _rolling_min(l, cfg.stochastic_length)
        stoch_high = _rolling_max(h, cfg.stochastic_length)
        stoch_range = stoch_high - stoch_low
        stoch_raw = _safe_divide((c - stoch_low) * 100.0, stoch_range, np.isfinite(stoch_range) & (stoch_range != 0.0))
        stoch_k = _sma(stoch_raw, cfg.stochastic_k_smoothing)
        stoch_d = _sma(stoch_k, cfg.stochastic_d_smoothing)
        stochastic = stoch_d if cfg.stochastic_displayed_value == "%D" else stoch_k

        srsi_base = _rsi(srsi_source, cfg.stoch_rsi_rsi_length)
        srsi_low = _rolling_min(srsi_base, cfg.stoch_rsi_length)
        srsi_high = _rolling_max(srsi_base, cfg.stoch_rsi_length)
        srsi_range = srsi_high - srsi_low
        srsi_raw = _safe_divide((srsi_base - srsi_low) * 100.0, srsi_range, np.isfinite(srsi_range) & (srsi_range != 0.0))
        srsi_k = _sma(srsi_raw, cfg.stoch_rsi_k_smoothing)
        srsi_d = _sma(srsi_k, cfg.stoch_rsi_d_smoothing)
        stoch_rsi = srsi_d if cfg.stoch_rsi_displayed_value == "%D" else srsi_k

        smi_high = _rolling_max(h, cfg.smi_length)
        smi_low = _rolling_min(l, cfg.smi_length)
        smi_distance = c - (smi_high + smi_low) / 2.0
        smi_range = smi_high - smi_low
        smi_num = _ema(_ema(smi_distance, cfg.smi_first_smoothing), cfg.smi_second_smoothing)
        smi_rng = _ema(_ema(smi_range, cfg.smi_first_smoothing), cfg.smi_second_smoothing)
        smi_main = _safe_divide(smi_num * 100.0, smi_rng / 2.0, np.isfinite(smi_rng) & (smi_rng != 0.0))
        smi_signal = _ema(smi_main, cfg.smi_signal_length)
        smi = smi_signal if cfg.smi_displayed_value == "Sinyal" else smi_main

        obv_thr = _dynamic_threshold(obv, settings.dynamic_threshold_length, settings.lookback, cfg.obv_dynamic_multiplier, settings.dynamic_step_cap_multiplier, cumulative=True)
        macd_thr = _dynamic_threshold(macd, settings.dynamic_threshold_length, settings.lookback, cfg.macd_dynamic_multiplier, settings.dynamic_step_cap_multiplier, cumulative=False)
        mom_thr = _dynamic_threshold(momentum, settings.dynamic_threshold_length, settings.lookback, cfg.momentum_dynamic_multiplier, settings.dynamic_step_cap_multiplier, cumulative=False)

        series = {
            "CMF": (cmf, np.full(n, cfg.cmf_minimum_move), volume_calculable, 0.10, self.EVIDENCE_MAX_STANDARD),
            "OBV": (obv, obv_thr, volume_calculable, None, self.EVIDENCE_MAX_STANDARD),
            "CCI": (cci, np.full(n, cfg.cci_minimum_move), np.ones(n, dtype=bool), 100.0, self.EVIDENCE_MAX_STANDARD),
            "RSI": (rsi, np.full(n, cfg.rsi_minimum_move), np.ones(n, dtype=bool), 20.0, self.EVIDENCE_MAX_STANDARD),
            "MACD": (macd, macd_thr, np.full(n, cfg.macd_fast_length < cfg.macd_slow_length, dtype=bool), None, self.EVIDENCE_MAX_STANDARD),
            "MOMENTUM": (momentum, mom_thr, np.ones(n, dtype=bool), None, self.EVIDENCE_MAX_STANDARD),
            "STOCHASTIC": (stochastic, np.full(n, cfg.stochastic_minimum_move), np.ones(n, dtype=bool), 30.0, self.EVIDENCE_MAX_TIMING),
            "STOCH_RSI": (stoch_rsi, np.full(n, cfg.stoch_rsi_minimum_move), np.ones(n, dtype=bool), 30.0, self.EVIDENCE_MAX_TIMING),
            "SMI": (smi, np.full(n, cfg.smi_minimum_move), np.ones(n, dtype=bool), 40.0, self.EVIDENCE_MAX_STANDARD),
        }

        trend_history: dict[str, list[_TrendResult]] = {name: [] for name in series}
        state_memory = {name: 0 for name in series}
        for i in range(n):
            for name, (values, thresholds, eligibility, _, _) in series.items():
                eligible = bool(eligibility[i])
                previous = state_memory[name] if eligible else 0
                tr = _calculate_trend(values, i, thresholds[i], settings, previous, cfg.use_spike_filter) if eligible else _TrendResult(0, TrendReason.DATA_WAIT, 0, None)
                trend_history[name].append(tr)
                if not eligible:
                    state_memory[name] = 0
                elif tr.direction != 0:
                    state_memory[name] = tr.direction
                elif tr.pending == 0:
                    state_memory[name] = 0

        weak = _clamp(cfg.weak_evidence_threshold, 0.05, 0.85)
        medium = _clamp(max(cfg.medium_evidence_threshold, weak + 0.05), weak + 0.05, 0.90)
        strong = _clamp(max(cfg.strong_evidence_threshold, medium + 0.05), medium + 0.05, 0.95)
        del medium  # classification threshold is retained for Tur-2/UI, counts need weak/strong only.

        snapshots: list[RawIndicatorSnapshot] = []
        for i in range(n):
            coverage_trust = 0.0 if not np.isfinite(coverage[i]) else _clamp(coverage[i] / max(cfg.minimum_volume_coverage, 1e-6), 0.0, 1.0)
            variation_trust = 1.0 if cfg.minimum_volume_variation <= 0.0 else 0.0 if not np.isfinite(variation[i]) else _clamp(variation[i] / cfg.minimum_volume_variation, 0.0, 1.0)
            limited_trust = min(coverage_trust, variation_trust) * cfg.limited_volume_evidence_weight
            volume_trust = 1.0 if volume_reliable[i] else limited_trust if volume_calculable[i] else 0.0

            indicators: dict[str, IndicatorEvidence] = {}
            for name, (values, thresholds, eligibility, zone_scale, maximum) in series.items():
                tr = trend_history[name][i]
                valid = bool(eligibility[i]) and i >= settings.lookback and np.isfinite(values[i]) and np.isfinite(values[i - settings.lookback]) and tr.consistency is not None
                if name in {"OBV", "MACD", "MOMENTUM"}:
                    valid = valid and np.isfinite(thresholds[i])
                if name == "OBV":
                    valid = valid and np.isfinite(obv_baseline[i])
                threshold = float(thresholds[i]) if np.isfinite(thresholds[i]) else np.nan
                movement = _movement_strength(values, i, settings.lookback, threshold)
                if name == "OBV":
                    signed_zone = _normalized_signed(values[i] - obv_baseline[i], max(abs(threshold), 1e-7)) if np.isfinite(obv_baseline[i]) else 0.0
                elif name in {"MACD", "MOMENTUM"}:
                    signed_zone = _normalized_signed(values[i], max(abs(threshold), 1e-7))
                elif name == "RSI":
                    signed_zone = _normalized_signed(values[i] - 50.0, 20.0)
                elif name in {"STOCHASTIC", "STOCH_RSI"}:
                    signed_zone = _normalized_signed(values[i] - 50.0, 30.0)
                else:
                    signed_zone = _normalized_signed(values[i], float(zone_scale or 1.0))
                # Source v2.3.7 keeps raw CMF/OBV evidence unscaled; volume trust
                # affects their effective contribution weight in section 14/14B.
                evidence = _evidence_strength(tr.direction, tr.pending, tr.reason, bool(valid), tr.consistency, signed_zone, movement, cfg.pending_evidence_weight, 1.0, maximum)
                relative = None if evidence is None else _clamp(evidence / max(maximum, 1e-6), -1.0, 1.0)
                indicators[name] = IndicatorEvidence(_opt(values[i]), bool(valid), tr.direction, tr.pending, tr.reason, tr.consistency, movement, signed_zone, evidence, relative)

            price_value = _opt(price_context[i]) if price_context_valid[i] else None
            indicators["PRICE_CONTEXT"] = IndicatorEvidence(
                price_value,
                bool(price_context_valid[i]),
                1 if (price_value or 0.0) > 0.0 else -1 if (price_value or 0.0) < 0.0 else 0,
                0,
                TrendReason.CONFIRMED if price_context_valid[i] else TrendReason.DATA_WAIT,
                None,
                abs(price_value or 0.0),
                price_value or 0.0,
                price_value,
                price_value,
            )

            valid_count = sum(int(e.valid) for e in indicators.values())
            up_count = sum(int((e.relative_evidence or 0.0) >= weak) for e in indicators.values())
            down_count = sum(int((e.relative_evidence or 0.0) <= -weak) for e in indicators.values())
            strong_up = sum(int((e.relative_evidence or 0.0) >= strong) for e in indicators.values())
            strong_down = sum(int((e.relative_evidence or 0.0) <= -strong) for e in indicators.values())

            effective_weights = dict(self._WEIGHTS)
            effective_weights["CMF"] = self._WEIGHTS["CMF"] * volume_trust if indicators["CMF"].valid else 0.0
            effective_weights["OBV"] = self._WEIGHTS["OBV"] * volume_trust if indicators["OBV"].valid else 0.0
            valid_weight = 0.0
            weighted_up = 0.0
            weighted_down = 0.0
            for name, e in indicators.items():
                weight = effective_weights[name] if name in {"CMF", "OBV"} else self._WEIGHTS[name] if e.valid else 0.0
                if name in {"CMF", "OBV"}:
                    pass
                elif not e.valid:
                    weight = 0.0
                valid_weight += weight
                raw_evidence = e.evidence or 0.0
                weighted_up += max(raw_evidence, 0.0) * weight
                weighted_down += max(-raw_evidence, 0.0) * weight
            net_score = (weighted_up - weighted_down) / valid_weight * 100.0 if valid_weight > 0.0 else None

            quality = RawDataQuality.OK if valid_count >= 6 else RawDataQuality.WARMUP
            timestamp = frame.iloc[i].get("timestamp") if "timestamp" in frame.columns else i
            snapshots.append(
                RawIndicatorSnapshot(
                    timestamp=timestamp,
                    data_quality=quality,
                    volume_quality=VolumeQuality(int(volume_quality[i])),
                    volume_coverage=_opt(coverage[i]),
                    volume_variation=_opt(variation[i]),
                    volume_calculable=bool(volume_calculable[i]),
                    volume_reliable=bool(volume_reliable[i]),
                    volume_trust=float(volume_trust),
                    atr=_opt(atr[i]),
                    atr_ratio=_opt(atr_ratio[i]),
                    price_context=price_value,
                    price_context_valid=bool(price_context_valid[i]),
                    indicators=indicators,
                    valid_evidence_count=valid_count,
                    up_evidence_count=up_count,
                    down_evidence_count=down_count,
                    strong_up_count=strong_up,
                    strong_down_count=strong_down,
                    net_evidence_score=net_score,
                )
            )
        return snapshots


__all__ = [
    "EffectiveTrendSettings",
    "IndicatorEvidence",
    "RawDataQuality",
    "RawIndicatorConfig",
    "RawIndicatorDashboardEngine",
    "RawIndicatorSnapshot",
    "TrendProfile",
    "TrendReason",
    "VolumeQuality",
]
