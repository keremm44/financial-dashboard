from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from financial_dashboard.engines.market_structure_events import (
    MarketStructureEventRecord,
    StructureEventConfirmation,
    StructureEventOutcome,
    StructureEventRelevance,
    StructureEventValidity,
)
from financial_dashboard.engines.market_structure_state import BosMaturity, EVENT_BOS
from financial_dashboard.engines.models import Direction
from financial_dashboard.engines.volume_evidence import (
    VolumeEvidenceDataQuality,
    VolumeEvidenceSnapshot,
    VolumeEvidenceStatus,
    link_structure_events_to_volume,
)
from financial_dashboard.engines.volume_participation_engine import VolumeParticipationMetrics
from financial_dashboard.engines.volume_participation_final import (
    FinalParticipationState,
    UnifiedParticipationExport,
)
from financial_dashboard.engines.volume_structure_link_runtime import (
    link_structure_events_to_volume_indexed,
)


def _snapshot(index: int, *, direction: int = 0) -> VolumeEvidenceSnapshot:
    state = (
        FinalParticipationState.UP_CONFIRMED
        if direction > 0
        else FinalParticipationState.DOWN_CONFIRMED
        if direction < 0
        else FinalParticipationState.NEUTRAL
    )
    return VolumeEvidenceSnapshot(
        symbol="ASELS",
        timeframe="1d",
        bar_index=index,
        timestamp=pd.Timestamp("2025-01-01") + pd.Timedelta(days=index),
        segment_id=0,
        status=VolumeEvidenceStatus.READY,
        data_quality=VolumeEvidenceDataQuality.READY,
        state=state.value,
        evidence_direction=direction,
        metrics=VolumeParticipationMetrics(
            data_ready=True,
            volume_usable=True,
            capital_usable=True,
        ),
        audit_export=UnifiedParticipationExport(
            state=state.value,
            support_direction=direction,
            engine_direction=direction,
        ),
        is_confirmed=True,
    )


def _event(index: int, *, direction: Direction = Direction.UP) -> MarketStructureEventRecord:
    timestamp = pd.Timestamp("2025-01-01") + pd.Timedelta(days=index)
    return MarketStructureEventRecord(
        event_uid=f"ASELS:1d:EXTERNAL:{index}",
        identity=index,
        scope="EXTERNAL",
        event_type=EVENT_BOS,
        direction=direction,
        candidate_bar=max(0, index - 1),
        event_bar=index,
        candidate_at=timestamp - pd.Timedelta(days=1),
        confirmed_at=timestamp,
        broken_swing_identity=index + 100,
        broken_source_bar=max(0, index - 2),
        broken_source_at=timestamp - pd.Timedelta(days=2),
        broken_level=100.0 + index,
        origin_swing_identity=index + 200,
        origin_source_bar=max(0, index - 3),
        origin_source_at=timestamp - pd.Timedelta(days=3),
        origin_price=99.0 + index,
        quality=75.0,
        evidence_text="confirmed",
        confirmation_status=StructureEventConfirmation.CONFIRMED,
        validity=StructureEventValidity.VALID,
        relevance=StructureEventRelevance.CURRENT,
        outcome=StructureEventOutcome.OBSERVED,
        symbol="ASELS",
        timeframe="1d",
        bos_maturity=BosMaturity.CONTINUATION,
    )


def test_indexed_multi_event_linker_is_exactly_equal_to_legacy() -> None:
    history = tuple(
        _snapshot(index, direction=1 if index in {5, 10, 15} else 0)
        for index in range(20)
    )
    events = (_event(5), _event(10), _event(15))

    legacy = link_structure_events_to_volume(events, history)
    indexed = link_structure_events_to_volume_indexed(events, history)

    assert indexed == legacy


def test_indexed_linker_preserves_fail_closed_validation() -> None:
    history = tuple(_snapshot(index) for index in range(8))
    bad_namespace = replace(_event(5), timeframe="4h")

    with pytest.raises(ValueError, match="timeframe mismatch"):
        link_structure_events_to_volume_indexed((bad_namespace,), history)
    with pytest.raises(ValueError, match="non-negative"):
        link_structure_events_to_volume_indexed((_event(5),), history, pre_event_bars=-1)
