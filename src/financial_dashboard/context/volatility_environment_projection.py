from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Callable

from financial_dashboard.engines.volatility_bands_fib_engine import (
    BandAgreement,
    BandState,
    VolatilityState,
)
from financial_dashboard.engines.volatility_direction_transition import EarlyDirectionTransition

from .envelope import ContextDataQuality, ContextDomain, FactRef, normalize_context_data_quality
from .lineage import families_for


AvailabilityResolver = Callable[[Any, str], Any]


class VolatilityRangeRegime(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    PENDING = "PENDING"
    BALANCED = "BALANCED"
    CONTRACTING = "CONTRACTING"
    MATURE_SQUEEZE = "MATURE_SQUEEZE"
    EXPANDING = "EXPANDING"
    NORMALIZING = "NORMALIZING"
    SHOCK = "SHOCK"


class ExpansionCharacter(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    NEUTRAL = "NEUTRAL"
    BAND_TEST = "BAND_TEST"
    DIRECTIONAL_CANDIDATE = "DIRECTIONAL_CANDIDATE"
    DIRECTIONAL_CONFIRMED = "DIRECTIONAL_CONFIRMED"
    BAND_ACCEPTED = "BAND_ACCEPTED"
    BAND_TREND = "BAND_TREND"
    MEAN_REVERSION = "MEAN_REVERSION"
    FALSE_EXCURSION = "FALSE_EXCURSION"
    UNSTABLE_CONFLICT = "UNSTABLE_CONFLICT"


class VolatilityTransitionStage(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    NONE = "NONE"
    EARLY_EPISODE = "EARLY_EPISODE"
    CANONICAL_CANDIDATE = "CANONICAL_CANDIDATE"
    CONFIRMED = "CONFIRMED"
    WEAKENING = "WEAKENING"


@dataclass(frozen=True, slots=True)
class VolatilityEnvironmentTimeframeProjection:
    timeframe: str
    ref: FactRef
    range_regime: VolatilityRangeRegime
    expansion_character: ExpansionCharacter
    transition_stage: VolatilityTransitionStage
    expansion_direction: int
    regime_code: int | None
    band_state_code: int | None
    band_agreement_code: int | None
    fib_state_code: int | None
    structure_state_code: int | None
    structure_fib_alignment_code: int | None
    direction_bias_code: int | None
    coherence_code: int | None
    early_state: str
    early_episode_id: int
    early_episode_started: bool
    early_evidence_count: int
    quality: float | None
    regime_band_family_quality: float | None
    structure_swing_family_quality: float | None
    fib_retracement_ratio: float | None


@dataclass(frozen=True, slots=True)
class VolatilityEnvironmentProjection:
    symbol: str
    timeframes: tuple[str, ...]
    timeframe_facts: tuple[VolatilityEnvironmentTimeframeProjection, ...]

    @property
    def refs(self) -> tuple[FactRef, ...]:
        return tuple(item.ref for item in self.timeframe_facts)

    def for_timeframe(self, timeframe: str) -> VolatilityEnvironmentTimeframeProjection:
        normalized = timeframe.strip().lower()
        for item in self.timeframe_facts:
            if item.timeframe == normalized:
                return item
        raise KeyError(f"volatility environment timeframe not found: {timeframe}")

    def available_at(self, as_of: Any) -> "VolatilityEnvironmentProjection":
        return replace(
            self,
            timeframe_facts=tuple(
                item for item in self.timeframe_facts if item.ref.is_available_at(as_of)
            ),
        )


def _enum(enum_type, value):
    if value is None:
        return None
    try:
        return enum_type(int(value))
    except (TypeError, ValueError):
        return None


def _range_regime(regime: VolatilityState | None, unavailable: bool) -> VolatilityRangeRegime:
    if unavailable:
        return VolatilityRangeRegime.UNAVAILABLE
    mapping = {
        VolatilityState.PENDING: VolatilityRangeRegime.PENDING,
        VolatilityState.BALANCED: VolatilityRangeRegime.BALANCED,
        VolatilityState.CONTRACTING: VolatilityRangeRegime.CONTRACTING,
        VolatilityState.SQUEEZE_MATURING: VolatilityRangeRegime.MATURE_SQUEEZE,
        VolatilityState.UP_CANDIDATE: VolatilityRangeRegime.EXPANDING,
        VolatilityState.UP_CONFIRMED: VolatilityRangeRegime.EXPANDING,
        VolatilityState.DOWN_CANDIDATE: VolatilityRangeRegime.EXPANDING,
        VolatilityState.DOWN_CONFIRMED: VolatilityRangeRegime.EXPANDING,
        VolatilityState.WEAKENING: VolatilityRangeRegime.NORMALIZING,
        VolatilityState.ONE_BAR_SHOCK: VolatilityRangeRegime.SHOCK,
    }
    return mapping.get(regime, VolatilityRangeRegime.PENDING)


def _expansion_direction(regime: VolatilityState | None) -> int:
    if regime in {VolatilityState.UP_CANDIDATE, VolatilityState.UP_CONFIRMED}:
        return 1
    if regime in {VolatilityState.DOWN_CANDIDATE, VolatilityState.DOWN_CONFIRMED}:
        return -1
    return 0


def _expansion_character(
    regime: VolatilityState | None,
    band_state: BandState | None,
    band_agreement: BandAgreement | None,
    unavailable: bool,
) -> ExpansionCharacter:
    if unavailable:
        return ExpansionCharacter.UNAVAILABLE
    if band_agreement is BandAgreement.CONFLICT:
        return ExpansionCharacter.UNSTABLE_CONFLICT
    if band_state in {BandState.UPPER_FALSE_EXCURSION, BandState.LOWER_FALSE_EXCURSION}:
        return ExpansionCharacter.FALSE_EXCURSION
    if band_state in {BandState.UPPER_MEAN_REVERSION, BandState.LOWER_MEAN_REVERSION}:
        return ExpansionCharacter.MEAN_REVERSION
    if band_state in {BandState.UPPER_TREND, BandState.LOWER_TREND}:
        return ExpansionCharacter.BAND_TREND
    if band_state in {BandState.UPPER_ACCEPTANCE, BandState.LOWER_ACCEPTANCE}:
        return ExpansionCharacter.BAND_ACCEPTED
    if regime in {VolatilityState.UP_CONFIRMED, VolatilityState.DOWN_CONFIRMED}:
        return ExpansionCharacter.DIRECTIONAL_CONFIRMED
    if regime in {VolatilityState.UP_CANDIDATE, VolatilityState.DOWN_CANDIDATE}:
        return ExpansionCharacter.DIRECTIONAL_CANDIDATE
    if band_state in {BandState.UPPER_TEST, BandState.LOWER_TEST}:
        return ExpansionCharacter.BAND_TEST
    return ExpansionCharacter.NEUTRAL


def _transition_stage(
    regime: VolatilityState | None,
    early_state: EarlyDirectionTransition,
    unavailable: bool,
) -> VolatilityTransitionStage:
    if unavailable:
        return VolatilityTransitionStage.UNAVAILABLE
    if regime in {VolatilityState.UP_CONFIRMED, VolatilityState.DOWN_CONFIRMED}:
        return VolatilityTransitionStage.CONFIRMED
    if regime in {VolatilityState.UP_CANDIDATE, VolatilityState.DOWN_CANDIDATE}:
        return VolatilityTransitionStage.CANONICAL_CANDIDATE
    if regime is VolatilityState.WEAKENING:
        return VolatilityTransitionStage.WEAKENING
    if early_state is not EarlyDirectionTransition.NONE:
        return VolatilityTransitionStage.EARLY_EPISODE
    return VolatilityTransitionStage.NONE


def _fact_ref(
    *,
    symbol: str,
    timeframe: str,
    timestamp: Any,
    available_at: Any,
    native_state: str,
    data_quality: ContextDataQuality,
) -> FactRef:
    causal_family, source_family = families_for(
        ContextDomain.VOLATILITY,
        fact_type="VOLATILITY_ENVIRONMENT",
    )
    return FactRef(
        domain=ContextDomain.VOLATILITY,
        fact_type="VOLATILITY_ENVIRONMENT",
        symbol=symbol,
        timeframe=timeframe,
        native_id=f"VOLATILITY_ENV:{timeframe}:{timestamp}",
        native_state=native_state,
        origin_time=timestamp,
        confirmed_at=timestamp,
        available_at=available_at,
        lineage_id=None,
        causal_family=causal_family,
        source_family=source_family,
        data_quality=data_quality,
    )


def project_volatility_environment(
    replay: Any | None,
    *,
    available_at: AvailabilityResolver,
) -> VolatilityEnvironmentProjection | None:
    if replay is None:
        return None

    rows: list[VolatilityEnvironmentTimeframeProjection] = []
    for timeframe in replay.timeframes:
        latest = replay.for_timeframe(timeframe).latest
        if latest is None or latest.timestamp is None:
            continue
        export = latest.confirmed_export
        quality = normalize_context_data_quality(export.data_quality)
        unavailable = str(export.data_quality).upper() in {
            "WARMUP",
            "SOURCE_GAP",
            "INCOMPLETE_BAR",
            "DATA_LIMITED",
        }
        regime = _enum(VolatilityState, export.regime)
        band_state = _enum(BandState, export.band_state)
        band_agreement = _enum(BandAgreement, export.band_agreement)
        early_state = latest.early.state
        range_regime = _range_regime(regime, unavailable)
        expansion_character = _expansion_character(
            regime,
            band_state,
            band_agreement,
            unavailable,
        )
        transition_stage = _transition_stage(regime, early_state, unavailable)
        native_state = ":".join(
            (range_regime.value, expansion_character.value, transition_stage.value)
        )
        ref = _fact_ref(
            symbol=replay.symbol,
            timeframe=timeframe,
            timestamp=latest.timestamp,
            available_at=available_at(latest.timestamp, timeframe),
            native_state=native_state,
            data_quality=quality,
        )
        rows.append(
            VolatilityEnvironmentTimeframeProjection(
                timeframe=timeframe,
                ref=ref,
                range_regime=range_regime,
                expansion_character=expansion_character,
                transition_stage=transition_stage,
                expansion_direction=_expansion_direction(regime),
                regime_code=None if export.regime is None else int(export.regime),
                band_state_code=None if export.band_state is None else int(export.band_state),
                band_agreement_code=(
                    None if export.band_agreement is None else int(export.band_agreement)
                ),
                fib_state_code=None if export.fib_state is None else int(export.fib_state),
                structure_state_code=(
                    None if export.structure_state is None else int(export.structure_state)
                ),
                structure_fib_alignment_code=(
                    None
                    if export.structure_fib_alignment is None
                    else int(export.structure_fib_alignment)
                ),
                direction_bias_code=(
                    None if export.direction_bias is None else int(export.direction_bias)
                ),
                coherence_code=None if export.coherence is None else int(export.coherence),
                early_state=str(getattr(early_state, "value", early_state)),
                early_episode_id=int(latest.early.episode_id),
                early_episode_started=bool(latest.early.episode_started),
                early_evidence_count=int(latest.early.evidence_count),
                quality=export.quality,
                regime_band_family_quality=export.regime_band_family_quality,
                structure_swing_family_quality=export.structure_swing_family_quality,
                fib_retracement_ratio=export.fib_retracement_ratio,
            )
        )

    return VolatilityEnvironmentProjection(
        symbol=replay.symbol,
        timeframes=tuple(replay.timeframes),
        timeframe_facts=tuple(rows),
    )


__all__ = [
    "ExpansionCharacter",
    "VolatilityEnvironmentProjection",
    "VolatilityEnvironmentTimeframeProjection",
    "VolatilityRangeRegime",
    "VolatilityTransitionStage",
    "project_volatility_environment",
]
