from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from financial_dashboard.context.projections import project_reaction_evidence
from financial_dashboard.targeting.models import (
    TargetEvidence,
    TargetEvidenceFamily,
    TargetEvidenceType,
    TargetRole,
)


NOW = datetime(2026, 8, 24, 20, 0, tzinfo=timezone.utc)


def test_order_block_behavior_state_is_preserved_in_cross_domain_reaction_fact() -> None:
    evidence = TargetEvidence(
        uid="TE-ob",
        symbol="ASELS",
        timeframe="1h",
        evidence_type=TargetEvidenceType.ORDER_BLOCK,
        family=TargetEvidenceFamily.SUPPLY_DEMAND,
        roles=(TargetRole.DEMAND, TargetRole.REACTION),
        low=100.0,
        high=105.0,
        anchor_price=102.5,
        origin_index=10,
        origin_time=NOW,
        confirmed_at=NOW,
        available_at=NOW,
        source_state="DEEP_MITIGATION",
        target_eligible=True,
        native_origin_id="OB:1h:10:1",
        origin_event_id="OB:1h:10:1",
        source_identity="OB:1h:10:1",
        formation_atr=None,
        source_quality=3.0,
    )
    replay = SimpleNamespace(symbol="ASELS", timeframes=("1h",), evidence=(evidence,))

    projection = project_reaction_evidence(
        symbol="ASELS",
        order_block_replay=replay,
        fvg_engulfing_replay=None,
        data_quality_by_timeframe={"1h": "DATA_OK"},
        requested_timeframes=("1h",),
    )

    assert len(projection.reaction_zones) == 1
    item = projection.reaction_zones[0]
    assert item.evidence_type == "ORDER_BLOCK"
    assert item.ref.native_state == "DEEP_MITIGATION"
    assert item.semantic_role == "REACTION_ZONE"
