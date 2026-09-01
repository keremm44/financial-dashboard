from __future__ import annotations

from dataclasses import dataclass

from .structural import DecisionHorizon


@dataclass(frozen=True, slots=True)
class HorizonEvaluationProfile:
    """Typed Decision-side roles for one LT/ST assessment.

    This profile describes how the Decision layer consumes already-causal facts. It
    does not define MarketState factual aggregation and it does not own BUY/SELL.
    """

    horizon: DecisionHorizon
    structural_authority_timeframe: str
    secondary_structural_timeframe: str | None
    permission_anchor_timeframe: str
    permission_context_timeframes: tuple[str, ...]
    reaction_timeframes: tuple[str, ...]
    participation_timeframe: str
    environment_timeframe: str
    timing_timeframe: str
    execution_timeframe: str


LONG_TERM_EVALUATION_PROFILE = HorizonEvaluationProfile(
    horizon=DecisionHorizon.LONG_TERM,
    structural_authority_timeframe="1d",
    secondary_structural_timeframe="4h",
    permission_anchor_timeframe="1d",
    permission_context_timeframes=("4h", "2h", "1h"),
    reaction_timeframes=("1d", "4h", "2h", "1h"),
    participation_timeframe="4h",
    environment_timeframe="4h",
    timing_timeframe="1h",
    execution_timeframe="30m",
)

SHORT_TERM_EVALUATION_PROFILE = HorizonEvaluationProfile(
    horizon=DecisionHorizon.SHORT_TERM,
    structural_authority_timeframe="1h",
    secondary_structural_timeframe=None,
    permission_anchor_timeframe="1h",
    permission_context_timeframes=("30m",),
    reaction_timeframes=("4h", "2h", "1h", "30m"),
    participation_timeframe="1h",
    environment_timeframe="4h",
    timing_timeframe="30m",
    execution_timeframe="30m",
)


def horizon_evaluation_profile(horizon: DecisionHorizon) -> HorizonEvaluationProfile:
    if horizon is DecisionHorizon.LONG_TERM:
        return LONG_TERM_EVALUATION_PROFILE
    return SHORT_TERM_EVALUATION_PROFILE


__all__ = [
    "HorizonEvaluationProfile",
    "LONG_TERM_EVALUATION_PROFILE",
    "SHORT_TERM_EVALUATION_PROFILE",
    "horizon_evaluation_profile",
]
