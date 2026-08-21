from __future__ import annotations

from dataclasses import replace
from typing import Any

from financial_dashboard.engines.market_structure_evidence import MarketStructureExport
from financial_dashboard.engines.models import EngineResult

from .models import EvidenceContext, EvidenceDirection, EvidenceFamily, TechnicalEvidencePacket, direction_from_value
from .policy import adapt_market_structure as _base_adapt_market_structure


def adapt_market_structure(
    export: MarketStructureExport,
    context: EvidenceContext,
    *,
    result: EngineResult | None = None,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    """Preserve structural direction even when only the permanent export is supplied.

    `external_state` is already a signed public Market Structure export:
    bullish/transition-up are positive, bearish/transition-down are negative and
    neutral is zero.  The optional EngineResult may add quality/state detail, but
    TEL must not silently lose direction merely because that facade object was not
    supplied.

    We intentionally do not fall back to `internal_state` when external_state is
    unavailable: the single normalized MARKET_STRUCTURE item represents the
    external/primary structure contract, and promoting internal direction would
    reinterpret a partially unavailable external state.
    """

    packet = _base_adapt_market_structure(
        export,
        context,
        result=result,
        source_quality=source_quality,
    )
    if result is not None:
        return packet

    direction = (
        direction_from_value(export.external_state)
        if export.external_state is not None
        else EvidenceDirection.NEUTRAL
    )
    evidence = tuple(
        replace(item, direction=direction)
        if item.family is EvidenceFamily.MARKET_STRUCTURE
        else item
        for item in packet.evidence
    )
    return replace(packet, evidence=evidence)
