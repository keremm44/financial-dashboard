from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import pandas as pd

from .models import Direction, EngineResult
from .stabil_trend_engine import (
    DailyRawState,
    DailyTrendSnapshot,
    DailyTrendState,
    GapState,
    H4EvidenceStatus,
    H4TrendSnapshot,
    H4TrendState,
    StabilTrendConfig,
    StabilTrendContext,
    WeeklyTrendSnapshot,
    WeeklyTrendState,
)
from .stabil_trend_runtime import StabilTrendEngine as _LifecycleEngine


class StabilMainState(StrEnum):
    PENDING = "PENDING"
    STABLE_UPTREND = "STABLE_UPTREND"
    HEALTHY_UPTREND = "HEALTHY_UPTREND"
    CONTROLLED_CORRECTION = "CONTROLLED_CORRECTION"
    RECOVERY_STARTING = "RECOVERY_STARTING"
    OVEREXTENDED = "OVEREXTENDED"
    UPTREND_WEAKENING = "UPTREND_WEAKENING"
    NOT_STABLE_UPTREND = "NOT_STABLE_UPTREND"


class StabilReason(StrEnum):
    NONE = "NONE"
    WAIT_WEEKLY = "WAIT_WEEKLY"
    WAIT_DAILY = "WAIT_DAILY"
    DAILY_STRUCTURE_BROKEN = "DAILY_STRUCTURE_BROKEN"
    SELLING_EXPANSION = "SELLING_EXPANSION"
    GAP_WATCH = "GAP_WATCH"
    GAP_CONFIRMED = "GAP_CONFIRMED"
    WEEKLY_NOT_UP = "WEEKLY_NOT_UP"
    PARABOLIC = "PARABOLIC"
    DAILY_TOO_DEEP = "DAILY_TOO_DEEP"
    CONTROLLED_H4_ATTEMPT_FAILED = "CONTROLLED_H4_ATTEMPT_FAILED"
    CONTROLLED_H4_FAILED = "CONTROLLED_H4_FAILED"
    CONTROLLED_H4_OLD = "CONTROLLED_H4_OLD"
    STRUCTURE_QUALITY_LOW = "STRUCTURE_QUALITY_LOW"
    DAILY_NEUTRAL = "DAILY_NEUTRAL"
    BEARISH_BALANCE = "BEARISH_BALANCE"
    CONTROLLED_H4_FRESH = "CONTROLLED_H4_FRESH"
    CONTROLLED_H4_WAIT = "CONTROLLED_H4_WAIT"
    WEEKLY_WEAKENING = "WEEKLY_WEAKENING"
    GAP_RECLAIMED = "GAP_RECLAIMED"
    HEALTHY_ADVANCE = "HEALTHY_ADVANCE"


EXPORT_STATE_CODE = {
    StabilMainState.STABLE_UPTREND: 1,
    StabilMainState.HEALTHY_UPTREND: 2,
    StabilMainState.CONTROLLED_CORRECTION: 3,
    StabilMainState.RECOVERY_STARTING: 4,
    StabilMainState.OVEREXTENDED: 5,
    StabilMainState.UPTREND_WEAKENING: 6,
    StabilMainState.NOT_STABLE_UPTREND: 7,
}


@dataclass(frozen=True, slots=True)
class StabilTrendExport:
    ready: bool
    state: StabilMainState
    state_code: int | None
    direction: Direction
    health: float | None
    risk: float | None
    weekly_score: float | None
    daily_health_score: float | None
    h4_recovery_score: float | None
    daily_selling_pressure: float | None
    reason: StabilReason
    trend_score_band: int
    risk_band: int
    evidence_coverage: int
    weekly: WeeklyTrendSnapshot
    daily: DailyTrendSnapshot
    h4: H4TrendSnapshot
    h4_evidence: H4EvidenceStatus


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def _clamp100(v: float) -> float:
    return _clamp(v, 0.0, 100.0)


def _norm100(v: float | None, lo: float, hi: float) -> float:
    if v is None or hi <= lo:
        return 0.0
    return _clamp100((float(v) - lo) / (hi - lo) * 100.0)


def _weekly_up_context(state: WeeklyTrendState) -> bool:
    return state in {WeeklyTrendState.UP_STABLE, WeeklyTrendState.UP_WEAKENING, WeeklyTrendState.UP_PARABOLIC}


def _main_state(ctx: StabilTrendContext) -> StabilMainState:
    w, d, h = ctx.weekly, ctx.daily, ctx.h4
    core_ready = w.data_ready and d.data_ready
    if not core_ready:
        return StabilMainState.PENDING
    if w.state in {WeeklyTrendState.NOT_UP, WeeklyTrendState.TRANSITION}:
        return StabilMainState.NOT_STABLE_UPTREND
    if d.state in {DailyTrendState.STRUCTURE_BROKEN, DailyTrendState.DISTRIBUTION_RISK, DailyTrendState.LOCAL_RECOVERY, DailyTrendState.LOCAL_OVERHEAT}:
        return StabilMainState.NOT_STABLE_UPTREND
    if d.state == DailyTrendState.GAP_WATCH:
        return StabilMainState.UPTREND_WEAKENING
    if w.state == WeeklyTrendState.UP_PARABOLIC or d.state == DailyTrendState.PARABOLIC:
        return StabilMainState.OVEREXTENDED
    if d.state == DailyTrendState.HEALTHY_ADVANCE:
        return StabilMainState.STABLE_UPTREND if w.state == WeeklyTrendState.UP_STABLE else StabilMainState.HEALTHY_UPTREND
    if d.state in {DailyTrendState.CONTROLLED_PULLBACK, DailyTrendState.BASE_BUILDING}:
        return StabilMainState.RECOVERY_STARTING if h.data_ready and ctx.h4_evidence == H4EvidenceStatus.FRESH else StabilMainState.CONTROLLED_CORRECTION
    if d.state in {DailyTrendState.PULLBACK_TOO_DEEP, DailyTrendState.NEUTRAL, DailyTrendState.BEARISH_BALANCE}:
        return StabilMainState.UPTREND_WEAKENING
    return StabilMainState.UPTREND_WEAKENING if _weekly_up_context(w.state) else StabilMainState.NOT_STABLE_UPTREND


def _reason(ctx: StabilTrendContext) -> StabilReason:
    w, d, h = ctx.weekly, ctx.daily, ctx.h4
    core_ready = w.data_ready and d.data_ready
    correction = d.state in {DailyTrendState.CONTROLLED_PULLBACK, DailyTrendState.BASE_BUILDING}
    if not core_ready:
        return StabilReason.WAIT_WEEKLY if not w.data_ready else StabilReason.WAIT_DAILY
    if d.state == DailyTrendState.STRUCTURE_BROKEN:
        return StabilReason.GAP_CONFIRMED if d.gap_state == GapState.CONFIRMED else StabilReason.DAILY_STRUCTURE_BROKEN
    if d.state == DailyTrendState.DISTRIBUTION_RISK:
        return StabilReason.SELLING_EXPANSION
    if d.state == DailyTrendState.GAP_WATCH:
        return StabilReason.GAP_WATCH
    if w.state in {WeeklyTrendState.NOT_UP, WeeklyTrendState.TRANSITION}:
        return StabilReason.WEEKLY_NOT_UP
    if w.state == WeeklyTrendState.UP_PARABOLIC or d.state == DailyTrendState.PARABOLIC:
        return StabilReason.PARABOLIC
    if d.state == DailyTrendState.PULLBACK_TOO_DEEP:
        return StabilReason.DAILY_TOO_DEEP
    if correction and h.data_ready and ctx.h4_evidence == H4EvidenceStatus.ATTEMPT_FAILED:
        return StabilReason.CONTROLLED_H4_ATTEMPT_FAILED
    if correction and h.data_ready and ctx.h4_evidence == H4EvidenceStatus.FAILED:
        return StabilReason.CONTROLLED_H4_FAILED
    if correction and h.data_ready and ctx.h4_evidence == H4EvidenceStatus.OLD:
        return StabilReason.CONTROLLED_H4_OLD
    if not w.structure_usable or not w.structure_quality or not d.structure_usable or not d.structure_quality:
        return StabilReason.STRUCTURE_QUALITY_LOW
    if d.state == DailyTrendState.NEUTRAL:
        return StabilReason.DAILY_NEUTRAL
    if d.state == DailyTrendState.BEARISH_BALANCE:
        return StabilReason.BEARISH_BALANCE
    if correction:
        return StabilReason.CONTROLLED_H4_FRESH if h.data_ready and ctx.h4_evidence == H4EvidenceStatus.FRESH else StabilReason.CONTROLLED_H4_WAIT
    if d.state == DailyTrendState.HEALTHY_ADVANCE:
        if d.gap_state == GapState.RECLAIMED:
            return StabilReason.GAP_RECLAIMED
        return StabilReason.WEEKLY_WEAKENING if w.state == WeeklyTrendState.UP_WEAKENING else StabilReason.HEALTHY_ADVANCE
    return StabilReason.DAILY_NEUTRAL


def _weekly_quality_parts(w: WeeklyTrendSnapshot, cfg: StabilTrendConfig) -> tuple[bool, bool]:
    if not all((w.last_high, w.previous_high, w.last_low, w.previous_low)):
        return False, False
    assert w.last_high and w.previous_high and w.last_low and w.previous_low
    spacing = (w.last_high.origin_index - w.previous_high.origin_index >= cfg.weekly_pivot_len * 2 and w.last_low.origin_index - w.previous_low.origin_index >= cfg.weekly_pivot_len * 2)
    current_ref = max((w.last_high.atr_at_origin + w.last_low.atr_at_origin) * 0.5, cfg.min_tick)
    previous_ref = max((w.previous_high.atr_at_origin + w.previous_low.atr_at_origin) * 0.5, cfg.min_tick)
    excursion = bool(w.pivot_alternation_valid and abs(w.last_high.price - w.last_low.price) / current_ref >= 0.75 and abs(w.previous_high.price - w.previous_low.price) / previous_ref >= 0.75)
    return spacing, excursion


def _weekly_score(w: WeeklyTrendSnapshot, cfg: StabilTrendConfig) -> float | None:
    if not w.data_ready or not w.structure_usable or w.slope_atr is None or w.acceptance is None:
        return None
    spacing, excursion = _weekly_quality_parts(w, cfg)
    structure = 100.0 if w.higher_high and w.higher_low else 55.0 if (w.higher_high or w.higher_low) else 10.0
    slope = _norm100(w.slope_atr, -0.12, 0.30)
    acceptance = _clamp100(w.acceptance * 100.0)
    support = 15.0 if not w.support_held else 100.0 if w.support_age is not None and w.support_age <= cfg.weekly_pivot_len * 8 else 70.0 if w.support_age is not None and w.support_age <= cfg.weekly_pivot_len * 14 else 45.0
    if not w.higher_high and not w.higher_low:
        support = min(support, 45.0)
    quality = (35.0 if spacing else 0.0) + (35.0 if excursion else 0.0) + (30.0 if w.structure_quality else 0.0)
    raw = _clamp100(structure * 0.30 + slope * 0.20 + acceptance * 0.20 + support * 0.15 + quality * 0.15)
    if not w.structure_quality:
        raw = min(raw, 65.0)
    if w.state == WeeklyTrendState.TRANSITION:
        raw = min(raw, 55.0)
    if w.state == WeeklyTrendState.NOT_UP:
        raw = min(raw, 40.0)
    return _clamp100(raw)


def _selling_pressure(d: DailyTrendSnapshot) -> float | None:
    vals = (d.down_body_atr, d.red_share, d.expansion_count)
    if any(v is None for v in vals) or (d.volume_usable and d.sell_volume_factor is None):
        return None
    body = _norm100(d.down_body_atr, 0.25, 1.10)
    red = _clamp100(float(d.red_share) * 100.0)
    expansion = _clamp100(float(d.expansion_count) / 4.0 * 100.0)
    vol = _norm100(d.sell_volume_factor, 0.70, 1.60) if d.volume_usable else 0.0
    base = body * 0.29 + red * 0.23 + vol * 0.20 + expansion * 0.28 if d.volume_usable else body * 0.36 + red * 0.30 + expansion * 0.34
    if d.uncontrolled_selling:
        base = max(base, 70.0)
    if d.volume_usable and d.heavy_sell_volume:
        base = max(base, 58.0)
    return _clamp100(base)


def _daily_score(d: DailyTrendSnapshot, cfg: StabilTrendConfig) -> float | None:
    selling = _selling_pressure(d)
    required = (d.depth_atr, d.slope_atr, d.acceptance, d.range_compression, selling)
    if not d.data_ready or not d.structure_usable or any(v is None for v in required):
        return None
    pullback_context = d.state in {DailyTrendState.CONTROLLED_PULLBACK, DailyTrendState.BASE_BUILDING, DailyTrendState.PULLBACK_TOO_DEEP, DailyTrendState.GAP_WATCH} or d.raw_state in {DailyRawState.PULLBACK, DailyRawState.BALANCE}
    structure = 10.0 if d.support_broken or d.lower_low else 100.0 if d.support_held and d.higher_high and d.higher_low else 78.0 if d.support_held and d.higher_low else 35.0 if d.lower_high else 55.0
    depth_atr = float(d.depth_atr)
    depth_base = 100.0 - _clamp100(depth_atr / cfg.healthy_depth_atr * 35.0) if depth_atr <= cfg.healthy_depth_atr else 15.0 if depth_atr >= cfg.deep_depth_atr else 65.0 - _clamp100((depth_atr - cfg.healthy_depth_atr) / max(cfg.deep_depth_atr - cfg.healthy_depth_atr, 0.1) * 45.0)
    advancing = d.raw_state == DailyRawState.ADVANCE
    controlled = d.raw_state == DailyRawState.PULLBACK
    basing = d.raw_state == DailyRawState.BALANCE
    depth_score = 75.0 if advancing and depth_atr < 0.60 else depth_base
    duration = _clamp100(100.0 - _clamp100(float(d.pullback_bars) / max(float(cfg.max_pullback_bars), 1.0) * 65.0)) if pullback_context else 82.0 if advancing else 65.0
    ema_score = _norm100(d.slope_atr, -0.05, 0.25) * 0.50 + _clamp100(float(d.acceptance) * 100.0) * 0.50
    pressure_score = 100.0 - float(selling)
    compression = _norm100(1.05 - float(d.range_compression), 0.0, 0.35) if basing and d.support_held and not d.support_broken else 65.0 if controlled and d.support_held else 45.0
    raw = _clamp100(structure * 0.25 + depth_score * 0.20 + duration * 0.15 + ema_score * 0.15 + pressure_score * 0.15 + compression * 0.10)
    if d.state == DailyTrendState.STRUCTURE_BROKEN: raw = min(raw, 25.0)
    elif d.state == DailyTrendState.DISTRIBUTION_RISK: raw = min(raw, 35.0)
    elif d.state == DailyTrendState.PULLBACK_TOO_DEEP: raw = min(raw, 45.0)
    elif d.state == DailyTrendState.BEARISH_BALANCE: raw = min(raw, 50.0)
    elif d.state == DailyTrendState.GAP_WATCH: raw = min(raw, 55.0)
    elif d.state == DailyTrendState.HEALTHY_ADVANCE and not (d.support_held and (d.higher_high or d.higher_low)): raw = min(raw, 65.0)
    elif d.state == DailyTrendState.CONTROLLED_PULLBACK: raw = _clamp(raw, 50.0, 78.0)
    elif d.state == DailyTrendState.BASE_BUILDING and d.support_held: raw = _clamp(raw, 45.0, 68.0)
    return _clamp100(raw)


def _h4_score(h: H4TrendSnapshot, cfg: StabilTrendConfig) -> float | None:
    required = (h.close_location, h.acceptance, h.buyer_body_atr, h.buyer_body_average_ratio, h.close_ema_atr)
    if not h.data_ready or h.state == H4TrendState.PENDING or any(v is None for v in required) or (h.volume_usable and h.buyer_volume_factor is None):
        return None
    event_context = h.active_emergence or h.recovery_still_valid or h.state in {H4TrendState.BUYERS_EMERGING, H4TrendState.RECOVERY_CONFIRMED}
    displacement = 100.0 if h.bull_displacement and event_context else (_norm100(h.buyer_body_atr, 0.0, 0.45) + _norm100(h.buyer_body_average_ratio, 0.6, cfg.displacement_factor)) * 0.50
    micro = 100.0 if h.micro_higher_low else 25.0
    ema_score = _clamp100(float(h.acceptance) * 100.0) * 0.55 + _norm100(h.close_ema_atr, -0.20, 0.60) * 0.45
    close_score = _clamp100(float(h.close_location) * 100.0)
    seller = 100.0 if h.sellers_shrinking else 45.0
    volume_score = _norm100(h.buyer_volume_factor, 0.70, 1.50) if h.volume_usable and h.volume_pass else 20.0 if h.volume_usable else 0.0
    if h.state == H4TrendState.RECOVERY_CONFIRMED and h.recovery_age is not None:
        age = _clamp100(100.0 - float(h.recovery_age) / max(float(cfg.h4_evidence_fresh_bars * 2), 1.0) * 45.0)
    elif h.active_emergence and h.event_age is not None:
        age = _clamp100(82.0 - float(h.event_age) / max(float(cfg.h4_evidence_fresh_bars), 1.0) * 30.0)
    else:
        age = 55.0 if h.sellers_shrinking else 25.0
    base = displacement * 0.25 + micro * 0.20 + ema_score * 0.20 + close_score * 0.10 + seller * 0.10 + volume_score * 0.10 + age * 0.05 if h.volume_usable else displacement * 0.29 + micro * 0.20 + ema_score * 0.24 + close_score * 0.13 + seller * 0.09 + age * 0.05
    boost = 12.0 if h.state == H4TrendState.RECOVERY_CONFIRMED and h.recovery_still_valid else 6.0 if h.state == H4TrendState.BUYERS_EMERGING or h.active_emergence else 0.0 if h.state == H4TrendState.SELLING_WEAKENING else -18.0 if h.state == H4TrendState.NO_RECOVERY else -35.0
    raw = _clamp100(base + boost - (35.0 if h.recent_failure else 0.0))
    if h.state == H4TrendState.RECOVERY_FAILED or h.recent_failure: raw = min(raw, 25.0)
    elif h.state == H4TrendState.NO_RECOVERY: raw = min(raw, 40.0)
    elif h.state == H4TrendState.SELLING_WEAKENING: raw = _clamp(raw, 35.0, 58.0)
    elif h.state == H4TrendState.BUYERS_EMERGING: raw = _clamp(raw, 50.0, 78.0)
    elif h.state == H4TrendState.RECOVERY_CONFIRMED and h.recovery_still_valid: raw = _clamp(raw, 72.0, 95.0)
    return _clamp100(raw)


def _overall(weekly: float | None, daily: float | None, h4: float | None, w_state: WeeklyTrendState, d_state: DailyTrendState) -> float | None:
    if weekly is None or daily is None:
        return None
    base = weekly * 0.50 + daily * 0.50 if h4 is None else weekly * 0.40 + daily * 0.40 + h4 * 0.20
    if w_state == WeeklyTrendState.NOT_UP: base = min(base, 40.0)
    elif w_state == WeeklyTrendState.TRANSITION: base = min(base, 50.0)
    elif d_state == DailyTrendState.STRUCTURE_BROKEN: base = min(base, 30.0)
    elif d_state == DailyTrendState.DISTRIBUTION_RISK: base = min(base, 35.0)
    elif d_state == DailyTrendState.BEARISH_BALANCE: base = min(base, 45.0)
    if not _weekly_up_context(w_state):
        base = min(base, weekly * 0.70 + daily * 0.30)
    return _clamp100(base)


def _risk(ctx: StabilTrendContext, selling: float | None, cfg: StabilTrendConfig) -> float | None:
    if selling is None:
        return None
    w, d, h = ctx.weekly, ctx.daily, ctx.h4
    structural = 100.0 if (d.state == DailyTrendState.STRUCTURE_BROKEN or d.support_broken) and d.gap_state == GapState.CONFIRMED else 85.0 if (d.state == DailyTrendState.STRUCTURE_BROKEN or d.support_broken) else 60.0 if d.state == DailyTrendState.GAP_WATCH else 45.0 if d.state == DailyTrendState.BEARISH_BALANCE else 15.0
    selling_risk = max(float(selling), 82.0 if d.state == DailyTrendState.DISTRIBUTION_RISK else 70.0 if d.uncontrolled_selling else 58.0 if d.heavy_sell_volume else float(selling))
    depth_risk = _norm100(d.depth_atr, cfg.healthy_depth_atr, cfg.deep_depth_atr) if d.depth_atr is not None else 0.0
    duration_risk = _norm100(float(d.pullback_bars), float(cfg.max_pullback_bars), float(cfg.max_pullback_bars) * 1.75)
    correction = max(depth_risk, duration_risk)
    context = 80.0 if w.state == WeeklyTrendState.NOT_UP else 60.0 if w.state == WeeklyTrendState.TRANSITION else 45.0 if w.slope_atr is not None and w.slope_atr <= 0.0 else 30.0 if w.slope_atr is not None and w.slope_atr < 0.10 else 15.0
    context = max(context, 70.0 if h.recent_failure else 0.0)
    return _clamp100(structural * 0.35 + selling_risk * 0.30 + correction * 0.20 + context * 0.15)


def _stabilize(raw: float | None, previous: float | None, step: float, fast: bool) -> float | None:
    if raw is None:
        return None
    if previous is None or fast:
        return raw
    return previous + _clamp(raw - previous, -step, step)


def _score_band(value: float | None, previous: int) -> int:
    if value is None: return 0
    if previous <= 0: return 1 if value < 30 else 2 if value < 50 else 3 if value < 65 else 4 if value < 80 else 5
    result = previous
    if previous == 1 and value >= 32: result = 2
    elif previous == 2 and value >= 52: result = 3
    elif previous == 3 and value >= 67: result = 4
    elif previous == 4 and value >= 82: result = 5
    elif previous == 5 and value < 78: result = 4
    elif previous == 4 and value < 63: result = 3
    elif previous == 3 and value < 48: result = 2
    elif previous == 2 and value < 28: result = 1
    return result


def _risk_band(value: float | None, previous: int) -> int:
    if value is None: return 0
    if previous <= 0: return 1 if value < 25 else 2 if value < 50 else 3 if value < 70 else 4
    result = previous
    if previous == 1 and value >= 27: result = 2
    elif previous == 2 and value >= 52: result = 3
    elif previous == 3 and value >= 72: result = 4
    elif previous == 4 and value < 68: result = 3
    elif previous == 3 and value < 48: result = 2
    elif previous == 2 and value < 23: result = 1
    return result


class StabilTrendEngine:
    """Final Stabil Trend engine: closed W/D/H4 lifecycle -> resolver -> stabilized export."""

    name = "stabil_trend"

    def __init__(self, config: StabilTrendConfig | None = None) -> None:
        self.config = config or StabilTrendConfig()
        self._lifecycle = _LifecycleEngine(self.config)
        self._export: StabilTrendExport | None = None
        self._last_w_time = self._last_d_time = self._last_h_time = None
        self._weekly_score = self._daily_score = self._h4_score = None
        self._health = self._risk = None
        self._trend_band = self._risk_band = 0

    def analyze(self, weekly: pd.DataFrame, daily: pd.DataFrame, h4: pd.DataFrame, *, as_of: Any | None = None) -> StabilTrendExport:
        ctx = self._lifecycle.analyze(weekly, daily, h4, as_of=as_of)
        w_raw, d_raw, h_raw = _weekly_score(ctx.weekly, self.config), _daily_score(ctx.daily, self.config), _h4_score(ctx.h4, self.config)
        selling = _selling_pressure(ctx.daily)
        overall_raw = _overall(w_raw, d_raw, h_raw, ctx.weekly.state, ctx.daily.state)
        risk_raw = _risk(ctx, selling, self.config)
        w_adv = ctx.weekly.timestamp is not None and ctx.weekly.timestamp != self._last_w_time
        d_adv = ctx.daily.timestamp is not None and ctx.daily.timestamp != self._last_d_time
        h_adv = ctx.h4.timestamp is not None and ctx.h4.timestamp != self._last_h_time
        daily_adv = d_adv or w_adv
        composite_adv = w_adv or d_adv or h_adv
        critical = ctx.daily.state in {DailyTrendState.STRUCTURE_BROKEN, DailyTrendState.DISTRIBUTION_RISK} or ctx.daily.gap_state == GapState.CONFIRMED or ctx.h4.state == H4TrendState.RECOVERY_FAILED or ctx.weekly.state == WeeklyTrendState.NOT_UP
        if w_adv:
            self._weekly_score = _stabilize(w_raw, self._weekly_score, 5.0, ctx.weekly.state == WeeklyTrendState.NOT_UP)
            self._last_w_time = ctx.weekly.timestamp
        if daily_adv:
            fast = ctx.daily.state in {DailyTrendState.STRUCTURE_BROKEN, DailyTrendState.DISTRIBUTION_RISK} or ctx.daily.gap_state == GapState.CONFIRMED
            self._daily_score = _stabilize(d_raw, self._daily_score, 7.0, fast)
            if d_adv: self._last_d_time = ctx.daily.timestamp
        if h_adv:
            self._h4_score = _stabilize(h_raw, self._h4_score, 10.0, ctx.h4.state == H4TrendState.RECOVERY_FAILED or ctx.h4.recent_failure)
            self._last_h_time = ctx.h4.timestamp
        if composite_adv:
            self._health = _stabilize(overall_raw, self._health, 6.0, critical)
            self._risk = _stabilize(risk_raw, self._risk, 8.0, critical)
            self._trend_band = _score_band(self._health, self._trend_band)
            self._risk_band = _risk_band(self._risk, self._risk_band)
        state = _main_state(ctx)
        reason = _reason(ctx)
        ready = ctx.weekly.data_ready and ctx.daily.data_ready
        direction = Direction.UP if state in {StabilMainState.STABLE_UPTREND, StabilMainState.HEALTHY_UPTREND, StabilMainState.RECOVERY_STARTING} else Direction.NEUTRAL
        self._export = StabilTrendExport(ready, state, EXPORT_STATE_CODE.get(state) if ready else None, direction, self._health if ready else None, self._risk if ready else None, self._weekly_score, self._daily_score, self._h4_score, selling, reason, self._trend_band, self._risk_band, int(ctx.weekly.data_ready) + int(ctx.daily.data_ready) + int(ctx.h4.data_ready), ctx.weekly, ctx.daily, ctx.h4, ctx.h4_evidence)
        return self._export

    def export(self) -> StabilTrendExport | None:
        return self._export

    def snapshot(self) -> StabilTrendExport | None:
        return self._export

    def engine_result(self) -> EngineResult | None:
        e = self._export
        if e is None:
            return None
        return EngineResult(engine=self.name, state=e.state.value, timestamp=e.h4.timestamp or e.daily.timestamp or e.weekly.timestamp, direction=e.direction, score=e.health, quality=None, levels={}, events=(), reasons=(e.reason.value,), is_confirmed=True)
