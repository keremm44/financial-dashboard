from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .base import BaseEngine
from .models import Direction, EngineResult


@dataclass(frozen=True, slots=True)
class AuctionPreset:
    name: str
    lookback: int
    bins: int
    hvn_rel_min: float
    hvn_mean_mult: float
    lvn_rel_max: float
    lvn_mean_max: float
    lvn_depth_min: float
    node_min_separation_bins: int
    acceptance_bars: int
    acceptance_margin_atr: float
    rejection_excursion_atr: float
    rejection_reentry_atr: float
    migration_lag_bars: int
    migration_min_atr: float
    balance_overlap_min: float
    imbalance_overlap_max: float
    imbalance_outside_atr: float


_PRESETS: dict[str, AuctionPreset] = {
    "15m": AuctionPreset("15m · Hassas", 180, 48, 0.52, 1.12, 0.42, 0.88, 0.10, 2, 2, 0.035, 0.045, 0.015, 3, 0.11, 0.72, 0.60, 0.035),
    "30m": AuctionPreset("30m · Giriş", 150, 44, 0.54, 1.14, 0.40, 0.86, 0.11, 2, 2, 0.040, 0.050, 0.015, 3, 0.12, 0.74, 0.58, 0.040),
    "1h": AuctionPreset("1h · Kısa Değer", 130, 42, 0.56, 1.16, 0.38, 0.84, 0.12, 2, 2, 0.045, 0.055, 0.020, 3, 0.13, 0.75, 0.56, 0.045),
    "2h": AuctionPreset("2h · Ana Auction", 110, 40, 0.58, 1.18, 0.36, 0.82, 0.13, 2, 2, 0.050, 0.060, 0.020, 3, 0.14, 0.76, 0.54, 0.050),
    "4h": AuctionPreset("4h · Bağlam", 90, 36, 0.60, 1.20, 0.34, 0.80, 0.14, 2, 3, 0.055, 0.070, 0.025, 2, 0.16, 0.78, 0.52, 0.055),
    "1d": AuctionPreset("1d · Makro", 70, 32, 0.62, 1.22, 0.32, 0.78, 0.15, 2, 3, 0.060, 0.080, 0.030, 2, 0.18, 0.80, 0.50, 0.060),
}


@dataclass(frozen=True, slots=True)
class AuctionConfig:
    timeframe: str = "1h"
    value_area_percent: float = 70.0
    min_tick: float = 0.01
    max_hvn_nodes: int = 3
    max_lvn_nodes: int = 3

    def __post_init__(self) -> None:
        if self.timeframe not in _PRESETS:
            raise ValueError(f"unsupported auction timeframe: {self.timeframe}")
        if not 50.0 <= self.value_area_percent <= 90.0:
            raise ValueError("value_area_percent must be between 50 and 90")
        if self.min_tick <= 0:
            raise ValueError("min_tick must be > 0")

    @property
    def preset(self) -> AuctionPreset:
        return _PRESETS[self.timeframe]


@dataclass(frozen=True, slots=True)
class AuctionNode:
    kind: str
    center_price: float
    low_price: float
    high_price: float
    score: float
    center_bin: int
    low_bin: int
    high_bin: int
    volume_ratio: float = 0.0
    mean_ratio: float = 0.0
    local_depth: float = 0.0
    inside_value_area: bool = False


@dataclass(frozen=True, slots=True)
class AuctionProfile:
    valid: bool = False
    bars_used: int = 0
    low_price: float | None = None
    high_price: float | None = None
    bin_width: float | None = None
    source_volume: float = 0.0
    allocated_volume: float = 0.0
    allocation_error_pct: float | None = None
    poc_bin: int = -1
    poc_price: float | None = None
    val_bin: int = -1
    vah_bin: int = -1
    val_price: float | None = None
    vah_price: float | None = None
    value_area_coverage_pct: float | None = None
    max_bin_volume: float = 0.0
    volumes: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class AuctionReaction:
    state: str = "INSIDE_VALUE"
    direction: int = 0
    reference_level: float | None = None
    evidence_bars: int = 0
    excursion_atr: float = 0.0
    reentry_atr: float = 0.0


@dataclass(frozen=True, slots=True)
class AuctionMigration:
    state: str = "MIG_STABLE"
    direction: int = 0
    confirmed: bool = False
    poc_delta_atr: float = 0.0
    value_center_delta_atr: float = 0.0
    coherence: float = 0.0


@dataclass(frozen=True, slots=True)
class AuctionBalance:
    state: str = "BAL_TRANSITION"
    direction: int = 0
    confirmed: bool = False
    value_overlap_pct: float | None = None
    outside_distance_atr: float = 0.0
    acceptance_support: bool = False
    migration_support: bool = False


@dataclass(frozen=True, slots=True)
class AuctionPrimaryZone:
    kind: str
    center_price: float
    low_price: float
    high_price: float
    score: float
    location_side: int
    distance_atr: float


@dataclass(frozen=True, slots=True)
class AuctionExport:
    poc: float | None = None
    vah: float | None = None
    val: float | None = None
    reaction_state: str | None = None
    migration_state: str | None = None
    balance_state: str | None = None
    balance_direction: int = 0
    quality: float | None = None
    regime_strength: float | None = None
    primary_zone_kind: str | None = None
    primary_zone_low: float | None = None
    primary_zone_high: float | None = None
    primary_zone_score: float | None = None
    hvn_nodes: tuple[AuctionNode, ...] = ()
    lvn_nodes: tuple[AuctionNode, ...] = ()


def _clamp100(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _atr(rows: list[dict[str, Any]], length: int = 14, min_tick: float = 0.01) -> float:
    if not rows:
        return min_tick
    start = max(0, len(rows) - length)
    trs: list[float] = []
    for i in range(start, len(rows)):
        row = rows[i]
        tr = row["high"] - row["low"]
        if i > 0:
            prev = rows[i - 1]["close"]
            tr = max(tr, abs(row["high"] - prev), abs(row["low"] - prev))
        trs.append(float(tr))
    return max(sum(trs) / max(len(trs), 1), min_tick)


def _price_to_bin(price: float, low: float, width: float, bins: int) -> int:
    return max(0, min(bins - 1, int((price - low) // width)))


def _find_poc(volumes: list[float], low: float, width: float) -> int:
    max_vol = max(volumes)
    total = sum(volumes)
    weighted_center = sum((low + (i + 0.5) * width) * v for i, v in enumerate(volumes)) / total if total > 0 else low + len(volumes) * width * 0.5
    tol = max(max_vol * 1e-10, 1e-10)
    candidates = [i for i, v in enumerate(volumes) if abs(v - max_vol) <= tol]
    return min(candidates, key=lambda i: (abs(low + (i + 0.5) * width - weighted_center), i))


def _value_area(volumes: list[float], poc: int, target_pct: float) -> tuple[int, int, float]:
    total = sum(max(v, 0.0) for v in volumes)
    target = total * target_pct / 100.0
    lo = hi = poc
    cumulative = max(volumes[poc], 0.0)
    while cumulative < target and (lo > 0 or hi < len(volumes) - 1):
        lower = max(volumes[lo - 1], 0.0) if lo > 0 else -1.0
        upper = max(volumes[hi + 1], 0.0) if hi < len(volumes) - 1 else -1.0
        tol = max(max(lower, upper) * 1e-10, 1e-10)
        if lo > 0 and hi < len(volumes) - 1 and abs(lower - upper) <= tol:
            lo -= 1
            hi += 1
            cumulative += lower + upper
        elif hi < len(volumes) - 1 and (lo == 0 or upper > lower):
            hi += 1
            cumulative += upper
        else:
            lo -= 1
            cumulative += lower
    coverage = cumulative / total * 100.0 if total > 0 else 0.0
    return lo, hi, coverage


def build_profile(rows: list[dict[str, Any]], config: AuctionConfig) -> AuctionProfile:
    preset = config.preset
    window = rows[-preset.lookback :]
    if not window:
        return AuctionProfile()
    valid_volume = [r for r in window if r.get("volume") is not None and r["volume"] > 0]
    if not valid_volume:
        return AuctionProfile(bars_used=len(window))
    profile_low = min(r["low"] for r in window)
    profile_high = max(r["high"] for r in window)
    working_range = max(profile_high - profile_low, config.min_tick * preset.bins)
    center = (profile_high + profile_low) * 0.5
    low = center - working_range * 0.5
    high = center + working_range * 0.5
    width = working_range / preset.bins
    volumes = [0.0] * preset.bins
    source_volume = 0.0
    allocated = 0.0
    for row in window:
        v = float(row.get("volume") or 0.0)
        if v <= 0:
            continue
        source_volume += v
        candle_low = min(row["low"], row["high"])
        candle_high = max(row["low"], row["high"])
        candle_range = candle_high - candle_low
        assigned = 0.0
        if candle_range <= config.min_tick * 0.25:
            idx = _price_to_bin(row["close"], low, width, preset.bins)
            volumes[idx] += v
            assigned = v
        else:
            first = _price_to_bin(candle_low, low, width, preset.bins)
            last = _price_to_bin(candle_high, low, width, preset.bins)
            for idx in range(first, last + 1):
                bin_low = low + idx * width
                bin_high = high if idx == preset.bins - 1 else bin_low + width
                overlap = max(0.0, min(candle_high, bin_high) - max(candle_low, bin_low))
                if overlap > 0:
                    part = v * overlap / candle_range
                    volumes[idx] += part
                    assigned += part
            residual = v - assigned
            if abs(residual) > max(v * 1e-8, 1e-8):
                idx = _price_to_bin(row["close"], low, width, preset.bins)
                volumes[idx] = max(0.0, volumes[idx] + residual)
                assigned += residual
        allocated += assigned
    if source_volume <= 0 or max(volumes) <= 0:
        return AuctionProfile(bars_used=len(window))
    poc = _find_poc(volumes, low, width)
    val_bin, vah_bin, coverage = _value_area(volumes, poc, config.value_area_percent)
    error = abs(allocated - source_volume) / source_volume * 100.0
    return AuctionProfile(
        valid=True,
        bars_used=len(window),
        low_price=low,
        high_price=high,
        bin_width=width,
        source_volume=source_volume,
        allocated_volume=allocated,
        allocation_error_pct=error,
        poc_bin=poc,
        poc_price=low + (poc + 0.5) * width,
        val_bin=val_bin,
        vah_bin=vah_bin,
        val_price=low + val_bin * width,
        vah_price=low + (vah_bin + 1) * width,
        value_area_coverage_pct=coverage,
        max_bin_volume=max(volumes),
        volumes=tuple(volumes),
    )


def _bands_overlap_or_near(low_a: int, high_a: int, low_b: int, high_b: int, min_sep: int) -> bool:
    return low_a <= high_b + min_sep and high_a >= low_b - min_sep


def _nodes(profile: AuctionProfile, config: AuctionConfig) -> tuple[tuple[AuctionNode, ...], tuple[AuctionNode, ...]]:
    if not profile.valid or len(profile.volumes) < 5 or profile.bin_width is None or profile.low_price is None:
        return (), ()
    raw = list(profile.volumes)
    smooth: list[float] = []
    for i, center in enumerate(raw):
        left = raw[i - 1] if i > 0 else center
        right = raw[i + 1] if i < len(raw) - 1 else center
        smooth.append((left + 2.0 * center + right) / 4.0)

    max_s = max(smooth)
    mean_s = sum(smooth) / len(smooth)
    p = config.preset
    edge_guard = max(2, round(len(smooth) * 0.06))
    hvn_candidates: list[tuple[float, int]] = []
    lvn_candidates: list[tuple[float, int]] = []

    for i in range(1, len(smooth) - 1):
        left, cur, right = smooth[i - 1], smooth[i], smooth[i + 1]
        if cur >= left and cur >= right and (cur > left or cur > right):
            rel = cur / max_s if max_s else 0.0
            mean_ratio = cur / mean_s if mean_s else 0.0
            prominence = max(cur - max(left, right), 0.0) / cur if cur else 0.0
            if rel >= p.hvn_rel_min and mean_ratio >= p.hvn_mean_mult:
                score = min(100.0, rel * 55.0 + min(mean_ratio / 2.0, 1.0) * 30.0 + min(prominence / 0.20, 1.0) * 15.0)
                hvn_candidates.append((score, i))

        if edge_guard <= i <= len(smooth) - 1 - edge_guard and cur <= left and cur <= right and (cur < left or cur < right):
            rel = cur / max_s if max_s else 1.0
            mean_ratio = cur / mean_s if mean_s else 1.0
            shoulder_avg = max((left + right) * 0.5, 1e-10)
            depth = max(0.0, 1.0 - cur / shoulder_avg)
            if rel <= p.lvn_rel_max and mean_ratio <= p.lvn_mean_max and depth >= p.lvn_depth_min:
                score = min(100.0, (1.0 - rel) * 50.0 + min(depth / 0.40, 1.0) * 30.0 + max(0.0, 1.0 - min(mean_ratio, 1.0)) * 20.0)
                lvn_candidates.append((score, i))

    def hvn_band(center_bin: int) -> tuple[int, int]:
        peak = smooth[center_bin]
        shoulder_floor = peak * 0.72
        low_bin = high_bin = center_bin
        for step in range(1, 4):
            idx = center_bin - step
            if idx >= 0 and smooth[idx] >= shoulder_floor:
                low_bin = idx
            else:
                break
        for step in range(1, 4):
            idx = center_bin + step
            if idx < len(smooth) and smooth[idx] >= shoulder_floor:
                high_bin = idx
            else:
                break
        return low_bin, high_bin

    def lvn_band(center_bin: int) -> tuple[int, int]:
        valley = smooth[center_bin]
        left_shoulder = smooth[center_bin - 1]
        right_shoulder = smooth[center_bin + 1]
        shoulder_ref = min(left_shoulder, right_shoulder)
        valley_ceiling = valley + max(shoulder_ref - valley, 0.0) * 0.55
        low_bin = high_bin = center_bin
        for step in range(1, 3):
            idx = center_bin - step
            if idx >= edge_guard and smooth[idx] <= valley_ceiling:
                low_bin = idx
            else:
                break
        for step in range(1, 3):
            idx = center_bin + step
            if idx <= len(smooth) - 1 - edge_guard and smooth[idx] <= valley_ceiling:
                high_bin = idx
            else:
                break
        return low_bin, high_bin

    def make_node(kind: str, score: float, center_bin: int) -> AuctionNode:
        low_bin, high_bin = hvn_band(center_bin) if kind == "HVN" else lvn_band(center_bin)
        center_price = profile.low_price + (center_bin + 0.5) * profile.bin_width
        low_price = profile.low_price + low_bin * profile.bin_width
        high_price = profile.low_price + (high_bin + 1) * profile.bin_width
        rel = smooth[center_bin] / max_s if max_s else 0.0
        mean_ratio = smooth[center_bin] / mean_s if mean_s else 0.0
        depth = 0.0
        if kind == "LVN":
            shoulder_avg = max((smooth[center_bin - 1] + smooth[center_bin + 1]) * 0.5, 1e-10)
            depth = max(0.0, 1.0 - smooth[center_bin] / shoulder_avg)
        inside_value = profile.val_bin <= center_bin <= profile.vah_bin
        return AuctionNode(
            kind=kind,
            center_price=center_price,
            low_price=low_price,
            high_price=high_price,
            score=round(score, 2),
            center_bin=center_bin,
            low_bin=low_bin,
            high_bin=high_bin,
            volume_ratio=rel,
            mean_ratio=mean_ratio,
            local_depth=depth,
            inside_value_area=inside_value,
        )

    def select(candidates: list[tuple[float, int]], kind: str, limit: int, avoid: tuple[AuctionNode, ...] = ()) -> tuple[AuctionNode, ...]:
        selected: list[AuctionNode] = []
        for score, center_bin in sorted(candidates, key=lambda item: (-item[0], item[1])):
            candidate = make_node(kind, score, center_bin)
            blocked = any(
                _bands_overlap_or_near(candidate.low_bin, candidate.high_bin, node.low_bin, node.high_bin, p.node_min_separation_bins)
                for node in (*avoid, *selected)
            )
            if blocked:
                continue
            selected.append(candidate)
            if len(selected) >= limit:
                break
        return tuple(selected)

    hvn = select(hvn_candidates, "HVN", config.max_hvn_nodes)
    lvn = select(lvn_candidates, "LVN", config.max_lvn_nodes, hvn)
    return hvn, lvn


def _value_center(profile: AuctionProfile) -> float | None:
    if not profile.valid or profile.vah_price is None or profile.val_price is None:
        return None
    return (profile.vah_price + profile.val_price) * 0.5


def _reaction(rows: list[dict[str, Any]], config: AuctionConfig, atr: float) -> AuctionReaction:
    p = config.preset
    if len(rows) <= p.acceptance_bars:
        return AuctionReaction()
    ref = build_profile(rows[:-p.acceptance_bars], config)
    if not ref.valid or ref.vah_price is None or ref.val_price is None or ref.bin_width is None:
        return AuctionReaction()
    acceptance_margin = max(atr * p.acceptance_margin_atr, ref.bin_width * 0.10, config.min_tick * 2.0)
    reject_excursion = max(atr * p.rejection_excursion_atr, ref.bin_width * 0.12, config.min_tick * 2.0)
    reject_reentry = max(atr * p.rejection_reentry_atr, config.min_tick * 2.0)
    recent = rows[-p.acceptance_bars :]
    accept_up = all(r["close"] > ref.vah_price + acceptance_margin for r in recent)
    accept_down = all(r["close"] < ref.val_price - acceptance_margin for r in recent)
    current = rows[-1]
    up_exc = max(current["high"] - ref.vah_price, 0.0)
    down_exc = max(ref.val_price - current["low"], 0.0)
    up_reentry = max(ref.vah_price - current["close"], 0.0)
    down_reentry = max(current["close"] - ref.val_price, 0.0)
    reject_up = up_exc >= reject_excursion and current["close"] <= ref.vah_price - reject_reentry
    reject_down = down_exc >= reject_excursion and current["close"] >= ref.val_price + reject_reentry
    if accept_up and not accept_down:
        return AuctionReaction("ACCEPT_UP", 1, ref.vah_price, p.acceptance_bars, max(current["close"] - ref.vah_price, 0.0) / atr, 0.0)
    if accept_down and not accept_up:
        return AuctionReaction("ACCEPT_DOWN", -1, ref.val_price, p.acceptance_bars, max(ref.val_price - current["close"], 0.0) / atr, 0.0)
    if reject_up and (not reject_down or up_exc >= down_exc):
        return AuctionReaction("REJECT_UP", -1, ref.vah_price, 0, up_exc / atr, up_reentry / atr)
    if reject_down:
        return AuctionReaction("REJECT_DOWN", 1, ref.val_price, 0, down_exc / atr, down_reentry / atr)
    touch = max(ref.bin_width * 0.20, config.min_tick * 2.0)
    test_up = current["high"] >= ref.vah_price - touch
    test_down = current["low"] <= ref.val_price + touch
    if test_up and test_down:
        up_near = abs(current["close"] - ref.vah_price)
        down_near = abs(current["close"] - ref.val_price)
        if up_near <= down_near:
            return AuctionReaction("TEST_UP", 0, ref.vah_price)
        return AuctionReaction("TEST_DOWN", 0, ref.val_price)
    if test_up:
        return AuctionReaction("TEST_UP", 0, ref.vah_price)
    if test_down:
        return AuctionReaction("TEST_DOWN", 0, ref.val_price)
    return AuctionReaction("INSIDE_VALUE", 0)


def _migration_step(newer: AuctionProfile, older: AuctionProfile, config: AuctionConfig, atr: float) -> tuple[int, int, float, float, float]:
    if not newer.valid or not older.valid or newer.poc_price is None or older.poc_price is None:
        return 0, 0, 0.0, 0.0, 0.0
    new_center = _value_center(newer)
    old_center = _value_center(older)
    if new_center is None or old_center is None or newer.bin_width is None or older.bin_width is None:
        return 0, 0, 0.0, 0.0, 0.0
    bin_ref = max(newer.bin_width, older.bin_width, config.min_tick)
    p = config.preset
    poc_threshold = max(atr * p.migration_min_atr, bin_ref * 0.75)
    value_threshold = max(atr * p.migration_min_atr * 0.65, bin_ref * 0.50)
    boundary_threshold = max(value_threshold * 0.65, bin_ref * 0.35)

    def direction(delta: float, threshold: float) -> int:
        return 1 if delta >= threshold else -1 if delta <= -threshold else 0

    poc_delta = newer.poc_price - older.poc_price
    center_delta = new_center - old_center
    vah_delta = (newer.vah_price or 0.0) - (older.vah_price or 0.0)
    val_delta = (newer.val_price or 0.0) - (older.val_price or 0.0)
    poc_dir = direction(poc_delta, poc_threshold)
    center_dir = direction(center_delta, value_threshold)
    vah_dir = direction(vah_delta, boundary_threshold)
    val_dir = direction(val_delta, boundary_threshold)
    up = sum(x == 1 for x in (center_dir, vah_dir, val_dir))
    down = sum(x == -1 for x in (center_dir, vah_dir, val_dir))
    value_dir = 1 if center_dir == 1 and up >= 2 else -1 if center_dir == -1 and down >= 2 else 0
    coherence = max(up, down) / 3.0 * 100.0
    return poc_dir, value_dir, poc_delta / atr, center_delta / atr, coherence


def _migration(rows: list[dict[str, Any]], config: AuctionConfig, current: AuctionProfile, atr: float) -> AuctionMigration:
    lag = config.preset.migration_lag_bars
    if len(rows) <= lag * 2:
        return AuctionMigration()
    previous = build_profile(rows[:-lag], config)
    older = build_profile(rows[: -lag * 2], config)
    poc_now, value_now, poc_delta, center_delta, coherence = _migration_step(current, previous, config, atr)
    poc_before, value_before, _, _, _ = _migration_step(previous, older, config, atr)
    confirmed_up = value_now == 1 and value_before == 1 and poc_now != -1 and poc_before != -1
    confirmed_down = value_now == -1 and value_before == -1 and poc_now != 1 and poc_before != 1
    if confirmed_up:
        return AuctionMigration("MIG_UP", 1, True, poc_delta, center_delta, coherence)
    if confirmed_down:
        return AuctionMigration("MIG_DOWN", -1, True, poc_delta, center_delta, coherence)
    if value_now == 1:
        return AuctionMigration("MIG_DEVELOPING_UP", 0, False, poc_delta, center_delta, coherence)
    if value_now == -1:
        return AuctionMigration("MIG_DEVELOPING_DOWN", 0, False, poc_delta, center_delta, coherence)
    state = "MIG_STABLE" if value_before == 0 else "MIG_MIXED"
    return AuctionMigration(state, 0, False, poc_delta, center_delta, coherence)


def _overlap(a: AuctionProfile, b: AuctionProfile, min_tick: float) -> float | None:
    if not a.valid or not b.valid or a.vah_price is None or a.val_price is None or b.vah_price is None or b.val_price is None:
        return None
    width = max(min(a.vah_price - a.val_price, b.vah_price - b.val_price), min_tick)
    common = max(0.0, min(a.vah_price, b.vah_price) - max(a.val_price, b.val_price))
    return max(0.0, min(100.0, common / width * 100.0))


def _balance(rows: list[dict[str, Any]], config: AuctionConfig, current: AuctionProfile, reaction: AuctionReaction, migration: AuctionMigration, atr: float) -> AuctionBalance:
    lag = config.preset.migration_lag_bars
    older = build_profile(rows[: -lag * 2], config) if len(rows) > lag * 2 else AuctionProfile()
    if not current.valid or not older.valid:
        return AuctionBalance()
    overlap = _overlap(current, older, config.min_tick)
    ref_rows = rows[:-config.preset.acceptance_bars]
    ref = build_profile(ref_rows, config)
    if not ref.valid or ref.vah_price is None or ref.val_price is None:
        return AuctionBalance(value_overlap_pct=overlap)
    margin = max(atr * config.preset.imbalance_outside_atr, (ref.bin_width or config.min_tick) * 0.10, config.min_tick * 2.0)
    close = rows[-1]["close"]
    position = 1 if close > ref.vah_price + margin else -1 if close < ref.val_price - margin else 0
    outside = max(close - ref.vah_price, 0.0) / atr if position == 1 else max(ref.val_price - close, 0.0) / atr if position == -1 else 0.0
    accept_up, accept_down = reaction.state == "ACCEPT_UP", reaction.state == "ACCEPT_DOWN"
    mig_up, mig_down = migration.state == "MIG_UP", migration.state == "MIG_DOWN"
    confirmed_up = accept_up and mig_up and position == 1
    confirmed_down = accept_down and mig_down and position == -1
    if confirmed_up:
        return AuctionBalance("BAL_IMBALANCE_UP", 1, True, overlap, outside, True, True)
    if confirmed_down:
        return AuctionBalance("BAL_IMBALANCE_DOWN", -1, True, overlap, outside, True, True)
    developing_up = position == 1 and not mig_down and (accept_up or mig_up or migration.state == "MIG_DEVELOPING_UP")
    developing_down = position == -1 and not mig_up and (accept_down or mig_down or migration.state == "MIG_DEVELOPING_DOWN")
    if developing_up:
        return AuctionBalance("BAL_DEVELOPING_UP", 1, False, overlap, outside, accept_up, mig_up or migration.state == "MIG_DEVELOPING_UP")
    if developing_down:
        return AuctionBalance("BAL_DEVELOPING_DOWN", -1, False, overlap, outside, accept_down, mig_down or migration.state == "MIG_DEVELOPING_DOWN")
    high_overlap = overlap is not None and overlap >= config.preset.balance_overlap_min * 100.0
    if position == 0 and high_overlap and migration.state in {"MIG_STABLE", "MIG_MIXED"}:
        return AuctionBalance("BAL_BALANCED", 0, False, overlap, 0.0)
    return AuctionBalance("BAL_TRANSITION", 0, False, overlap, outside, accept_up or accept_down, mig_up or mig_down)


def _quality(profile: AuctionProfile, hvn: tuple[AuctionNode, ...], lvn: tuple[AuctionNode, ...], reaction: AuctionReaction, migration: AuctionMigration, balance: AuctionBalance, config: AuctionConfig) -> tuple[float, float]:
    if not profile.valid:
        return 0.0, 0.0
    coverage = min(profile.bars_used / config.preset.lookback, 1.0) * 100.0
    error = profile.allocation_error_pct if profile.allocation_error_pct is not None else 100.0
    allocation_q = 100.0 if error <= 0.001 else 96.0 if error <= 0.01 else 88.0 if error <= 0.05 else 76.0 if error <= 0.10 else 58.0 if error <= 0.50 else 35.0
    profile_q = coverage * 0.55 + allocation_q * 0.45
    mean_vol = profile.source_volume / max(len(profile.volumes), 1)
    dominance = profile.max_bin_volume / mean_vol if mean_vol > 0 else 1.0
    dominance_q = _clamp100(35.0 + max(dominance - 1.0, 0.0) / 1.20 * 65.0)
    overshoot = abs((profile.value_area_coverage_pct or config.value_area_percent) - config.value_area_percent)
    value_q = dominance_q * 0.65 + _clamp100(100.0 - overshoot * 8.0) * 0.35
    node_q = 40.0
    if hvn and lvn:
        node_q = _clamp100(30.0 + hvn[0].score * 0.35 + lvn[0].score * 0.35)
    elif hvn:
        node_q = _clamp100(35.0 + hvn[0].score * 0.50)
    elif lvn:
        node_q = _clamp100(30.0 + lvn[0].score * 0.45)
    reaction_q = 45.0 if reaction.state.startswith("TEST") else 75.0 if reaction.state.startswith("ACCEPT") or reaction.state.startswith("REJECT") else 55.0
    if balance.state.startswith("BAL_IMBALANCE"):
        regime_q = 82.0 + migration.coherence * 0.18
    elif balance.state.startswith("BAL_DEVELOPING"):
        regime_q = 48.0 + (12.0 if balance.acceptance_support else 0.0) + (12.0 if balance.migration_support else 0.0) + migration.coherence * 0.20
    elif balance.state == "BAL_BALANCED":
        regime_q = (balance.value_overlap_pct or 0.0) * 0.55 + (100.0 if migration.state == "MIG_STABLE" else 68.0) * 0.45
    else:
        regime_q = 35.0 + migration.coherence * 0.20
    quality = _clamp100(profile_q * 0.20 + value_q * 0.25 + node_q * 0.20 + reaction_q * 0.15 + _clamp100(regime_q) * 0.20)
    if balance.state.startswith("BAL_IMBALANCE"):
        strength = _clamp100(55.0 + min(balance.outside_distance_atr / max(config.preset.imbalance_outside_atr * 3.0, 0.01), 1.0) * 15.0 + min(abs(migration.value_center_delta_atr) / max(config.preset.migration_min_atr * 2.5, 0.01), 1.0) * 15.0 + reaction_q * 0.15)
    elif balance.state.startswith("BAL_DEVELOPING"):
        strength = _clamp100(38.0 + reaction_q * 0.12 + migration.coherence * 0.15)
    elif balance.state == "BAL_BALANCED":
        strength = _clamp100((balance.value_overlap_pct or 0.0) * 0.50 + (100.0 if migration.state == "MIG_STABLE" else 65.0) * 0.35 + 15.0)
    else:
        strength = _clamp100(25.0 + migration.coherence * 0.20)
    return round(quality, 2), round(strength, 2)


def _primary_zone(profile: AuctionProfile, hvn: tuple[AuctionNode, ...], lvn: tuple[AuctionNode, ...], balance: AuctionBalance, close: float, atr: float, config: AuctionConfig) -> AuctionPrimaryZone | None:
    if not profile.valid or profile.poc_price is None or profile.vah_price is None or profile.val_price is None or profile.bin_width is None:
        return None
    candidates: list[tuple[str, float, float, float, float]] = []
    poc_hvn = next((n for n in hvn if n.low_price <= profile.poc_price <= n.high_price), None)
    if poc_hvn:
        candidates.append(("POC_HVN", profile.poc_price, poc_hvn.low_price, poc_hvn.high_price, 96.0))
    else:
        half = profile.bin_width * 0.5
        candidates.append(("POC", profile.poc_price, profile.poc_price - half, profile.poc_price + half, 88.0))
    candidates.extend(("HVN", n.center_price, n.low_price, n.high_price, 58.0 + n.score * 0.42) for n in hvn if n is not poc_hvn)
    candidates.extend(("LVN", n.center_price, n.low_price, n.high_price, 50.0 + n.score * 0.45) for n in lvn)
    boundary_half = max(profile.bin_width * 0.30, atr * 0.0125, config.min_tick * 2.0)
    candidates.append(("VAH", profile.vah_price, profile.vah_price - boundary_half, profile.vah_price + boundary_half, 78.0))
    candidates.append(("VAL", profile.val_price, profile.val_price - boundary_half, profile.val_price + boundary_half, 78.0))
    best: AuctionPrimaryZone | None = None
    for kind, center, low, high, intrinsic in candidates:
        distance = 0.0 if low <= close <= high else (low - close if close < low else close - high) / atr
        reach = 100.0 / (1.0 + (max(distance, 0.0) / 1.25) ** 2)
        regime_fit = 60.0
        if balance.state == "BAL_BALANCED":
            regime_fit = 100.0 if kind == "POC_HVN" else 95.0 if kind == "POC" else 90.0 if kind == "HVN" else 82.0 if kind in {"VAH", "VAL"} else 70.0
        elif balance.state in {"BAL_IMBALANCE_UP", "BAL_DEVELOPING_UP"}:
            regime_fit = 98.0 if kind == "VAH" else 84.0 if center >= profile.poc_price and kind in {"HVN", "POC_HVN", "LVN"} else 66.0 if kind == "POC" else 44.0 if kind == "VAL" else 70.0
        elif balance.state in {"BAL_IMBALANCE_DOWN", "BAL_DEVELOPING_DOWN"}:
            regime_fit = 98.0 if kind == "VAL" else 84.0 if center <= profile.poc_price and kind in {"HVN", "POC_HVN", "LVN"} else 66.0 if kind == "POC" else 44.0 if kind == "VAH" else 70.0
        elif balance.state == "BAL_TRANSITION":
            regime_fit = 92.0 if kind == "LVN" else 84.0 if kind in {"VAH", "VAL"} else 75.0 if kind in {"HVN", "POC_HVN"} else 65.0
        score = _clamp100(intrinsic * 0.45 + regime_fit * 0.30 + reach * 0.25)
        side = 1 if close < low else -1 if close > high else 0
        candidate = AuctionPrimaryZone(kind, center, low, high, round(score, 2), side, round(distance, 4))
        if best is None or candidate.score > best.score + 0.10 or (abs(candidate.score - best.score) <= 0.10 and candidate.distance_atr < best.distance_atr):
            best = candidate
    return best


class AuctionVolumeProfileEngine(BaseEngine):
    """Deterministic OHLCV approximation of the ARGENT Auction / Volume Profile radar."""

    name = "auction_volume_profile"

    def __init__(self, config: AuctionConfig | None = None) -> None:
        self.config = config or AuctionConfig()
        self.reset()

    def reset(self) -> None:
        self._rows: list[dict[str, Any]] = []
        self._snapshot: EngineResult | None = None
        self._export: AuctionExport | None = None

    def replay(self, frame: pd.DataFrame) -> list[EngineResult]:
        self.reset()
        out: list[EngineResult] = []
        for _, row in frame.iterrows():
            result = self.update(row)
            if result is not None:
                out.append(result)
        return out

    def snapshot(self) -> EngineResult | None:
        return self._snapshot

    @property
    def export_contract(self) -> AuctionExport | None:
        return self._export

    def update(self, bar: pd.Series | dict[str, Any]) -> EngineResult | None:
        row = dict(bar) if isinstance(bar, dict) else bar.to_dict()
        if not bool(row.get("is_closed", True)):
            return self._snapshot
        required = ("timestamp", "open", "high", "low", "close", "volume")
        missing = [key for key in required if key not in row or pd.isna(row[key])]
        if missing:
            raise ValueError(f"auction requires closed OHLCV bars; missing {missing}")
        clean = {
            "timestamp": row["timestamp"],
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        }
        self._rows.append(clean)
        profile = build_profile(self._rows, self.config)
        if not profile.valid:
            self._export = AuctionExport()
            self._snapshot = EngineResult(self.name, "AUCTION_UNAVAILABLE", clean["timestamp"], Direction.NEUTRAL, 0.0, 0.0, {}, (), ("valid trade volume required",), True)
            return self._snapshot
        atr = _atr(self._rows, min_tick=self.config.min_tick)
        hvn, lvn = _nodes(profile, self.config)
        reaction = _reaction(self._rows, self.config, atr)
        migration = _migration(self._rows, self.config, profile, atr)
        balance = _balance(self._rows, self.config, profile, reaction, migration, atr)
        quality, strength = _quality(profile, hvn, lvn, reaction, migration, balance, self.config)
        primary = _primary_zone(profile, hvn, lvn, balance, clean["close"], atr, self.config)
        direction = Direction.UP if balance.direction > 0 else Direction.DOWN if balance.direction < 0 else Direction.NEUTRAL
        score = strength if direction is Direction.UP else -strength if direction is Direction.DOWN else 0.0
        levels = {"poc": profile.poc_price, "vah": profile.vah_price, "val": profile.val_price}
        if primary is not None:
            levels.update({"primary_zone_low": primary.low_price, "primary_zone_high": primary.high_price})
        events = tuple(x for x in (reaction.state if reaction.state not in {"INSIDE_VALUE", "TEST_UP", "TEST_DOWN"} else None, migration.state if migration.state not in {"MIG_STABLE", "MIG_MIXED"} else None, balance.state) if x)
        reasons = (
            f"reaction={reaction.state}",
            f"migration={migration.state}",
            f"value_overlap={balance.value_overlap_pct:.2f}" if balance.value_overlap_pct is not None else "value_overlap=NA",
            f"profile_allocation_error_pct={profile.allocation_error_pct:.6f}" if profile.allocation_error_pct is not None else "profile_allocation_error_pct=NA",
        )
        self._export = AuctionExport(
            poc=profile.poc_price,
            vah=profile.vah_price,
            val=profile.val_price,
            reaction_state=reaction.state,
            migration_state=migration.state,
            balance_state=balance.state,
            balance_direction=int(direction),
            quality=quality,
            regime_strength=strength,
            primary_zone_kind=primary.kind if primary else None,
            primary_zone_low=primary.low_price if primary else None,
            primary_zone_high=primary.high_price if primary else None,
            primary_zone_score=primary.score if primary else None,
            hvn_nodes=hvn,
            lvn_nodes=lvn,
        )
        self._snapshot = EngineResult(
            engine=self.name,
            state=balance.state,
            timestamp=clean["timestamp"],
            direction=direction,
            score=round(score, 2),
            quality=quality,
            levels=levels,
            events=events,
            reasons=reasons,
            is_confirmed=True,
        )
        return self._snapshot
