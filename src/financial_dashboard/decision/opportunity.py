from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from financial_dashboard.targeting.models import (
    TargetCluster,
    TargetClusterKind,
    TargetEvidenceType,
    TargetingSnapshot,
)

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
    hard_room_constraint: bool = True
    target_semantics: str | None = None


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


def _token(value: object | None) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value)).strip().upper()


def _target_semantics(cluster: TargetCluster) -> tuple[str, bool]:
    """Classify whether the nearest cluster is a true economic obstacle.

    Structural support/resistance remains a hard room constraint. Nearby liquidity
    magnets remain visible as profit-path context but are soft by themselves: the
    calibrated early-rise audit showed that most such magnets are crossed during the
    same strong move. Reaction-only OB/FVG/engulfing clusters are also soft context.
    Unknown/legacy shapes fail closed to hard=True to preserve safety semantics.
    """

    evidence = tuple(getattr(cluster, "evidence", ()) or ())
    evidence_types = {_token(getattr(item, "evidence_type", None)) for item in evidence}
    evidence_types.discard("")
    kind = _token(getattr(cluster, "kind", None))
    liquidity_anchor = getattr(cluster, "liquidity_anchor", None)

    # Explicit structural S/R remains the hard economic barrier even when the same
    # cluster also contains liquidity evidence.
    if TargetEvidenceType.SUPPORT_RESISTANCE.value in evidence_types:
        return "STRUCTURAL_SUPPORT_RESISTANCE", True

    if (
        liquidity_anchor is not None
        or TargetEvidenceType.LIQUIDITY.value in evidence_types
        or kind == TargetClusterKind.LIQUIDITY_TARGET.value
    ):
        return "LIQUIDITY_MAGNET", False

    reaction_only = {
        TargetEvidenceType.ORDER_BLOCK.value,
        TargetEvidenceType.FVG.value,
        TargetEvidenceType.ENGULFING.value,
    }
    if evidence_types and evidence_types.issubset(reaction_only):
        return "REACTION_TECHNICAL_ZONE", False

    return "UNCLASSIFIED_TARGET", True


def assess_opportunity(
    side: StructuralDirection,
    targeting: TargetingSnapshot | None,
    *,
    calibration: OpportunityCalibration | None,
) -> OpportunityAssessment:
    """Classify directional room without inventing a universal ATR threshold.

    A calibration object is mandatory for AMPLE/MODERATE/COMPRESSED/NONE. If it is
    not supplied, the system explicitly returns UNKNOWN instead of silently using a
    magic constant. Room state and target semantics are deliberately separate:
    nearby liquidity/reaction targets can describe compressed room without becoming
    an automatic hard veto; explicit structural S/R can still impose a hard barrier.
    """

    if side is StructuralDirection.UNRESOLVED:
        return OpportunityAssessment(
            OpportunityState.UNKNOWN,
            None,
            None,
            None,
            ("OPPORTUNITY_SIDE_UNRESOLVED",),
            (),
            False,
            None,
        )
    if targeting is None:
        return OpportunityAssessment(
            OpportunityState.UNKNOWN,
            None,
            None,
            None,
            ("TARGETING_UNAVAILABLE",),
            (),
            False,
            None,
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
            False,
            None,
        )

    room = float(target.distance_atr)
    lineage = _lineage(target)
    quality = str(getattr(target.quality, "value", target.quality))
    semantics, hard_room_constraint = _target_semantics(target)
    semantic_reasons = [f"TARGET_SEMANTICS:{semantics}"]
    if not hard_room_constraint:
        if semantics == "LIQUIDITY_MAGNET":
            semantic_reasons.append("LIQUIDITY_MAGNET_IS_SOFT_ROOM_CONTEXT")
        else:
            semantic_reasons.append("REACTION_TECHNICAL_ZONE_IS_SOFT_ROOM_CONTEXT")

    if calibration is None:
        return OpportunityAssessment(
            OpportunityState.UNKNOWN,
            room,
            target.identity,
            quality,
            ("OPPORTUNITY_CALIBRATION_REQUIRED", *semantic_reasons),
            lineage,
            hard_room_constraint,
            semantics,
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
        (
            f"DIRECTIONAL_ROOM:{room:.6g}ATR",
            f"TARGET_QUALITY:{quality}",
            *semantic_reasons,
        ),
        lineage,
        hard_room_constraint,
        semantics,
    )


__all__ = [
    "OpportunityAssessment",
    "OpportunityCalibration",
    "OpportunityState",
    "assess_opportunity",
]
