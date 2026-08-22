from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Any, Generic, Mapping, Protocol, TypeVar

from financial_dashboard.engines.ham_evidence import (
    FamilySnapshot,
    HamEvidenceSnapshot,
    HamFamily,
)
from financial_dashboard.engines.models import Direction
from financial_dashboard.engines.raw_indicator_dashboard import RawDataQuality
from financial_dashboard.ham_mtf_replay import (
    HAM_EVIDENCE_TIMEFRAMES,
    HamMTFEvidenceReplay,
)


HAM_MAX_ABS_DELTA = 5.0
HAM_DELTA_DECIMALS = 2

# The family proportions retain the source v2.3.7 family emphasis without invoking
# its local SYS state/decision layer. FLOW balance already contains volume trust.
HAM_SUPPORT_FAMILY_WEIGHTS: Mapping[HamFamily, float] = MappingProxyType(
    {
        HamFamily.PRICE: 1.35,
        HamFamily.MOMENTUM: 1.35,
        HamFamily.TIMING: 0.35,
        HamFamily.FLOW: 0.80,
    }
)

# Higher timeframes provide broad context but do not gate or suppress lower ones.
# The fixed full-capacity denominator makes missing evidence conservative rather
# than allowing a single available timeframe to consume the complete +/-5 budget.
HAM_SUPPORT_TIMEFRAME_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {
        "1d": 1.00,
        "4h": 1.00,
        "2h": 0.90,
        "1h": 0.75,
        "30m": 0.60,
    }
)


class HamSupportAlignment(StrEnum):
    AGREES = "AGREES"
    CONFLICTS = "CONFLICTS"
    MIXED = "MIXED"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ConfidenceBearingDecision(Protocol):
    """Minimum core boundary read by the Ham post-core adapter."""

    direction: Direction
    confidence: float


@dataclass(frozen=True, slots=True)
class HamFamilyContribution:
    family: HamFamily
    balance: float | None
    activity: float | None
    coverage: float
    ready: bool
    family_weight: float
    effective_weight: float
    directional_alignment: float | None
    weighted_alignment: float


@dataclass(frozen=True, slots=True)
class HamTimeframeSupport:
    timeframe: str
    available: bool
    timestamp: Any | None
    source_quality: str
    raw_quality: RawDataQuality | None
    profile_ready: bool
    volume_quality: str
    volume_trust: float | None
    timeframe_weight: float
    directional_score: float
    evidence_coverage: float
    families: tuple[HamFamilyContribution, ...]
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HamSupportAssessment:
    """Symmetric Ham agreement with an already-fixed core direction."""

    core_direction: Direction
    ham_delta: float
    directional_score: float
    evidence_coverage: float
    alignment: HamSupportAlignment
    timeframes: tuple[HamTimeframeSupport, ...]
    reasons: tuple[str, ...]
    max_abs_delta: float = HAM_MAX_ABS_DELTA
    contract_version: int = 1

    def timeframe(self, timeframe: str) -> HamTimeframeSupport:
        normalized = timeframe.strip().lower()
        for item in self.timeframes:
            if item.timeframe == normalized:
                return item
        raise KeyError(f"Ham support timeframe not found: {timeframe}")


CoreDecisionT = TypeVar("CoreDecisionT", bound=ConfidenceBearingDecision)


@dataclass(frozen=True, slots=True)
class HamAdjustedConfidence(Generic[CoreDecisionT]):
    """Post-core envelope; the original core decision remains the same object."""

    core: CoreDecisionT
    core_confidence: float
    assessment: HamSupportAssessment
    final_confidence: float
    applied_delta: float

    @property
    def ham_delta(self) -> float:
        return self.assessment.ham_delta


def _round(value: float, decimals: int = HAM_DELTA_DECIMALS) -> float:
    rounded = round(float(value), decimals)
    return 0.0 if rounded == 0.0 else rounded


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _direction(value: object) -> Direction:
    if isinstance(value, bool):
        raise TypeError("core direction must be Direction.UP, DOWN, or NEUTRAL")
    try:
        return Direction(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "core direction must be Direction.UP, DOWN, or NEUTRAL"
        ) from exc


def _finite_between(
    value: float | None,
    *,
    minimum: float,
    maximum: float,
    name: str,
    optional: bool = False,
) -> float | None:
    if value is None and optional:
        return None
    if value is None or not isfinite(float(value)):
        raise ValueError(f"{name} must be finite")
    number = float(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{name} must be in [{minimum}, {maximum}]")
    return number


def _validate_family(
    timeframe: str,
    family: HamFamily,
    evidence: FamilySnapshot,
) -> tuple[float | None, float | None, float]:
    balance = _finite_between(
        evidence.balance,
        minimum=-100.0,
        maximum=100.0,
        name=f"{timeframe} {family.value} balance",
        optional=True,
    )
    activity = _finite_between(
        evidence.activity,
        minimum=0.0,
        maximum=100.0,
        name=f"{timeframe} {family.value} activity",
        optional=True,
    )
    coverage = _finite_between(
        evidence.coverage,
        minimum=0.0,
        maximum=100.0,
        name=f"{timeframe} {family.value} coverage",
    )
    _finite_between(
        evidence.confidence,
        minimum=0.0,
        maximum=1.0,
        name=f"{timeframe} {family.value} confidence",
    )
    assert coverage is not None
    return balance, activity, coverage


def _family_contributions(
    snapshot: HamEvidenceSnapshot,
    *,
    timeframe: str,
    direction_sign: int,
    timeframe_weight: float,
) -> tuple[tuple[HamFamilyContribution, ...], float, float]:
    rows: list[HamFamilyContribution] = []
    weighted_sum = 0.0
    available_weight = 0.0
    for family in HamFamily:
        evidence = snapshot.families.for_family(family)
        balance, activity, coverage = _validate_family(timeframe, family, evidence)
        family_weight = HAM_SUPPORT_FAMILY_WEIGHTS[family]
        usable = (
            snapshot.data_quality is RawDataQuality.OK
            and evidence.ready
            and balance is not None
        )
        effective_weight = (
            timeframe_weight * family_weight * coverage / 100.0
            if usable
            else 0.0
        )
        alignment = (
            float(direction_sign) * balance / 100.0
            if usable and direction_sign != 0 and balance is not None
            else None
        )
        contribution = 0.0 if alignment is None else alignment * effective_weight
        weighted_sum += contribution
        available_weight += effective_weight
        rows.append(
            HamFamilyContribution(
                family=family,
                balance=balance,
                activity=activity,
                coverage=coverage,
                ready=evidence.ready,
                family_weight=family_weight,
                effective_weight=effective_weight,
                directional_alignment=alignment,
                weighted_alignment=contribution,
            )
        )
    return tuple(rows), weighted_sum, available_weight


def _full_capacity() -> float:
    return sum(HAM_SUPPORT_TIMEFRAME_WEIGHTS.values()) * sum(
        HAM_SUPPORT_FAMILY_WEIGHTS.values()
    )


def assess_ham_support(
    core_direction: Direction,
    evidence: HamMTFEvidenceReplay,
) -> HamSupportAssessment:
    """Calculate a deterministic confidence-only Ham delta in ``[-5, +5]``.

    Missing, warmup, or non-ready evidence contributes zero while the denominator
    remains the complete five-timeframe/four-family capacity. Source-quality
    warnings remain visible but do not let an unconsumed open preview alter the
    score derived from the latest confirmed snapshots.
    """

    direction = _direction(core_direction)
    direction_sign = int(direction)
    by_timeframe: dict[str, Any] = {}
    for replay in evidence.timeframe_replays:
        normalized = replay.timeframe.strip().lower()
        if normalized not in HAM_SUPPORT_TIMEFRAME_WEIGHTS:
            raise ValueError(f"unsupported Ham support timeframe: {replay.timeframe}")
        if normalized in by_timeframe:
            raise ValueError(f"duplicate Ham support timeframe: {normalized}")
        by_timeframe[normalized] = replay

    timeframe_rows: list[HamTimeframeSupport] = []
    total_weighted_alignment = 0.0
    total_available_weight = 0.0
    reasons: list[str] = []

    for timeframe in HAM_EVIDENCE_TIMEFRAMES:
        timeframe_weight = HAM_SUPPORT_TIMEFRAME_WEIGHTS[timeframe]
        replay = by_timeframe.get(timeframe)
        if replay is None:
            timeframe_rows.append(
                HamTimeframeSupport(
                    timeframe=timeframe,
                    available=False,
                    timestamp=None,
                    source_quality="MISSING",
                    raw_quality=None,
                    profile_ready=False,
                    volume_quality="MISSING",
                    volume_trust=None,
                    timeframe_weight=timeframe_weight,
                    directional_score=0.0,
                    evidence_coverage=0.0,
                    families=(),
                    reasons=(f"HAM:TIMEFRAME_MISSING:{timeframe}",),
                )
            )
            reasons.append(f"HAM:TIMEFRAME_MISSING:{timeframe}")
            continue

        snapshot = replay.latest
        family_rows, weighted_alignment, available_weight = _family_contributions(
            snapshot,
            timeframe=timeframe,
            direction_sign=direction_sign,
            timeframe_weight=timeframe_weight,
        )
        timeframe_capacity = timeframe_weight * sum(
            HAM_SUPPORT_FAMILY_WEIGHTS.values()
        )
        timeframe_score = weighted_alignment / timeframe_capacity
        timeframe_coverage = available_weight / timeframe_capacity
        row_reasons: list[str] = []
        source_quality = replay.source_quality.status.value
        if source_quality != "OK":
            row_reasons.append(
                f"HAM:SOURCE_QUALITY:{timeframe}:{source_quality}"
            )
        if snapshot.data_quality is not RawDataQuality.OK:
            row_reasons.append(
                f"HAM:RAW_QUALITY:{timeframe}:{snapshot.data_quality.value}"
            )
        non_ready = tuple(
            contribution.family.value
            for contribution in family_rows
            if contribution.effective_weight == 0.0
        )
        if non_ready:
            row_reasons.append(
                f"HAM:FAMILY_NOT_READY:{timeframe}:{','.join(non_ready)}"
            )
        timeframe_rows.append(
            HamTimeframeSupport(
                timeframe=timeframe,
                available=True,
                timestamp=snapshot.timestamp,
                source_quality=source_quality,
                raw_quality=snapshot.data_quality,
                profile_ready=snapshot.profile_ready,
                volume_quality=snapshot.raw.volume_quality.name,
                volume_trust=float(snapshot.raw.volume_trust),
                timeframe_weight=timeframe_weight,
                directional_score=_round(timeframe_score, 6),
                evidence_coverage=_round(timeframe_coverage, 6),
                families=family_rows,
                reasons=tuple(row_reasons),
            )
        )
        total_weighted_alignment += weighted_alignment
        total_available_weight += available_weight
        reasons.extend(row_reasons)

    capacity = _full_capacity()
    directional_score = _clamp(total_weighted_alignment / capacity, -1.0, 1.0)
    evidence_coverage = _clamp(total_available_weight / capacity, 0.0, 1.0)
    ham_delta = _round(
        _clamp(
            directional_score * HAM_MAX_ABS_DELTA,
            -HAM_MAX_ABS_DELTA,
            HAM_MAX_ABS_DELTA,
        )
    )

    if direction is Direction.NEUTRAL:
        alignment = HamSupportAlignment.NOT_APPLICABLE
        ham_delta = 0.0
        reasons.append("HAM:CORE_DIRECTION_NEUTRAL:NO_ADJUSTMENT")
    elif total_available_weight == 0.0:
        alignment = HamSupportAlignment.UNAVAILABLE
        ham_delta = 0.0
        reasons.append("HAM:NO_READY_EVIDENCE:NO_ADJUSTMENT")
    elif ham_delta > 0.0:
        alignment = HamSupportAlignment.AGREES
        reasons.append("HAM:AGREES_WITH_CORE_DIRECTION")
    elif ham_delta < 0.0:
        alignment = HamSupportAlignment.CONFLICTS
        reasons.extend(
            (
                "HAM:CONFLICTS_WITH_CORE_DIRECTION",
                "HAM:CONFLICT_IS_SUPPORT_ONLY_NOT_REVERSAL",
            )
        )
    else:
        alignment = HamSupportAlignment.MIXED
        reasons.append("HAM:MIXED_OR_BALANCED:NO_ADJUSTMENT")

    return HamSupportAssessment(
        core_direction=direction,
        ham_delta=ham_delta,
        directional_score=_round(directional_score, 6),
        evidence_coverage=_round(evidence_coverage, 6),
        alignment=alignment,
        timeframes=tuple(timeframe_rows),
        reasons=tuple(dict.fromkeys(reasons)),
    )


def apply_ham_confidence(
    core: CoreDecisionT,
    evidence: HamMTFEvidenceReplay,
) -> HamAdjustedConfidence[CoreDecisionT]:
    """Wrap ``core`` and adjust confidence only; never mutate or replace it."""

    direction = _direction(core.direction)
    confidence = _finite_between(
        core.confidence,
        minimum=0.0,
        maximum=100.0,
        name="core confidence",
    )
    assert confidence is not None
    assessment = assess_ham_support(direction, evidence)
    final_confidence = _round(
        _clamp(confidence + assessment.ham_delta, 0.0, 100.0)
    )
    return HamAdjustedConfidence(
        core=core,
        core_confidence=confidence,
        assessment=assessment,
        final_confidence=final_confidence,
        applied_delta=_round(final_confidence - confidence),
    )


__all__ = [
    "ConfidenceBearingDecision",
    "HAM_DELTA_DECIMALS",
    "HAM_MAX_ABS_DELTA",
    "HAM_SUPPORT_FAMILY_WEIGHTS",
    "HAM_SUPPORT_TIMEFRAME_WEIGHTS",
    "HamAdjustedConfidence",
    "HamFamilyContribution",
    "HamSupportAlignment",
    "HamSupportAssessment",
    "HamTimeframeSupport",
    "apply_ham_confidence",
    "assess_ham_support",
]
