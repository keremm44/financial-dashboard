from __future__ import annotations

from dataclasses import replace
from typing import Any

from financial_dashboard.engines.pattern_compression_engine import PatternExport

from .models import (
    EvidenceContext,
    EvidenceDirection,
    EvidenceRole,
    TechnicalEvidenceItem,
    TechnicalEvidencePacket,
    direction_from_value,
    make_evidence_id,
)
from .policy import adapt_pattern as _base_adapt_pattern


def adapt_pattern(
    export: PatternExport,
    context: EvidenceContext,
    *,
    break_direction: int = 0,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    """Expose pattern geometry separately from break/retest trigger lifecycle.

    `classic_direction` is a pattern bias, not proof that a breakout trigger has
    happened. Trigger evidence exists only when the source export has a non-zero
    BREAK_STATE. Retest is a second lifecycle fact in the same Pattern independence
    group, never an additional independent vote.
    """

    packet = _base_adapt_pattern(
        export,
        context,
        break_direction=break_direction,
        source_quality=source_quality,
    )
    base = packet.evidence[0]

    actual_break_direction = (
        direction_from_value(export.break_state)
        if export.break_state not in {None, 0}
        else EvidenceDirection.NEUTRAL
    )
    geometry_direction = direction_from_value(export.classic_direction)

    levels = tuple(
        replace(
            level,
            polarity=actual_break_direction
            if actual_break_direction is not EvidenceDirection.NEUTRAL
            else EvidenceDirection.NEUTRAL,
        )
        if level.level_type == "BREAK_LEVEL"
        else level
        for level in packet.levels
    )

    geometry = _clone_item(
        base,
        context,
        evidence_type="PATTERN_GEOMETRY",
        role=EvidenceRole.STRUCTURE,
        direction=geometry_direction,
        strength=None,
        source_state=export.state,
    )

    evidence: list[TechnicalEvidenceItem] = []
    if export.break_state not in {None, 0}:
        evidence.append(
            _clone_item(
                base,
                context,
                evidence_type="PATTERN_BREAK",
                role=EvidenceRole.TRIGGER,
                direction=actual_break_direction,
                strength=export.break_strength,
                source_state=export.break_state,
            )
        )
        if export.retest_state not in {None, 0}:
            evidence.append(
                _clone_item(
                    base,
                    context,
                    evidence_type="PATTERN_RETEST",
                    role=EvidenceRole.TRIGGER,
                    direction=actual_break_direction,
                    strength=None,
                    source_state=export.retest_state,
                )
            )
    evidence.append(geometry)

    return replace(packet, evidence=tuple(evidence), levels=levels)


def _clone_item(
    base: TechnicalEvidenceItem,
    context: EvidenceContext,
    *,
    evidence_type: str,
    role: EvidenceRole,
    direction: EvidenceDirection,
    strength: float | None,
    source_state: str | int | None,
) -> TechnicalEvidenceItem:
    return replace(
        base,
        id=make_evidence_id(
            source_engine=base.source_engine,
            evidence_type=evidence_type,
            timeframe=context.timeframe,
            source_bar=base.source_bar,
            known_bar=context.known_bar,
            timestamp=context.timestamp,
        ),
        evidence_type=evidence_type,
        role=role,
        direction=direction,
        strength=strength,
        source_state=source_state,
    )
