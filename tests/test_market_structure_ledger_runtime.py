from __future__ import annotations

from dataclasses import replace

import pandas as pd

from financial_dashboard.engines.market_structure_events import (
    MarketStructureEventLedger,
    StructureEventOutcome,
    StructureEventRelevance,
    StructureEventValidity,
)
from financial_dashboard.engines.market_structure_state import (
    EVENT_BOS,
    EVENT_CHOCH,
    EVENT_TRANSITION_FAIL,
    StructureEvent,
)


def _rows(count: int = 40) -> list[dict[str, object]]:
    timestamps = pd.date_range("2026-01-01", periods=count, freq="h", tz="UTC")
    return [
        {
            "timestamp": timestamp,
            "high": 110.0 + index,
            "low": 90.0 - index * 0.1,
            "close": 100.0 + index * 0.2,
        }
        for index, timestamp in enumerate(timestamps)
    ]


def _event(
    identity: int,
    event_type: str,
    direction: int,
    event_bar: int,
    scope: str,
) -> StructureEvent:
    return StructureEvent(
        valid=True,
        identity=identity,
        scope=scope,
        event_type=event_type,
        direction=direction,
        candidate_bar=event_bar - 1,
        event_bar=event_bar,
        broken_swing_identity=identity * 10,
        broken_source_bar=max(0, event_bar - 3),
        origin_swing_identity=identity * 10 + 1,
        origin_source_bar=max(0, event_bar - 2),
        level=100.0 + identity,
        origin_price=99.0 + identity,
        quality=70.0,
        evidence_text=event_type,
    )


def _quadratic_reference(records, current_bar: int):
    annotated = []

    def latest_index(predicate):
        for index in range(len(annotated) - 1, -1, -1):
            if predicate(annotated[index]):
                return index
        return None

    for base_record in records:
        same_index = latest_index(
            lambda record: (
                record.scope == base_record.scope
                and record.event_type == base_record.event_type
                and record.direction is base_record.direction
                and record.relevance is StructureEventRelevance.CURRENT
            )
        )
        if same_index is not None:
            annotated[same_index] = replace(
                annotated[same_index],
                relevance=StructureEventRelevance.SUPERSEDED,
            )

        if base_record.event_type == EVENT_BOS:
            choch_index = latest_index(
                lambda record: (
                    record.scope == base_record.scope
                    and record.event_type == EVENT_CHOCH
                    and record.direction is base_record.direction
                    and record.validity is StructureEventValidity.VALID
                    and record.outcome is StructureEventOutcome.PENDING
                )
            )
            if choch_index is not None:
                annotated[choch_index] = replace(
                    annotated[choch_index],
                    relevance=StructureEventRelevance.HISTORICAL,
                    outcome=StructureEventOutcome.FOLLOW_THROUGH_CONFIRMED,
                    confirmed_by_event_uid=base_record.event_uid,
                )

        if base_record.event_type == EVENT_TRANSITION_FAIL:
            choch_index = latest_index(
                lambda record: (
                    record.scope == base_record.scope
                    and record.event_type == EVENT_CHOCH
                    and int(record.direction) == -int(base_record.direction)
                    and record.validity is StructureEventValidity.VALID
                    and record.outcome is StructureEventOutcome.PENDING
                )
            )
            if choch_index is not None:
                annotated[choch_index] = replace(
                    annotated[choch_index],
                    validity=StructureEventValidity.FAILED,
                    relevance=StructureEventRelevance.HISTORICAL,
                    outcome=StructureEventOutcome.FAILED,
                    failed_by_event_uid=base_record.event_uid,
                )

        annotated.append(base_record)

    return tuple(
        replace(record, age_bars=max(0, current_bar - record.event_bar))
        for record in annotated
    )


def test_linear_snapshot_matches_original_quadratic_annotation_order() -> None:
    rows = _rows()
    ledger = MarketStructureEventLedger()
    events = (
        _event(1, EVENT_CHOCH, 1, 3, "EXTERNAL"),
        _event(2, EVENT_CHOCH, 1, 5, "EXTERNAL"),
        _event(3, EVENT_BOS, 1, 7, "EXTERNAL"),
        _event(4, EVENT_BOS, 1, 9, "EXTERNAL"),
        _event(5, EVENT_CHOCH, -1, 11, "EXTERNAL"),
        _event(6, EVENT_TRANSITION_FAIL, 1, 13, "EXTERNAL"),
        _event(7, EVENT_CHOCH, -1, 15, "INTERNAL"),
        _event(8, EVENT_BOS, -1, 17, "INTERNAL"),
        _event(9, EVENT_CHOCH, 1, 19, "INTERNAL"),
        _event(10, EVENT_TRANSITION_FAIL, -1, 21, "INTERNAL"),
    )
    ledger.extend(events, rows)

    expected = _quadratic_reference(tuple(ledger._records), current_bar=30)
    actual = ledger.snapshot(current_bar=30)

    assert actual == expected
