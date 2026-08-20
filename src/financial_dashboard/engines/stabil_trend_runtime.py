from __future__ import annotations

from typing import Any

import pandas as pd

from .stabil_trend_engine import (
    DailyRawState,
    DailyTrendSnapshot,
    DailyTrendState,
    GapState,
    H4EvidenceStatus,
    H4Lifecycle,
    H4TrendSnapshot,
    H4TrendState,
    StabilTrendConfig,
    StabilTrendContext,
    _alternates,
    _atr,
    _clean,
    _confirmed_pivots,
    _daily_context,
    _ema,
    _h4_evidence,
    _normalized_slope,
    _rma,
    _safe_div,
    _true_ranges,
    _weekly_snapshot,
)


def _daily_snapshot_runtime(frame: pd.DataFrame, weekly_state, cfg: StabilTrendConfig) -> DailyTrendSnapshot:
    if frame.empty:
        return DailyTrendSnapshot()
    atr = _atr(frame)
    ema = _ema(frame["close"], cfg.daily_ema_len)
    atr5, atr20 = _rma(_true_ranges(frame), 5), _rma(_true_ranges(frame), 20)
    volume_avg = frame["volume"].astype(float).rolling(20).mean()
    positive_share = (frame["volume"].fillna(0).astype(float) > 0).astype(float).rolling(20).mean()
    acceptance_series = (frame["close"].astype(float) > ema).astype(float).rolling(cfg.acceptance_len).mean()
    highs, lows = _confirmed_pivots(frame, cfg.daily_pivot_len, atr)

    pull_origin = None
    pull_origin_index = None
    pull_start = None
    pull_ref_atr = None
    gap_start = None
    last_above_support = None
    last_validated_up = None
    prev_provisional = False
    previous_floor = None
    last = DailyTrendSnapshot()

    for i in range(len(frame)):
        row = frame.iloc[i]
        known_h = [p for p in highs if p.known_index <= i]
        known_l = [p for p in lows if p.known_index <= i]
        enough = len(known_h) >= 2 and len(known_l) >= 2
        atr_i = atr[i]
        acceptance = None if pd.isna(acceptance_series.iloc[i]) else float(acceptance_series.iloc[i])
        ema_past = float(ema.iloc[i - cfg.slope_lookback]) if i >= cfg.slope_lookback else None
        history_ready = i > max(cfg.daily_ema_len + cfg.slope_lookback, cfg.pullback_lookback) + cfg.daily_pivot_len * 8
        data_ready = history_ready and enough and atr_i is not None and ema_past is not None and acceptance is not None
        if not enough:
            last = DailyTrendSnapshot(timestamp=row.timestamp)
            continue

        lh, ph, ll, pl = known_h[-1], known_h[-2], known_l[-1], known_l[-2]
        usable = data_ready and lh.origin_index > ph.origin_index and ll.origin_index > pl.origin_index
        alternating = bool(usable and _alternates(lh, ph, ll, pl))
        spacing = bool(usable and lh.origin_index - ph.origin_index >= cfg.daily_pivot_len * 2 and ll.origin_index - pl.origin_index >= cfg.daily_pivot_len * 2)
        excursion = bool(alternating and abs(lh.price - ll.price) / max((lh.atr_at_origin + ll.atr_at_origin) * 0.5, cfg.min_tick) >= 0.75 and abs(ph.price - pl.price) / max((ph.atr_at_origin + pl.atr_at_origin) * 0.5, cfg.min_tick) >= 0.75)
        support_age = i - ll.origin_index if usable else None
        support_fresh = bool(usable and support_age is not None and support_age <= cfg.pullback_lookback * 3)
        quality = bool(usable and spacing and excursion and support_fresh)
        slope = _normalized_slope(float(ema.iloc[i]), ema_past, float(atr_i), cfg.slope_lookback) if data_ready and atr_i else None
        hh, hl = bool(usable and lh.price > ph.price), bool(usable and ll.price > pl.price)
        lower_h, lower_l = bool(usable and lh.price < ph.price), bool(usable and ll.price < pl.price)
        floor = ll.price - ll.atr_at_origin * cfg.support_atr_tolerance if usable else None
        below = bool(usable and floor is not None and float(row.close) < floor)
        if not below:
            last_above_support = i

        if usable and floor is not None and i > 0:
            prior_floor = previous_floor if previous_floor is not None else floor
            if float(row.open) < floor and float(frame.iloc[i - 1].close) >= prior_floor and abs(float(row.open) - float(frame.iloc[i - 1].close)) >= float(atr_i) * 0.20:
                gap_start = i
        gap_active = gap_start is not None and i - gap_start <= 2

        red_indices = [j for j in range(max(0, i - 4), i + 1) if float(frame.iloc[j].close) < float(frame.iloc[j].open) and atr[j] is not None]
        down_body_atr = sum(abs(float(frame.iloc[j].close) - float(frame.iloc[j].open)) / float(atr[j]) for j in red_indices) / len(red_indices) if red_indices else 0.0
        red_share = len(red_indices) / min(5.0, float(i + 1))
        vol_usable = bool(i >= 19 and not pd.isna(volume_avg.iloc[i]) and float(volume_avg.iloc[i]) > 0 and not pd.isna(positive_share.iloc[i]) and float(positive_share.iloc[i]) >= 0.50)
        seller_bars = [j for j in range(max(0, i - 7), i + 1) if float(frame.iloc[j].close) < float(frame.iloc[j].open) and vol_usable]
        sell_factor = (sum(float(frame.iloc[j].volume) for j in seller_bars) / len(seller_bars)) / float(volume_avg.iloc[i]) if seller_bars and vol_usable else 1.0
        sell_share = len(seller_bars) / min(8.0, float(i + 1))
        expansion_count = 0.0
        for j in range(max(1, i - 3), i + 1):
            if atr[j] is not None and float(frame.iloc[j].close) < float(frame.iloc[j].open) and abs(float(frame.iloc[j].close) - float(frame.iloc[j].open)) > float(atr[j]) * 0.85 and float(frame.iloc[j].close) < float(frame.iloc[j - 1].low):
                expansion_count += 1.0
        uncontrolled = bool(data_ready and expansion_count >= 2.0 and down_body_atr * red_share > 0.55 and red_share >= 0.60)
        heavy = bool(data_ready and vol_usable and sell_factor * sell_share > 1.25 and red_share >= 0.55)
        selling_continues = bool(data_ready and ((i > 0 and float(row.close) < float(frame.iloc[i - 1].close)) or uncontrolled or heavy))
        bars_since_above = i - last_above_support if last_above_support is not None else i + 1
        gap_confirmed = bool(below and gap_active and bars_since_above >= 2 and selling_continues)
        direct_break = bool(below and not gap_active)
        gap_watch = bool(below and gap_active and not gap_confirmed)
        gap_reclaimed = bool(usable and floor is not None and float(row.close) >= floor and gap_start is not None and i - gap_start <= 2)
        support_broken = direct_break or gap_confirmed

        recent_start = max(0, i - cfg.pullback_lookback + 1)
        recent_slice = frame.iloc[recent_start : i + 1]
        recent_high = float(recent_slice["high"].max())
        recent_origin = int(recent_slice["high"].astype(float).idxmax())
        provisional_depth = _safe_div(recent_high - float(row.close), float(atr_i)) if data_ready and atr_i else None
        provisional = bool(data_ready and usable and not support_broken and provisional_depth is not None and provisional_depth > 0.60)
        if provisional and not prev_provisional and atr_i is not None:
            pull_origin, pull_origin_index, pull_start, pull_ref_atr = recent_high, recent_origin, i, float(atr_i)
        prev_provisional = provisional
        if pull_origin is not None and (float(row.close) >= float(pull_origin) or support_broken):
            pull_origin, pull_origin_index, pull_start, pull_ref_atr = None, None, None, None
        active = pull_origin is not None and pull_origin_index is not None and pull_start is not None and pull_ref_atr is not None
        depth = _safe_div(float(pull_origin) - float(row.close), pull_ref_atr) if active else provisional_depth
        pull_bars = max(i - int(pull_start), 0) if active else 0
        support_held = bool(usable and not support_broken and not gap_watch)
        low_support_atr = _safe_div(float(row.low) - ll.price, float(atr_i)) if usable and atr_i else None
        prolonged = bool(data_ready and pull_bars > cfg.max_pullback_bars)
        pull_active = bool(data_ready and active and depth is not None and depth > 0.60 and not support_broken)
        advancing = bool(data_ready and usable and quality and float(row.close) > float(ema.iloc[i]) and slope is not None and slope > 0.10 and acceptance >= 0.625 and (hh or lh.price >= ph.price) and support_held)
        controlled = bool(data_ready and usable and quality and pull_active and support_held and depth is not None and depth <= cfg.healthy_depth_atr and not uncontrolled and not heavy and not prolonged)
        compression = _safe_div(float(atr5[i]), float(atr20[i])) if atr5[i] is not None and atr20[i] is not None else None
        basing = bool(data_ready and usable and quality and pull_active and support_held and pull_bars >= 5 and compression is not None and compression < 0.85 and slope is not None and abs(slope) <= 0.18 and not uncontrolled)
        stretch = _safe_div(float(row.close) - float(ema.iloc[i]), float(atr_i)) if atr_i else None
        parabolic = bool(data_ready and usable and quality and hh and hl and float(row.close) > float(ema.iloc[i]) and slope is not None and slope > 0.35 and stretch is not None and stretch > 4.0)
        validated = bool(data_ready and usable and quality and hh and hl and support_held and slope is not None and slope > 0.08 and acceptance >= 0.60)
        if validated:
            last_validated_up = i
        prior_up = bool(data_ready and usable and not support_broken and not lower_l and (validated or (last_validated_up is not None and i - last_validated_up <= cfg.pullback_lookback * 2)))
        bearish = bool(data_ready and usable and (lower_h or lower_l or (slope is not None and slope < -0.05)) and not validated)
        gap = GapState.CONFIRMED if gap_confirmed else GapState.WATCH if gap_watch else GapState.RECLAIMED if gap_reclaimed else GapState.NONE

        raw = DailyRawState.PENDING
        if data_ready:
            if not usable: raw = DailyRawState.NEUTRAL
            elif support_broken: raw = DailyRawState.STRUCTURE_BROKEN
            elif gap_watch: raw = DailyRawState.GAP_WATCH
            elif uncontrolled or (heavy and depth is not None and depth > cfg.healthy_depth_atr): raw = DailyRawState.DISTRIBUTION
            elif parabolic: raw = DailyRawState.PARABOLIC
            elif (depth is not None and depth >= cfg.deep_depth_atr) or (prolonged and not hl): raw = DailyRawState.TOO_DEEP
            elif advancing: raw = DailyRawState.ADVANCE
            elif basing: raw = DailyRawState.BALANCE
            elif controlled: raw = DailyRawState.PULLBACK
            else: raw = DailyRawState.NEUTRAL
        state = _daily_context(raw, weekly_state, prior_up, bearish)
        last = DailyTrendSnapshot(row.timestamp, data_ready, usable, quality, alternating, support_fresh, support_held, vol_usable, raw, state, ll.price if usable else None, floor, support_age, gap, pull_start, frame.iloc[pull_start].timestamp if pull_start is not None else None, pull_origin, pull_origin_index, pull_ref_atr, depth, pull_bars, slope, acceptance, down_body_atr, red_share, sell_factor, expansion_count, compression, low_support_atr, hh, hl, lower_h, lower_l, uncontrolled, heavy, support_broken, prior_up, bearish)
        previous_floor = floor
    return last


def _h4_snapshot_runtime(frame: pd.DataFrame, cfg: StabilTrendConfig) -> H4TrendSnapshot:
    if frame.empty:
        return H4TrendSnapshot()
    atr = _atr(frame)
    ema = _ema(frame["close"], cfg.h4_fast_ema_len)
    bodies = (frame["close"].astype(float) - frame["open"].astype(float)).abs()
    body_avg = bodies.rolling(12).mean()
    acceptance_series = (frame["close"].astype(float) > ema).astype(float).rolling(5).mean()
    volume_avg = frame["volume"].astype(float).rolling(20).mean()
    positive_share = (frame["volume"].fillna(0).astype(float) > 0).astype(float).rolling(20).mean()
    _, lows = _confirmed_pivots(frame, cfg.h4_micro_pivot_len, atr)

    lifecycle = H4Lifecycle.NO_EVENT
    event_i = recovery_i = failure_i = invalidation_i = None
    event_low = event_mid = None
    seller_indices: list[int] = []
    weakness_indices: list[int] = []
    last = H4TrendSnapshot()

    for i in range(len(frame)):
        row = frame.iloc[i]
        atr_i = atr[i]
        acceptance = None if pd.isna(acceptance_series.iloc[i]) else float(acceptance_series.iloc[i])
        history_ready = i > cfg.h4_fast_ema_len + cfg.h4_micro_pivot_len * 8 + 24
        data_ready = bool(history_ready and atr_i is not None and not pd.isna(body_avg.iloc[i]) and acceptance is not None and i >= 3)
        body = abs(float(row.close) - float(row.open))
        candle_range = float(row.high) - float(row.low)
        close_loc = (float(row.close) - float(row.low)) / candle_range if candle_range > cfg.min_tick * 0.10 else 0.50
        lower_wick = min(float(row.open), float(row.close)) - float(row.low)
        lower_wick_ratio = lower_wick / candle_range if candle_range > cfg.min_tick * 0.10 else 0.0
        vol_usable = bool(i >= 19 and not pd.isna(volume_avg.iloc[i]) and float(volume_avg.iloc[i]) > 0 and not pd.isna(positive_share.iloc[i]) and float(positive_share.iloc[i]) >= 0.50)
        buyer_vol_factor = float(row.volume) / float(volume_avg.iloc[i]) if vol_usable else 1.0
        volume_pass = not vol_usable or buyer_vol_factor >= 1.0
        buyer_body = body if float(row.close) > float(row.open) else 0.0
        buyer_body_atr = _safe_div(buyer_body, float(atr_i)) if atr_i else None
        buyer_body_average_ratio = _safe_div(buyer_body, float(body_avg.iloc[i])) if not pd.isna(body_avg.iloc[i]) else None
        bull_displacement = bool(data_ready and float(row.close) > float(row.open) and body > float(body_avg.iloc[i]) * cfg.displacement_factor and buyer_body >= float(atr_i) * 0.45 and close_loc >= 0.70 and volume_pass)

        known_lows = [p for p in lows if p.known_index <= i]
        last_pivot = known_lows[-1] if known_lows else None
        previous_pivot = known_lows[-2] if len(known_lows) >= 2 else None
        micro_hl = bool(last_pivot and previous_pivot and last_pivot.origin_index > previous_pivot.origin_index and last_pivot.price > previous_pivot.price)

        current_seller = float(row.close) < float(row.open)
        shrinking_raw = False
        if current_seller:
            prior = [j for j in seller_indices if i - j <= 8][-3:]
            if len(prior) >= 2:
                avg_body = sum(abs(float(frame.iloc[j].close) - float(frame.iloc[j].open)) for j in prior) / len(prior)
                body_shrink = body < avg_body * 0.72
                wick_evidence = lower_wick_ratio >= 0.22
                no_fresh_low = i >= 3 and float(row.low) >= float(frame.iloc[i - 3 : i]["low"].min())
                close_recovery = close_loc >= 0.45
                shrinking_raw = body_shrink and (wick_evidence or no_fresh_low or close_recovery)
            seller_indices.append(i)
            if shrinking_raw:
                weakness_indices.append(i)
        sellers_shrinking = any(i - j <= 2 for j in weakness_indices)

        recovery_age_before = i - recovery_i if recovery_i is not None else None
        candidate_active = lifecycle in {H4Lifecycle.DISPLACEMENT_ACTIVE, H4Lifecycle.BUYERS_EMERGING}
        old_recovery_renewable = lifecycle == H4Lifecycle.RECOVERY_CONFIRMED and recovery_age_before is not None and recovery_age_before > cfg.h4_evidence_fresh_bars
        can_start = bool(bull_displacement and (lifecycle == H4Lifecycle.NO_EVENT or (lifecycle == H4Lifecycle.RECOVERY_FAILED and failure_i is not None and i > failure_i) or old_recovery_renewable))

        if data_ready:
            if can_start:
                event_i, event_low, event_mid = i, float(row.low), (float(row.open) + float(row.close)) * 0.5
                recovery_i = invalidation_i = failure_i = None
                lifecycle = H4Lifecycle.DISPLACEMENT_ACTIVE
            else:
                event_age = i - event_i if event_i is not None else None
                recovery_age = i - recovery_i if recovery_i is not None else None
                failure_age = i - failure_i if failure_i is not None else None
                event_within = event_age is not None and event_age <= cfg.h4_evidence_fresh_bars * 2
                micro_for_event = bool(micro_hl and event_i is not None and last_pivot is not None and last_pivot.origin_index >= event_i)
                follow = bool(event_within and event_mid is not None and event_i is not None and i > event_i and float(row.close) > event_mid and float(row.close) > float(ema.iloc[i]) and float(ema.iloc[i]) > float(ema.iloc[i - 3]) and acceptance >= 0.60 and float(row.close) >= float(frame.iloc[i - 1].close) and micro_for_event)
                if candidate_active:
                    if not event_within:
                        lifecycle, event_i, event_low, event_mid, recovery_i, invalidation_i, failure_i = H4Lifecycle.NO_EVENT, None, None, None, None, None, None
                    elif event_low is not None and float(row.close) < event_low:
                        invalidation_i = failure_i = i
                        lifecycle = H4Lifecycle.RECOVERY_FAILED
                    elif follow:
                        recovery_i = i
                        lifecycle = H4Lifecycle.RECOVERY_CONFIRMED
                    else:
                        lifecycle = H4Lifecycle.BUYERS_EMERGING
                elif lifecycle == H4Lifecycle.RECOVERY_CONFIRMED:
                    if event_low is not None and float(row.close) < event_low:
                        invalidation_i = failure_i = i
                        lifecycle = H4Lifecycle.RECOVERY_FAILED
                    elif recovery_age is not None and recovery_age > cfg.h4_evidence_fresh_bars * 2:
                        lifecycle, event_i, event_low, event_mid, recovery_i, invalidation_i, failure_i = H4Lifecycle.NO_EVENT, None, None, None, None, None, None
                elif lifecycle == H4Lifecycle.RECOVERY_FAILED and failure_age is not None and failure_age > cfg.h4_evidence_fresh_bars:
                    lifecycle, event_i, event_low, event_mid, recovery_i, invalidation_i, failure_i = H4Lifecycle.NO_EVENT, None, None, None, None, None, None

        event_age = i - event_i if event_i is not None else None
        recovery_age = i - recovery_i if recovery_i is not None else None
        failure_age = i - failure_i if failure_i is not None else None
        retained = lifecycle == H4Lifecycle.RECOVERY_CONFIRMED and recovery_age is not None and recovery_age <= cfg.h4_evidence_fresh_bars * 2
        recovery_valid = bool(retained and event_mid is not None and float(row.close) > event_mid and float(row.close) > float(ema.iloc[i]))
        active_emergence = bool(lifecycle in {H4Lifecycle.DISPLACEMENT_ACTIVE, H4Lifecycle.BUYERS_EMERGING} and event_age is not None and event_age <= cfg.h4_evidence_fresh_bars)
        recent_failure = bool(lifecycle == H4Lifecycle.RECOVERY_FAILED and failure_age is not None and failure_age <= cfg.h4_evidence_fresh_bars)
        state = H4TrendState.PENDING
        if data_ready:
            state = H4TrendState.RECOVERY_FAILED if recent_failure else H4TrendState.RECOVERY_CONFIRMED if recovery_valid else H4TrendState.BUYERS_EMERGING if active_emergence else H4TrendState.SELLING_WEAKENING if sellers_shrinking else H4TrendState.NO_RECOVERY
        close_ema_atr = _safe_div(float(row.close) - float(ema.iloc[i]), float(atr_i)) if atr_i else None
        last = H4TrendSnapshot(row.timestamp, data_ready, vol_usable, state, lifecycle, event_i, frame.iloc[event_i].timestamp if event_i is not None else None, event_low, event_mid, recovery_i, frame.iloc[recovery_i].timestamp if recovery_i is not None else None, invalidation_i, frame.iloc[invalidation_i].timestamp if invalidation_i is not None else None, event_age, recovery_age, failure_age, last_pivot, bull_displacement, micro_hl, sellers_shrinking, close_loc, acceptance, buyer_vol_factor, volume_pass, buyer_body_atr, buyer_body_average_ratio, close_ema_atr, recovery_valid, active_emergence, recent_failure)
    return last


class StabilTrendEngine:
    """Public Tur-1 Stabil Trend runtime. Final MAIN state/scoring belongs to Tur-2."""

    def __init__(self, config: StabilTrendConfig | None = None) -> None:
        self.config = config or StabilTrendConfig()
        self._snapshot: StabilTrendContext | None = None

    def analyze(self, weekly: pd.DataFrame, daily: pd.DataFrame, h4: pd.DataFrame, *, as_of: Any | None = None) -> StabilTrendContext:
        w, d, h = _clean(weekly, as_of), _clean(daily, as_of), _clean(h4, as_of)
        weekly_snapshot = _weekly_snapshot(w, self.config)
        daily_snapshot = _daily_snapshot_runtime(d, weekly_snapshot.state, self.config)
        h4_snapshot = _h4_snapshot_runtime(h, self.config)
        evidence = _h4_evidence(daily_snapshot, h4_snapshot, self.config)
        timestamps = [x for x in (weekly_snapshot.timestamp, daily_snapshot.timestamp, h4_snapshot.timestamp) if x is not None]
        self._snapshot = StabilTrendContext(max(timestamps) if timestamps else as_of, weekly_snapshot, daily_snapshot, h4_snapshot, evidence)
        return self._snapshot

    def snapshot(self) -> StabilTrendContext | None:
        return self._snapshot
