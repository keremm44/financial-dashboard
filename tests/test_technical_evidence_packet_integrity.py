import pytest

from financial_dashboard.technical_evidence import (
    EvidenceDirection,
    EvidenceFamily,
    EvidenceRole,
    NormalizedLevel,
    TechnicalEvidenceItem,
    TechnicalEvidencePacket,
)


def _item(*, timeframe: str) -> TechnicalEvidenceItem:
    return TechnicalEvidenceItem(
        id=f"item-{timeframe}",
        source_engine="market_structure",
        evidence_type="TEST",
        timeframe=timeframe,
        role=EvidenceRole.STRUCTURE,
        family=EvidenceFamily.MARKET_STRUCTURE,
        direction=EvidenceDirection.BULL,
        known_bar=10,
    )


def test_packet_rejects_evidence_from_another_timeframe():
    with pytest.raises(ValueError, match="another timeframe"):
        TechnicalEvidencePacket(
            timeframe="2h",
            known_bar=10,
            timestamp="2026-08-20T18:00:00+03:00",
            evidence=(_item(timeframe="4h"),),
        )


def test_packet_rejects_level_from_another_timeframe():
    level = NormalizedLevel(
        id="level-4h",
        source_engine="support_resistance",
        level_type="SUPPORT",
        timeframe="4h",
        price=100.0,
        known_bar=10,
    )
    with pytest.raises(ValueError, match="another timeframe"):
        TechnicalEvidencePacket(
            timeframe="2h",
            known_bar=10,
            timestamp="2026-08-20T18:00:00+03:00",
            levels=(level,),
        )


def test_packet_timeframe_match_is_case_insensitive():
    packet = TechnicalEvidencePacket(
        timeframe="2H",
        known_bar=10,
        timestamp="2026-08-20T18:00:00+03:00",
        evidence=(_item(timeframe="2h"),),
    )
    assert packet.evidence[0].timeframe == "2h"
