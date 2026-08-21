from __future__ import annotations

import pytest

from financial_dashboard.engines.market_structure_evidence import MarketStructureExport
from financial_dashboard.technical_evidence import (
    EvidenceContext,
    EvidenceDirection,
    EvidenceFamily,
    EvidenceGraphError,
    EvidenceRole,
    NormalizedLevel,
    TechnicalEvidenceItem,
    TechnicalEvidencePacket,
    adapt_market_structure,
    build_technical_evidence_bundle,
)


TS = "2026-08-20T18:00:00+03:00"


def _ctx() -> EvidenceContext:
    return EvidenceContext(
        timeframe="2h",
        known_bar=120,
        timestamp=TS,
        source_data_quality="OK",
        is_closed=True,
        is_complete=True,
    )


def _ms_export(*, external_state: float | None, internal_state: float | None = 0.0) -> MarketStructureExport:
    return MarketStructureExport(
        external_state=external_state,
        internal_state=internal_state,
        evidence_score=72.0,
        external_protected_low=None,
        external_protected_high=None,
        external_weak_low=None,
        external_weak_high=None,
        internal_protected_low=None,
        internal_protected_high=None,
        internal_weak_low=None,
        internal_weak_high=None,
    )


def _item(*, known_bar: int | None = 120, timestamp: str | None = TS) -> TechnicalEvidenceItem:
    return TechnicalEvidenceItem(
        id=f"item-{known_bar}-{timestamp}",
        source_engine="market_structure",
        evidence_type="TEST",
        timeframe="2h",
        role=EvidenceRole.STRUCTURE,
        family=EvidenceFamily.MARKET_STRUCTURE,
        direction=EvidenceDirection.BULL,
        known_bar=known_bar,
        timestamp=timestamp,
    )


def test_market_structure_direction_falls_back_to_signed_external_export_without_engine_result():
    bullish = adapt_market_structure(_ms_export(external_state=2.0), _ctx()).evidence[0]
    transition_up = adapt_market_structure(_ms_export(external_state=1.0), _ctx()).evidence[0]
    bearish = adapt_market_structure(_ms_export(external_state=-2.0), _ctx()).evidence[0]
    transition_down = adapt_market_structure(_ms_export(external_state=-1.0), _ctx()).evidence[0]
    neutral = adapt_market_structure(_ms_export(external_state=0.0), _ctx()).evidence[0]

    assert bullish.direction is EvidenceDirection.BULL
    assert transition_up.direction is EvidenceDirection.BULL
    assert bearish.direction is EvidenceDirection.BEAR
    assert transition_down.direction is EvidenceDirection.BEAR
    assert neutral.direction is EvidenceDirection.NEUTRAL


def test_market_structure_missing_external_state_does_not_promote_internal_direction():
    item = adapt_market_structure(
        _ms_export(external_state=None, internal_state=2.0),
        _ctx(),
    ).evidence[0]

    assert item.direction is EvidenceDirection.NEUTRAL
    assert item.source_state is None
    assert item.raw_export["internal_state"] == 2.0


def test_bundle_rejects_member_known_bar_from_another_snapshot():
    packet = TechnicalEvidencePacket(
        timeframe="2h",
        known_bar=120,
        timestamp=TS,
        evidence=(_item(known_bar=119),),
    )

    with pytest.raises(EvidenceGraphError, match="snapshot bar mismatch"):
        build_technical_evidence_bundle((packet,))


def test_bundle_rejects_member_timestamp_from_another_snapshot():
    packet = TechnicalEvidencePacket(
        timeframe="2h",
        known_bar=120,
        timestamp=TS,
        evidence=(_item(timestamp="2026-08-20T16:00:00+03:00"),),
    )

    with pytest.raises(EvidenceGraphError, match="snapshot timestamp mismatch"):
        build_technical_evidence_bundle((packet,))


def test_bundle_rejects_level_timestamp_from_another_snapshot():
    level = NormalizedLevel(
        id="level",
        source_engine="support_resistance",
        level_type="SUPPORT",
        timeframe="2h",
        price=100.0,
        known_bar=120,
        timestamp="2026-08-20T16:00:00+03:00",
    )
    packet = TechnicalEvidencePacket(
        timeframe="2h",
        known_bar=120,
        timestamp=TS,
        levels=(level,),
    )

    with pytest.raises(EvidenceGraphError, match="level snapshot timestamp mismatch"):
        build_technical_evidence_bundle((packet,))


def test_explicit_as_of_timestamp_fails_closed_when_timezone_comparison_is_unverifiable():
    packet = TechnicalEvidencePacket(
        timeframe="2h",
        known_bar=120,
        timestamp="2026-08-20T18:00:00",
    )

    with pytest.raises(EvidenceGraphError, match="not comparable"):
        build_technical_evidence_bundle(
            (packet,),
            as_of_timestamp="2026-08-20T18:00:00+03:00",
        )
