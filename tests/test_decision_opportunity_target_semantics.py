from financial_dashboard.decision.opportunity import _target_semantics
from financial_dashboard.targeting.models import (
    TargetCluster,
    TargetClusterKind,
    TargetClusterQuality,
    TargetEvidence,
    TargetEvidenceFamily,
    TargetEvidenceType,
    TargetRole,
    TargetSide,
)


def _evidence(evidence_type: TargetEvidenceType, uid: str) -> TargetEvidence:
    return TargetEvidence(
        uid=uid,
        symbol="ASELS",
        timeframe="1h",
        evidence_type=evidence_type,
        family=(
            TargetEvidenceFamily.STRUCTURAL
            if evidence_type is TargetEvidenceType.SUPPORT_RESISTANCE
            else TargetEvidenceFamily.REACTION
        ),
        roles=(TargetRole.MAGNET,),
        low=100.0,
        high=101.0,
        anchor_price=100.5,
        origin_index=1,
        origin_time=1,
        confirmed_at=1,
        available_at=1,
        source_state="CONFIRMED",
        target_eligible=True,
        native_origin_id=f"native-{uid}",
        origin_event_id=f"event-{uid}",
        source_identity=f"source-{uid}",
    )


def _cluster(*evidence: TargetEvidence, liquidity_anchor=100.5) -> TargetCluster:
    return TargetCluster(
        identity="target-1",
        side=TargetSide.ABOVE,
        kind=TargetClusterKind.LIQUIDITY_TARGET,
        envelope_low=100.0,
        envelope_high=101.0,
        core_low=100.0,
        core_high=101.0,
        liquidity_anchor=liquidity_anchor,
        distance_price=1.0,
        distance_percent=1.0,
        distance_atr=0.5,
        evidence=tuple(evidence),
        raw_source_count=len(evidence),
        independent_origin_count=len(evidence),
        independent_family_count=len({item.family for item in evidence}),
        timeframes_present=("1h",),
        roles_present=(TargetRole.MAGNET,),
        quality=TargetClusterQuality.SUPPORTED,
    )


def test_nearby_liquidity_magnet_is_soft_room_context() -> None:
    cluster = _cluster(_evidence(TargetEvidenceType.LIQUIDITY, "liq"))

    semantics, hard = _target_semantics(cluster)

    assert semantics == "LIQUIDITY_MAGNET"
    assert hard is False


def test_structural_resistance_remains_hard_even_with_liquidity() -> None:
    cluster = _cluster(
        _evidence(TargetEvidenceType.LIQUIDITY, "liq"),
        _evidence(TargetEvidenceType.SUPPORT_RESISTANCE, "sr"),
    )

    semantics, hard = _target_semantics(cluster)

    assert semantics == "STRUCTURAL_SUPPORT_RESISTANCE"
    assert hard is True
