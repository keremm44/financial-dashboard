from __future__ import annotations

from typing import Any

import pandas as pd

from .models import Direction, EngineResult
from .stabil_trend_engine import DailyTrendState, GapState, H4TrendState, StabilTrendConfig, WeeklyTrendState
from .stabil_trend_final import (
    EXPORT_STATE_CODE,
    StabilMainState,
    StabilReason,
    StabilTrendExport,
    _daily_score,
    _h4_score,
    _main_state,
    _overall,
    _reason,
    _risk,
    _risk_band,
    _score_band,
    _selling_pressure,
    _stabilize,
    _weekly_score,
)
from .stabil_trend_runtime import StabilTrendEngine as _LifecycleEngine


def _advanced(current: Any | None, previous: Any | None) -> bool:
    return current is not None and (previous is None or current > previous)


class StabilTrendEngine:
    """Final public Stabil Trend engine with source-snapshot-gated score smoothing."""

    name = "stabil_trend"

    def __init__(self, config: StabilTrendConfig | None = None) -> None:
        self.config = config or StabilTrendConfig()
        self._lifecycle = _LifecycleEngine(self.config)
        self._export: StabilTrendExport | None = None
        self._last_w_time = self._last_d_time = self._last_h_time = None
        self._weekly_score = self._daily_health_score = self._h4_recovery_score = None
        self._health = self._risk_score = None
        self._trend_band = self._risk_band_value = 0

    def analyze(
        self,
        weekly: pd.DataFrame,
        daily: pd.DataFrame,
        h4: pd.DataFrame,
        *,
        as_of: Any | None = None,
    ) -> StabilTrendExport:
        ctx = self._lifecycle.analyze(weekly, daily, h4, as_of=as_of)
        w_raw = _weekly_score(ctx.weekly, self.config)
        d_raw = _daily_score(ctx.daily, self.config)
        h_raw = _h4_score(ctx.h4, self.config)
        selling = _selling_pressure(ctx.daily)
        overall_raw = _overall(w_raw, d_raw, h_raw, ctx.weekly.state, ctx.daily.state)
        risk_raw = _risk(ctx, selling, self.config)

        w_adv = _advanced(ctx.weekly.timestamp, self._last_w_time)
        d_adv = _advanced(ctx.daily.timestamp, self._last_d_time)
        h_adv = _advanced(ctx.h4.timestamp, self._last_h_time)
        daily_score_adv = d_adv or w_adv
        composite_adv = w_adv or d_adv or h_adv
        critical = (
            ctx.daily.state in {DailyTrendState.STRUCTURE_BROKEN, DailyTrendState.DISTRIBUTION_RISK}
            or ctx.daily.gap_state == GapState.CONFIRMED
            or ctx.h4.state == H4TrendState.RECOVERY_FAILED
            or ctx.weekly.state == WeeklyTrendState.NOT_UP
        )

        if w_adv:
            self._weekly_score = _stabilize(
                w_raw, self._weekly_score, 5.0, ctx.weekly.state == WeeklyTrendState.NOT_UP
            )
            self._last_w_time = ctx.weekly.timestamp

        if daily_score_adv:
            fast_daily = (
                ctx.daily.state in {DailyTrendState.STRUCTURE_BROKEN, DailyTrendState.DISTRIBUTION_RISK}
                or ctx.daily.gap_state == GapState.CONFIRMED
            )
            self._daily_health_score = _stabilize(d_raw, self._daily_health_score, 7.0, fast_daily)
            if d_adv:
                self._last_d_time = ctx.daily.timestamp

        if h_adv:
            self._h4_recovery_score = _stabilize(
                h_raw,
                self._h4_recovery_score,
                10.0,
                ctx.h4.state == H4TrendState.RECOVERY_FAILED or ctx.h4.recent_failure,
            )
            self._last_h_time = ctx.h4.timestamp

        if composite_adv:
            self._health = _stabilize(overall_raw, self._health, 6.0, critical)
            self._risk_score = _stabilize(risk_raw, self._risk_score, 8.0, critical)
            self._trend_band = _score_band(self._health, self._trend_band)
            self._risk_band_value = _risk_band(self._risk_score, self._risk_band_value)

        state = _main_state(ctx)
        reason = _reason(ctx)
        ready = ctx.weekly.data_ready and ctx.daily.data_ready
        direction = (
            Direction.UP
            if state in {
                StabilMainState.STABLE_UPTREND,
                StabilMainState.HEALTHY_UPTREND,
                StabilMainState.RECOVERY_STARTING,
            }
            else Direction.NEUTRAL
        )
        self._export = StabilTrendExport(
            ready=ready,
            state=state,
            state_code=EXPORT_STATE_CODE.get(state) if ready else None,
            direction=direction,
            health=self._health if ready else None,
            risk=self._risk_score if ready else None,
            weekly_score=self._weekly_score,
            daily_health_score=self._daily_health_score,
            h4_recovery_score=self._h4_recovery_score,
            daily_selling_pressure=selling,
            reason=reason,
            trend_score_band=self._trend_band,
            risk_band=self._risk_band_value,
            evidence_coverage=int(ctx.weekly.data_ready) + int(ctx.daily.data_ready) + int(ctx.h4.data_ready),
            weekly=ctx.weekly,
            daily=ctx.daily,
            h4=ctx.h4,
            h4_evidence=ctx.h4_evidence,
        )
        return self._export

    def export(self) -> StabilTrendExport | None:
        return self._export

    def snapshot(self) -> StabilTrendExport | None:
        return self._export

    def engine_result(self) -> EngineResult | None:
        e = self._export
        if e is None:
            return None
        timestamp = e.h4.timestamp if e.h4.timestamp is not None else e.daily.timestamp if e.daily.timestamp is not None else e.weekly.timestamp
        return EngineResult(
            engine=self.name,
            state=e.state.value,
            timestamp=timestamp,
            direction=e.direction,
            score=e.health,
            quality=None,
            levels={},
            events=(),
            reasons=(e.reason.value,),
            is_confirmed=True,
        )
