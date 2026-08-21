import pytest

from financial_dashboard.engines.fvg_engulfing_final import FvgEngulfingExport, FvgSideExport
from financial_dashboard.engines.liquidity_engine import LiquidityExport
from financial_dashboard.engines.mtf_story_models import ContextState, MTFStoryResult, MTFStoryState, TriggerState
from financial_dashboard.engines.models import Direction
from financial_dashboard.technical_evidence import (
    EvidenceContext,
    TechnicalEvidencePacket,
    adapt_fvg_engulfing,
    adapt_liquidity,
    adapt_mtf_story,
    build_technical_evidence_bundle,
    merge_packets,
)


def _ctx() -> EvidenceContext:
    return EvidenceContext(
        timeframe="2h",
        known_bar=120,
        timestamp="2026-08-20T18:00:00+03:00",
        source_data_quality="OK",
    )


def test_liquidity_sweep_reclaim_event_is_anchored_to_current_bar():
    packet = adapt_liquidity(
        LiquidityExport(
            latest_event_side="SSL",
            latest_event_state="RECLAIM",
            latest_event_level=100.0,
            latest_event_identity="SSL:42",
            latest_event_direction=1,
            quality=76.0,
        ),
        _ctx(),
    )
    event = next(item for item in packet.evidence if item.evidence_type == "LIQUIDITY_EVENT")
    level = next(level for level in packet.levels if level.level_type == "LATEST_EVENT_LEVEL")

    assert event.source_bar == 120
    assert level.source_bar == 120
    assert level.raw_metadata["event_bar"] == 120

    bundle = build_technical_evidence_bundle((packet,))
    enriched = bundle.evidence_by_id(event.id)
    assert enriched is not None
    assert enriched.freshness == pytest.approx(1.0)


def test_fvg_terminal_event_is_anchored_but_active_zone_origin_is_not_invented():
    packet = adapt_fvg_engulfing(
        FvgEngulfingExport(
            bull_fvg=FvgSideExport(
                state=3,
                top=104.0,
                bottom=103.0,
                quality=77.0,
                fill=0.30,
                event=4,
            )
        ),
        _ctx(),
    )
    location = next(item for item in packet.evidence if item.evidence_type == "BULL_FVG")
    event = next(item for item in packet.evidence if item.evidence_type == "BULL_FVG_EVENT")

    assert location.source_bar is None
    assert event.source_bar == 120

    bundle = build_technical_evidence_bundle((packet,))
    enriched_event = bundle.evidence_by_id(event.id)
    enriched_location = bundle.evidence_by_id(location.id)
    assert enriched_event is not None and enriched_event.freshness == pytest.approx(1.0)
    assert enriched_location is not None and enriched_location.freshness is None


def test_source_timestamp_missing_is_not_treated_as_matching_current_snapshot():
    result = MTFStoryResult(
        state=MTFStoryState.RANGE_MIXED,
        timestamp=None,
        dominant_direction=Direction.NEUTRAL,
        macro_direction=Direction.NEUTRAL,
        context_state=ContextState.MIXED_CONTEXT,
        trigger_state=TriggerState.NO_TRIGGER,
        quality=50.0,
        confidence=0.5,
    )
    with pytest.raises(ValueError, match="timestamp"):
        adapt_mtf_story(result, _ctx())


def test_merge_rejects_packet_with_missing_timestamp_against_timestamped_context():
    packet = TechnicalEvidencePacket(timeframe="2h", known_bar=120, timestamp=None)
    with pytest.raises(ValueError, match="timestamp"):
        merge_packets(_ctx(), (packet,))
