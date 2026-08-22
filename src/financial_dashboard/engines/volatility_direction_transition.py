from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import math
import pandas as pd

from .models import EngineResult
from .volatility_bands_fib import VolatilityBandsFibEngine
from .volatility_bands_fib_engine import VolatilityBandsConfig, VolatilityState
from .volatility_bands_fib_final import VolatilityBandsFibFinalExport


class EarlyDirectionTransition(StrEnum):
    NONE = "NONE"
    EARLY_UP = "EARLY_UP"
    EARLY_DOWN = "EARLY_DOWN"


@dataclass(frozen=True, slots=True)
class EarlyDirectionEvidence:
    state: EarlyDirectionTransition = EarlyDirectionTransition.NONE
    evidence_count: int = 0
    reasons: tuple[str, ...] = ()
    displacement_atr: float | None = None
    body_atr: float | None = None
    close_location: float | None = None
    bollinger_position: float | None = None
    bollinger_position_change: float | None = None
    atr_slope: float | None = None
    width_slope: float | None = None
    raw_state: EarlyDirectionTransition = EarlyDirectionTransition.NONE
    episode_started: bool = False
    episode_id: int = 0


@dataclass(frozen=True, slots=True)
class VolatilityDirectionSnapshot:
    timestamp: Any | None
    core_result: EngineResult | None
    confirmed_export: VolatilityBandsFibFinalExport
    early: EarlyDirectionEvidence


@dataclass(frozen=True, slots=True)
class _EarlyProfile:
    displacement_atr: float
    body_atr: float
    position_change: float
    context_count: int


def _profile(config: VolatilityBandsConfig) -> _EarlyProfile:
    if config.profile == "Hassas":
        return _EarlyProfile(.22, .25, .06, 1)
    if config.profile == "Seçici":
        return _EarlyProfile(.40, .42, .10, 2)
    return _EarlyProfile(.30, .32, .08, 2)


def _safe_div(numerator: float | None, denominator: float | None, fallback: float = 0.0) -> float:
    if numerator is None or denominator is None:
        return fallback
    if pd.isna(numerator) or pd.isna(denominator) or abs(float(denominator)) <= 1e-12:
        return fallback
    return float(numerator) / float(denominator)


def _true_ranges(rows: list[dict[str, Any]]) -> list[float]:
    out: list[float] = []
    for i, row in enumerate(rows):
        high = float(row["high"])
        low = float(row["low"])
        if i == 0:
            out.append(high - low)
            continue
        prior_close = float(rows[i - 1]["close"])
        out.append(max(high - low, abs(high - prior_close), abs(low - prior_close)))
    return out


def _wilder(values: list[float], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < length:
        return out
    seed = sum(values[:length]) / float(length)
    out[length - 1] = seed
    prev = seed
    alpha = 1.0 / float(length)
    for i in range(length, len(values)):
        prev = alpha * values[i] + (1.0 - alpha) * prev
        out[i] = prev
    return out


def _band_point(closes: list[float], index: int) -> tuple[float, float, float, float] | None:
    if index < 19:
        return None
    window = closes[index - 19 : index + 1]
    basis = sum(window) / 20.0
    variance = sum((value - basis) ** 2 for value in window) / 20.0
    stdev = math.sqrt(variance)
    upper = basis + 2.0 * stdev
    lower = basis - 2.0 * stdev
    width = upper - lower
    return basis, upper, lower, width


def _canonical_is_shock(result: EngineResult | None) -> bool:
    if result is None:
        return False
    if "ONE_BAR_SHOCK" in result.state:
        return True
    return any("ONE_BAR_SHOCK" in reason for reason in result.reasons)


def _regime(export: VolatilityBandsFibFinalExport) -> VolatilityState | None:
    if export.regime is None:
        return None
    try:
        return VolatilityState(int(export.regime))
    except (TypeError, ValueError):
        return None


def _directional_regime(state: VolatilityState | None) -> EarlyDirectionTransition:
    if state in {VolatilityState.UP_CANDIDATE, VolatilityState.UP_CONFIRMED}:
        return EarlyDirectionTransition.EARLY_UP
    if state in {VolatilityState.DOWN_CANDIDATE, VolatilityState.DOWN_CONFIRMED}:
        return EarlyDirectionTransition.EARLY_DOWN
    return EarlyDirectionTransition.NONE


class VolatilityDirectionTransitionEngine:
    """Fast descriptive direction-transition track around the canonical engine.

    Raw one-bar evidence is filtered through a small episode lifecycle. Repeated
    evidence in the same move is not re-emitted as a new transition, and neutral
    market flip-flops need two consecutive opposite raw observations before re-arm.
    If the canonical regime is already candidate/confirmed in the same direction,
    early evidence is suppressed because it is no longer early.
    """

    ATR_LENGTH = 14
    MINIMUM_EARLY_HISTORY = 24
    OPPOSITE_REARM_OBSERVATIONS = 2

    def __init__(self, config: VolatilityBandsConfig | None = None) -> None:
        self.config = config or VolatilityBandsConfig()
        self._early_profile = _profile(self.config)
        self._core = VolatilityBandsFibEngine(self.config)
        self._rows: list[dict[str, Any]] = []
        self._episode_direction = EarlyDirectionTransition.NONE
        self._episode_id = 0
        self._pending_opposite = EarlyDirectionTransition.NONE
        self._pending_opposite_count = 0
        self._snapshot = VolatilityDirectionSnapshot(
            timestamp=None,
            core_result=None,
            confirmed_export=self._core.final_export,
            early=EarlyDirectionEvidence(),
        )

    @staticmethod
    def _normalize_bar(bar: pd.Series | dict[str, Any]) -> dict[str, Any]:
        row = dict(bar)
        for key in ("timestamp", "open", "high", "low", "close", "volume"):
            if key not in row:
                raise ValueError(f"missing required field: {key}")
        row.setdefault("is_closed", True)
        row.setdefault("is_complete", True)
        return row

    def _early_evidence(self, core_result: EngineResult | None) -> EarlyDirectionEvidence:
        rows = self._rows
        if len(rows) < self.MINIMUM_EARLY_HISTORY:
            return EarlyDirectionEvidence()

        i = len(rows) - 1
        current = rows[i]
        previous = rows[i - 1]
        closes = [float(row["close"]) for row in rows]
        tr = _true_ranges(rows)
        atr = _wilder(tr, self.ATR_LENGTH)
        prior_atr = atr[i - 1]
        if prior_atr is None or prior_atr <= 0:
            return EarlyDirectionEvidence()

        band = _band_point(closes, i)
        prior_band = _band_point(closes, i - 1)
        slope_band = _band_point(closes, i - 3) if i >= 3 else None
        if band is None or prior_band is None or slope_band is None:
            return EarlyDirectionEvidence()

        basis, upper, lower, width = band
        prior_basis, prior_upper, prior_lower, prior_width = prior_band
        old_basis, _, _, old_width = slope_band
        if width <= 1e-12 or prior_width <= 1e-12:
            return EarlyDirectionEvidence()

        open_ = float(current["open"])
        high = float(current["high"])
        low = float(current["low"])
        close = float(current["close"])
        previous_close = float(previous["close"])

        displacement = _safe_div(close - previous_close, prior_atr)
        body = _safe_div(abs(close - open_), prior_atr)
        close_location = _safe_div(close - low, high - low, .5)
        position = _safe_div(close - lower, width, .5)
        prior_position = _safe_div(previous_close - prior_lower, prior_width, .5)
        position_change = position - prior_position

        atr_slope = 0.0
        if i >= 3 and atr[i] is not None and atr[i - 3] is not None:
            atr_slope = _safe_div(float(atr[i]) - float(atr[i - 3]), float(atr[i - 3]))

        normalized_width = _safe_div(width, max(abs(basis), self.config.minimum_tick))
        old_normalized_width = _safe_div(old_width, max(abs(old_basis), self.config.minimum_tick))
        width_slope = _safe_div(normalized_width - old_normalized_width, old_normalized_width)

        profile = self._early_profile
        up_core = (
            close > open_
            and close > previous_close
            and displacement >= profile.displacement_atr
            and body >= profile.body_atr
            and close_location >= .62
        )
        down_core = (
            close < open_
            and close < previous_close
            and displacement <= -profile.displacement_atr
            and body >= profile.body_atr
            and close_location <= .38
        )

        up_context = (
            close > basis,
            position_change >= profile.position_change,
            atr_slope > 0.0,
            width_slope > 0.0,
        )
        down_context = (
            close < basis,
            position_change <= -profile.position_change,
            atr_slope > 0.0,
            width_slope > 0.0,
        )
        up_count = sum(map(int, up_context))
        down_count = sum(map(int, down_context))

        metrics = dict(
            displacement_atr=displacement,
            body_atr=body,
            close_location=close_location,
            bollinger_position=position,
            bollinger_position_change=position_change,
            atr_slope=atr_slope,
            width_slope=width_slope,
        )

        if _canonical_is_shock(core_result):
            return EarlyDirectionEvidence(
                reasons=("canonical_one_bar_shock",),
                **metrics,
            )

        if up_core and up_count >= profile.context_count:
            reasons = ["up_displacement", "up_body", "upper_close_location"]
            if up_context[0]:
                reasons.append("above_basis")
            if up_context[1]:
                reasons.append("band_position_rising")
            if up_context[2]:
                reasons.append("atr_rising")
            if up_context[3]:
                reasons.append("band_width_rising")
            return EarlyDirectionEvidence(
                state=EarlyDirectionTransition.EARLY_UP,
                raw_state=EarlyDirectionTransition.EARLY_UP,
                evidence_count=3 + up_count,
                reasons=tuple(reasons),
                **metrics,
            )

        if down_core and down_count >= profile.context_count:
            reasons = ["down_displacement", "down_body", "lower_close_location"]
            if down_context[0]:
                reasons.append("below_basis")
            if down_context[1]:
                reasons.append("band_position_falling")
            if down_context[2]:
                reasons.append("atr_rising")
            if down_context[3]:
                reasons.append("band_width_rising")
            return EarlyDirectionEvidence(
                state=EarlyDirectionTransition.EARLY_DOWN,
                raw_state=EarlyDirectionTransition.EARLY_DOWN,
                evidence_count=3 + down_count,
                reasons=tuple(reasons),
                **metrics,
            )

        return EarlyDirectionEvidence(**metrics)

    @staticmethod
    def _suppressed(raw: EarlyDirectionEvidence, reason: str, episode_id: int) -> EarlyDirectionEvidence:
        return EarlyDirectionEvidence(
            state=EarlyDirectionTransition.NONE,
            raw_state=raw.state,
            evidence_count=raw.evidence_count,
            reasons=raw.reasons + (reason,),
            displacement_atr=raw.displacement_atr,
            body_atr=raw.body_atr,
            close_location=raw.close_location,
            bollinger_position=raw.bollinger_position,
            bollinger_position_change=raw.bollinger_position_change,
            atr_slope=raw.atr_slope,
            width_slope=raw.width_slope,
            episode_started=False,
            episode_id=episode_id,
        )

    def _start_episode(self, raw: EarlyDirectionEvidence) -> EarlyDirectionEvidence:
        self._episode_id += 1
        self._episode_direction = raw.state
        self._pending_opposite = EarlyDirectionTransition.NONE
        self._pending_opposite_count = 0
        return EarlyDirectionEvidence(
            state=raw.state,
            raw_state=raw.state,
            evidence_count=raw.evidence_count,
            reasons=raw.reasons + ("episode_started",),
            displacement_atr=raw.displacement_atr,
            body_atr=raw.body_atr,
            close_location=raw.close_location,
            bollinger_position=raw.bollinger_position,
            bollinger_position_change=raw.bollinger_position_change,
            atr_slope=raw.atr_slope,
            width_slope=raw.width_slope,
            episode_started=True,
            episode_id=self._episode_id,
        )

    def _apply_episode_lifecycle(
        self,
        raw: EarlyDirectionEvidence,
        export: VolatilityBandsFibFinalExport,
    ) -> EarlyDirectionEvidence:
        if raw.state is EarlyDirectionTransition.NONE:
            self._pending_opposite = EarlyDirectionTransition.NONE
            self._pending_opposite_count = 0
            return raw

        canonical_direction = _directional_regime(_regime(export))
        if canonical_direction is raw.state:
            return self._suppressed(raw, "same_direction_already_candidate_or_confirmed", self._episode_id)

        if self._episode_direction is EarlyDirectionTransition.NONE:
            return self._start_episode(raw)

        if raw.state is self._episode_direction:
            self._pending_opposite = EarlyDirectionTransition.NONE
            self._pending_opposite_count = 0
            return self._suppressed(raw, "same_episode_duplicate", self._episode_id)

        # A true reversal away from an already directional canonical regime may emit
        # immediately. In neutral regimes, require two consecutive opposite raw
        # observations before changing episode direction; this prevents one-bar
        # UP/DOWN flip-flops from becoming separate transition events.
        if canonical_direction is self._episode_direction:
            return self._start_episode(raw)

        if self._pending_opposite is raw.state:
            self._pending_opposite_count += 1
        else:
            self._pending_opposite = raw.state
            self._pending_opposite_count = 1

        if self._pending_opposite_count >= self.OPPOSITE_REARM_OBSERVATIONS:
            return self._start_episode(raw)
        return self._suppressed(raw, "opposite_rearm_pending", self._episode_id)

    def update(self, bar: pd.Series | dict[str, Any]) -> VolatilityDirectionSnapshot:
        row = self._normalize_bar(bar)
        if not bool(row.get("is_closed", True)) or not bool(row.get("is_complete", True)):
            return self._snapshot

        self._rows.append(row)
        core_result = self._core.update(row)
        raw = self._early_evidence(core_result)
        early = self._apply_episode_lifecycle(raw, self._core.final_export)
        self._snapshot = VolatilityDirectionSnapshot(
            timestamp=row["timestamp"],
            core_result=core_result,
            confirmed_export=self._core.final_export,
            early=early,
        )
        return self._snapshot

    def replay(self, frame: pd.DataFrame) -> tuple[VolatilityDirectionSnapshot, ...]:
        self.__init__(self.config)
        out: list[VolatilityDirectionSnapshot] = []
        for _, bar in frame.sort_values("timestamp", kind="stable").iterrows():
            before = len(self._rows)
            snapshot = self.update(bar)
            if len(self._rows) > before:
                out.append(snapshot)
        return tuple(out)

    def snapshot(self) -> VolatilityDirectionSnapshot:
        return self._snapshot

    @property
    def canonical_engine(self) -> VolatilityBandsFibEngine:
        return self._core


__all__ = [
    "EarlyDirectionEvidence",
    "EarlyDirectionTransition",
    "VolatilityDirectionSnapshot",
    "VolatilityDirectionTransitionEngine",
]
