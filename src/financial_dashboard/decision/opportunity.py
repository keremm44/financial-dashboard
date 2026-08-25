from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from financial_dashboard.targeting.models import TargetCluster, TargetingSnapshot

from .structural import StructuralDirection


class OpportunityState(StrEnum):
    AMPLE = "AMPLE"
    MODERATE = "MODERATE"
    COMPRESSED = "COMPRESSED"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class OpportunityCalibration:
    """Class-C boundaries; values must come from historical calibration.

    No universal ATR values are embedded in the decision layer.
    """

    none_max_atr: float
    compressed_max_atr: float
    moderate_max_atr: float

    def __post_init__(self) -> None:
        if self.none_max_atr < 0:
            raise ValueError("none_max_atr must be >= 0")
        if not self.none_max_atr < self.compressed_max_atr < self.moderate_max_atr:
            raise ValueError("opportunity ATR boundaries must be strictly increasing")


@dataclass(frozen=True, slots=True)
class OpportunityAssessment:
    state: OpportunityState
    room_atr: float | None
    target_identity: str | None
    target_quality: str | None
    reasons: tuple[str, ...]
    source_lineage: tuple[str, ...]


def _target_for_side(
    side: StructuralDirection,
    targeting: TargetingSnapshot,
) -> TargetCluster | None:
    if side is StructuralDirection.LONG:
        return targeting.nearest_upside_target
    if side is StructuralDirection.SHORT:
        return targeting.nearest_downside_target
    return None


def _lineage(cluster: TargetCluster | None) -> tuple[str, ...]:
    if cluster is None:
        return ()
    keys = {
        str(item.origin_event_id)
        for item in cluster.evidence
        if str(item.origin_event_id).strip()
    }
    return tuple(sorted(keys))


def assess_opportunity(
    side: StructuralDirection,
    targeting: TargetingSnapshot | None,
    *,
    calibration: OpportunityCalibration | None,
) -> OpportunityAssessment:
    """Classify directional room without inventing a universal ATR threshold.

    A calibration object is mandatory for AMPLE/MODERATE/COMPRESSED/NONE. If it is
    not supplied, the system explicitly returns UNKNOWN instead of silently using a
    magic constant.
    """

    if side is StructuralDirection.UNRESOLVED:
        return OpportunityAssessment(
            OpportunityState.UNKNOWN,
            None,
            None,
            None,
            ("OPPORTUNITY_SIDE_UNRESOLVED",),
            (),
        )
    if targeting is None:
        return OpportunityAssessment(
            OpportunityState.UNKNOWN,
            None,
            None,
            None,
            ("TARGETING_UNAVAILABLE",),
            (),
        )

    target = _target_for_side(side, targeting)
    if target is None:
        return OpportunityAssessment(
            OpportunityState.UNKNOWN,
            None,
            None,
            None,
            ("NO_DIRECTIONAL_TARGET_OBSERVED_NOT_CLEAR_PATH",),
            (),
        )

    room = float(target.distance_atr)
    lineage = _lineage(target)
    quality = str(getattr(target.quality, "value", target.quality))
    if calibration is None:
        return OpportunityAssessment(
            OpportunityState.UNKNOWN,
            room,
            target.identity,
            quality,
            ("OPPORTUNITY_CALIBRATION_REQUIRED",),
            lineage,
        )

    if room <= calibration.none_max_atr:
        state = OpportunityState.NONE
    elif room <= calibration.compressed_max_atr:
        state = OpportunityState.COMPRESSED
    elif room <= calibration.moderate_max_atr:
        state = OpportunityState.MODERATE
    else:
        state = OpportunityState.AMPLE

    return OpportunityAssessment(
        state,
        room,
        target.identity,
        quality,
        (f"DIRECTIONAL_ROOM:{room:.6g}ATR", f"TARGET_QUALITY:{quality}"),
        lineage,
    )


__all__ = [
    "OpportunityAssessment",
    "OpportunityCalibration",
    "OpportunityState",
    "assess_opportunity",
]
