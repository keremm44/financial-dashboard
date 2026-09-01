from __future__ import annotations

from dataclasses import replace

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.context.projections import StructuralFactsProjection


def normalize_decision_structure_projection(structural):
    """Normalize price-only Structure quality for Decision without mutating source diagnostics.

    Generic OHLCV quality can mark a batch DATA_LIMITED for warnings such as zero
    volume or an open/incomplete source tail. Market Structure is price-only and its
    replay already excludes unsafe candles, so those generic limitations must not
    erase 1D/1H structural authority inside the Decision layer. Other domain
    projections retain their native quality unchanged.

    Opaque test doubles are returned unchanged. Production calls provide
    ``StructuralFactsProjection``.
    """

    if not isinstance(structural, StructuralFactsProjection):
        return structural

    changed = False
    rows = []
    for row in structural.timeframe_facts:
        if row.data_quality is not ContextDataQuality.DATA_LIMITED:
            rows.append(row)
            continue
        changed = True
        events = tuple(
            replace(
                event,
                ref=replace(event.ref, data_quality=ContextDataQuality.VALID),
            )
            if event.ref.data_quality is ContextDataQuality.DATA_LIMITED
            else event
            for event in row.events
        )
        rows.append(
            replace(
                row,
                data_quality=ContextDataQuality.VALID,
                events=events,
            )
        )
    if not changed:
        return structural
    return replace(structural, timeframe_facts=tuple(rows))


__all__ = ["normalize_decision_structure_projection"]
