from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

import pandas as pd

from .lifecycle_persistence import (
    CANONICAL_LIFECYCLE_CONTRACT_VERSION,
    TRADE_LIFECYCLE_STATE_SCHEMA_VERSION,
)
from .st_behavior_validation import STCanonicalBehaviorReport
from .st_calibration import STExitCalibration, STHealthyBaseReactionConfidence
from .st_thesis_identity import STThesisFamily


ST_IMPLEMENTATION_FREEZE_VERSION = 1
_FROZEN_SCHEMA_VERSION = 6
_FROZEN_LIFECYCLE_CONTRACT_VERSION = 9
_FROZEN_RESOLVED_THESIS_FAMILIES = frozenset(
    {
        STThesisFamily.PULLBACK_CONTINUATION,
        STThesisFamily.BREAKOUT_ACCEPTANCE,
        STThesisFamily.FAILED_SELL_RECLAIM,
    }
)
_FROZEN_DEFAULT_REACTION_CONFIDENCE = STHealthyBaseReactionConfidence.DEVELOPING_OR_CONFIRMED


class STImplementationFreezeStatus(StrEnum):
    VALIDATION_REQUIRED = "VALIDATION_REQUIRED"
    PRODUCTION_CANDIDATE = "PRODUCTION_CANDIDATE"


@dataclass(frozen=True, slots=True)
class STRegimeValidationEvidence:
    """One real canonical historical validation slice used for release review.

    `regime_id` is a human-owned market-regime label. The freeze gate does not infer
    regimes from PnL or tune thresholds from history; it only prevents one slice,
    duplicate periods, readiness proxies, or legacy streams from being promoted as
    cross-regime evidence.
    """

    regime_id: str
    period_start: pd.Timestamp
    period_end: pd.Timestamp
    report: STCanonicalBehaviorReport

    def __post_init__(self) -> None:
        if not self.regime_id.strip():
            raise ValueError("ST freeze regime_id must be non-empty")
        start = pd.Timestamp(self.period_start)
        end = pd.Timestamp(self.period_end)
        if end <= start:
            raise ValueError("ST freeze regime period must have positive duration")
        if self.report.source != "CANONICAL" or not self.report.production_performance:
            raise ValueError("ST freeze evidence requires canonical production validation")
        if self.report.proxy_row_count:
            raise ValueError("ST freeze evidence cannot contain readiness proxy rows")
        if self.report.metrics.completed_trade_count <= 0:
            raise ValueError("ST freeze regime evidence requires at least one completed ST trade")

    @property
    def period_key(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        return (pd.Timestamp(self.period_start), pd.Timestamp(self.period_end))


@dataclass(frozen=True, slots=True)
class STCrossRegimeAcceptanceReview:
    """Explicit release review for roadmap claims that are not safe numeric tunables.

    These fields are governance attestations after inspecting canonical Step-11
    metrics across distinct real market regimes. They deliberately avoid inventing
    fixed profit, time, or bar thresholds.
    """

    strong_trends_not_systematically_cut_early: bool
    mature_dead_ranges_not_systematically_held_too_long: bool
    protective_exits_not_systematically_late: bool
    normal_corrections_not_systematically_exited: bool
    same_movement_churn_controlled: bool
    genuine_new_setups_not_systematically_blocked: bool
    review_note: str

    def __post_init__(self) -> None:
        if not self.review_note.strip():
            raise ValueError("ST freeze cross-regime review requires a non-empty note")

    @property
    def accepted(self) -> bool:
        return all(
            (
                self.strong_trends_not_systematically_cut_early,
                self.mature_dead_ranges_not_systematically_held_too_long,
                self.protective_exits_not_systematically_late,
                self.normal_corrections_not_systematically_exited,
                self.same_movement_churn_controlled,
                self.genuine_new_setups_not_systematically_blocked,
            )
        )


@dataclass(frozen=True, slots=True)
class STImplementationFreezeAssessment:
    status: STImplementationFreezeStatus
    freeze_version: int
    schema_version: int
    lifecycle_contract_version: int
    regime_count: int
    blockers: tuple[str, ...]
    evidence_regime_ids: tuple[str, ...]

    @property
    def production_candidate(self) -> bool:
        return self.status is STImplementationFreezeStatus.PRODUCTION_CANDIDATE


def _mechanical_freeze_blockers() -> list[str]:
    blockers: list[str] = []
    if TRADE_LIFECYCLE_STATE_SCHEMA_VERSION != _FROZEN_SCHEMA_VERSION:
        blockers.append("freeze/schema-version-changed")
    if CANONICAL_LIFECYCLE_CONTRACT_VERSION != _FROZEN_LIFECYCLE_CONTRACT_VERSION:
        blockers.append("freeze/lifecycle-contract-changed")

    resolved = frozenset(family for family in STThesisFamily if family is not STThesisFamily.UNRESOLVED)
    if resolved != _FROZEN_RESOLVED_THESIS_FAMILIES:
        blockers.append("freeze/thesis-families-changed")
    if STThesisFamily.UNRESOLVED not in set(STThesisFamily):
        blockers.append("freeze/unresolved-identity-missing")

    if (
        STExitCalibration().healthy_base_reaction_confidence
        is not _FROZEN_DEFAULT_REACTION_CONFIDENCE
    ):
        blockers.append("freeze/default-exit-calibration-changed")
    return blockers


def assess_st_implementation_freeze(
    evidence: Iterable[STRegimeValidationEvidence] = (),
    *,
    review: STCrossRegimeAcceptanceReview | None = None,
) -> STImplementationFreezeAssessment:
    """Assess Step-13 release readiness without changing trading behavior.

    Production-candidate status requires frozen mechanical contracts, at least two
    distinct canonical historical regime slices, and an explicit cross-regime review.
    Missing empirical evidence is a blocker rather than something inferred from unit
    tests, readiness proxies, legacy events, or a single historical example.
    """

    values = tuple(evidence)
    blockers = _mechanical_freeze_blockers()

    regime_ids = tuple(item.regime_id.strip() for item in values)
    unique_regimes = set(regime_ids)
    period_keys = tuple(item.period_key for item in values)

    if len(values) < 2:
        blockers.append("freeze/multiple-market-regimes-required")
    if len(unique_regimes) != len(values):
        blockers.append("freeze/regime-ids-must-be-distinct")
    if len(set(period_keys)) != len(values):
        blockers.append("freeze/regime-periods-must-be-distinct")

    if review is None:
        blockers.append("freeze/cross-regime-review-required")
    elif not review.accepted:
        blockers.append("freeze/cross-regime-behavior-not-accepted")

    canonical_blockers = tuple(dict.fromkeys(blockers))
    status = (
        STImplementationFreezeStatus.PRODUCTION_CANDIDATE
        if not canonical_blockers
        else STImplementationFreezeStatus.VALIDATION_REQUIRED
    )
    return STImplementationFreezeAssessment(
        status=status,
        freeze_version=ST_IMPLEMENTATION_FREEZE_VERSION,
        schema_version=TRADE_LIFECYCLE_STATE_SCHEMA_VERSION,
        lifecycle_contract_version=CANONICAL_LIFECYCLE_CONTRACT_VERSION,
        regime_count=len(unique_regimes),
        blockers=canonical_blockers,
        evidence_regime_ids=tuple(sorted(unique_regimes)),
    )


__all__ = [
    "STCrossRegimeAcceptanceReview",
    "STImplementationFreezeAssessment",
    "STImplementationFreezeStatus",
    "STRegimeValidationEvidence",
    "ST_IMPLEMENTATION_FREEZE_VERSION",
    "assess_st_implementation_freeze",
]
