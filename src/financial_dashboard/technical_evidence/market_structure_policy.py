from __future__ import annotations

from dataclasses import replace
from typing import Any

from financial_dashboard.engines.market_structure_evidence import (
    MarketStructureEventExport,
    MarketStructureExport,
)
from financial_dashboard.engines.models import EngineResult

from .models import (
    EvidenceContext,
    EvidenceDirection,
    EvidenceFamily,
    EvidenceRole,
    NormalizedLevel,
    ProvenanceType,
    TechnicalEvidenceItem,
    TechnicalEvidencePacket,
    direction_from_value,
    make_evidence_id,
    make_level_id,
    normalize_data_quality,
    plain_payload,
    score_0_100,
)
from .policy import adapt_market_structure as _base_adapt_market_structure


def adapt_market_structure(
    export: MarketStructureExport,
    context: EvidenceContext,
    *,
    result: EngineResult | None = None,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    """Normalize primary structure plus structured BOS/CHoCH/event evidence.

    `external_state` is already a signed public Market Structure export:
    bullish/transition-up are positive, bearish/transition-down are negative and
    neutral is zero. The optional EngineResult may add quality/state detail, but
    TEL must not silently lose direction merely because that facade object was not
    supplied.

    Structured external/internal event ports are emitted as TRIGGER facts. They
    keep their causal event bar, level and source identities so the Decision
    Engine never has to parse `EngineResult.events` strings.
    """

    packet = _base_adapt_market_structure(
        export,
        context,
        result=result,
        source_quality=source_quality,
    )

    if result is None:
        # Do not promote internal direction when the primary external export is
        # unavailable. Missing external state is an unknown primary structure.
        direction = (
            direction_from_value(export.external_state)
            if export.external_state is not None
            else EvidenceDirection.NEUTRAL
        )
        packet = replace(
            packet,
            evidence=tuple(
                replace(item, direction=direction)
                if item.family is EvidenceFamily.MARKET_STRUCTURE
                else item
                for item in packet.evidence
            ),
        )

    evidence = list(packet.evidence)
    levels = list(packet.levels)
    for scope, event in (("EXTERNAL", export.external_event), ("INTERNAL", export.internal_event)):
        event_item, event_level = _event_evidence(
            event,
            scope=scope,
            context=context,
            source_quality=source_quality,
        )
        if event_level is not None:
            levels.append(event_level)
        if event_item is not None:
            evidence.append(event_item)

    return replace(packet, evidence=tuple(evidence), levels=tuple(levels))


def _event_evidence(
    event: MarketStructureEventExport,
    *,
    scope: str,
    context: EvidenceContext,
    source_quality: Any,
) -> tuple[TechnicalEvidenceItem | None, NormalizedLevel | None]:
    if event.event_type is None:
        return None, None

    canonical_quality, original_quality = normalize_data_quality(
        context.source_data_quality if source_quality is None else source_quality
    )
    source_bar = event.event_bar
    direction = direction_from_value(event.direction)
    event_payload = plain_payload(event)
    level_ref: tuple[str, ...] = ()
    level: NormalizedLevel | None = None

    if event.level is not None:
        level_id = make_level_id(
            source_engine="market_structure",
            level_type=f"{scope}_STRUCTURE_EVENT_LEVEL",
            timeframe=context.timeframe,
            source_bar=source_bar,
            known_bar=context.known_bar,
            price=float(event.level),
            lower=None,
            upper=None,
        )
        level = NormalizedLevel(
            id=level_id,
            source_engine="market_structure",
            level_type=f"{scope}_STRUCTURE_EVENT_LEVEL",
            timeframe=context.timeframe,
            price=float(event.level),
            polarity=direction,
            quality=score_0_100(event.quality),
            source_bar=source_bar,
            known_bar=context.known_bar,
            timestamp=context.timestamp,
            state=event.event_type,
            raw_metadata={
                "scope": event.scope or scope,
                "identity": event.identity,
                "broken_swing_identity": event.broken_swing_identity,
                "broken_source_bar": event.broken_source_bar,
                "origin_swing_identity": event.origin_swing_identity,
                "origin_source_bar": event.origin_source_bar,
                "origin_price": event.origin_price,
                "evidence_text": event.evidence_text,
            },
        )
        level_ref = (level_id,)

    item = TechnicalEvidenceItem(
        id=make_evidence_id(
            source_engine="market_structure",
            evidence_type=f"MARKET_STRUCTURE_{scope}_EVENT",
            timeframe=context.timeframe,
            source_bar=source_bar,
            known_bar=context.known_bar,
            timestamp=context.timestamp,
        ),
        source_engine="market_structure",
        evidence_type=f"MARKET_STRUCTURE_{scope}_EVENT",
        timeframe=context.timeframe,
        role=EvidenceRole.TRIGGER,
        family=EvidenceFamily.MARKET_STRUCTURE,
        direction=direction,
        strength=None,
        quality=score_0_100(event.quality),
        data_quality=canonical_quality,
        source_data_quality=original_quality,
        source_bar=source_bar,
        known_bar=context.known_bar,
        timestamp=context.timestamp,
        level_refs=level_ref,
        provenance_type=ProvenanceType.ROOT,
        source_state=event.event_type,
        raw_export=event_payload if isinstance(event_payload, dict) else {"event": event_payload},
    )
    return item, level
