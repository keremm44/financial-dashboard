from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .st_behavior_validation import STBehaviorValidationReport


class STHealthyBaseReactionConfidence(StrEnum):
    """Calibratable buyer-reaction confidence for healthy-base recognition.

    This knob changes only how much buyer-reaction evidence is required before a
    mature ST trade may classify a base as healthy. It does not alter thesis
    identity, protective precedence, CONSUMED semantics, or execution urgency.
    """

    DEVELOPING_OR_CONFIRMED = "DEVELOPING_OR_CONFIRMED"
    CONFIRMED_ONLY = "CONFIRMED_ONLY"


@dataclass(frozen=True, slots=True)
class STExitCalibration:
    """Small D-class calibration surface for canonical ST exit policy.

    The default preserves the pre-Step-12 behavior. Alternative values are explicit
    candidates that must be compared with canonical Step-11 early/late metrics before
    adoption; they are not historical-optimum presets.
    """

    healthy_base_reaction_confidence: STHealthyBaseReactionConfidence = (
        STHealthyBaseReactionConfidence.DEVELOPING_OR_CONFIRMED
    )


@dataclass(frozen=True, slots=True)
class STCalibrationComparison:
    """Paired early/late behavior deltas for one candidate versus baseline."""

    premature_harvest_delta: int
    strong_continuation_hold_delta: int
    healthy_base_hold_delta: int
    harvest_idle_seconds_delta: float | None
    protective_delay_seconds_delta: float | None
    same_movement_blocks_delta: int
    novel_setups_executed_delta: int


def _delta_optional(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None:
        return None
    return float(candidate) - float(baseline)


def compare_st_calibration_reports(
    baseline: "STBehaviorValidationReport",
    candidate: "STBehaviorValidationReport",
) -> STCalibrationComparison:
    """Compare both early- and late-exit risk from canonical production reports only."""

    if baseline.source != "CANONICAL" or candidate.source != "CANONICAL":
        raise ValueError("calibration comparison requires canonical validation reports")
    if not baseline.production_performance or not candidate.production_performance:
        raise ValueError("calibration comparison cannot use proxy or legacy performance")

    b = baseline.metrics
    c = candidate.metrics
    return STCalibrationComparison(
        premature_harvest_delta=c.premature_harvest_candidates - b.premature_harvest_candidates,
        strong_continuation_hold_delta=c.strong_continuation_hold_rows - b.strong_continuation_hold_rows,
        healthy_base_hold_delta=c.healthy_base_hold_rows - b.healthy_base_hold_rows,
        harvest_idle_seconds_delta=_delta_optional(
            c.mean_harvest_idle_seconds,
            b.mean_harvest_idle_seconds,
        ),
        protective_delay_seconds_delta=_delta_optional(
            c.mean_protective_delay_seconds,
            b.mean_protective_delay_seconds,
        ),
        same_movement_blocks_delta=c.same_movement_blocks - b.same_movement_blocks,
        novel_setups_executed_delta=c.novel_setups_executed - b.novel_setups_executed,
    )


__all__ = [
    "STCalibrationComparison",
    "STExitCalibration",
    "STHealthyBaseReactionConfidence",
    "compare_st_calibration_reports",
]
