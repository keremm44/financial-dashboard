from __future__ import annotations

from dataclasses import replace
from typing import Any

from financial_dashboard.engines.support_resistance_engine import RangeState, SupportResistanceExport

from .models import (
    EvidenceContext,
    EvidenceFamily,
    EvidenceRole,
    ProvenanceType,
    TechnicalEvidenceItem,
    TechnicalEvidencePacket,
    direction_from_value,
    make_evidence_id,
)
from .policy import adapt_support_resistance as _base_adapt_support_resistance


def adapt_support_resistance(
    export: SupportResistanceExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    """Keep range geometry as LOCATION and expose only confirmed breaks as TRIGGER.

    `break_confirmed_index` is the causal event/known bar of a confirmed range
    break. It is not the origin of the boundary itself, so the boundary level
    remains unanchored while the trigger evidence may safely use that index.
    """

    packet = _base_adapt_support_resistance(
        export,
        context,
        source_quality=source_quality,
    )
    if (
        export.state != RangeState.BREAK_CONFIRMED.value
        or export.break_direction == 0
        or export.break_confirmed_index is None
    ):
        return packet

    boundary = next(
        (
            level
            for level in packet.levels
            if level.source_engine == "support_resistance" and level.level_type == "BREAK_BOUNDARY"
        ),
        None,
    )
    base = packet.evidence[0] if packet.evidence else None
    level_refs = (boundary.id,) if boundary is not None else ()
    trigger = TechnicalEvidenceItem(
        id=make_evidence_id(
            source_engine="support_resistance",
            evidence_type="SUPPORT_RESISTANCE_BREAK",
            timeframe=context.timeframe,
            source_bar=int(export.break_confirmed_index),
            known_bar=context.known_bar,
            timestamp=context.timestamp,
        ),
        source_engine="support_resistance",
        evidence_type="SUPPORT_RESISTANCE_BREAK",
        timeframe=context.timeframe,
        role=EvidenceRole.TRIGGER,
        family=EvidenceFamily.SUPPORT_RESISTANCE,
        direction=direction_from_value(export.break_direction),
        strength=None,
        quality=None,
        data_quality=base.data_quality if base is not None else context_quality(context, source_quality)[0],
        source_data_quality=base.source_data_quality if base is not None else context_quality(context, source_quality)[1],
        source_bar=int(export.break_confirmed_index),
        known_bar=context.known_bar,
        timestamp=context.timestamp,
        level_refs=level_refs,
        provenance_type=ProvenanceType.ROOT,
        source_state=export.state,
        raw_export=dict(base.raw_export) if base is not None else {},
    )
    return replace(packet, evidence=packet.evidence + (trigger,))


def context_quality(context: EvidenceContext, source_quality: Any):
    # Local import avoids expanding the public surface with another helper.
    from .models import normalize_data_quality

    return normalize_data_quality(
        context.source_data_quality if source_quality is None else source_quality
    )
