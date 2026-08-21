from __future__ import annotations

from dataclasses import replace

import pytest

from financial_dashboard.engines.fvg_engulfing_final import FvgEngulfingExport, FvgSideExport
from financial_dashboard.engines.liquidity_engine import LiquidityExport
from financial_dashboard.engines.raw_indicator_dashboard_decision import HamDashboardExport
from financial_dashboard.engines.volatility_bands_fib_final import VolatilityBandsFibFinalExport
from financial_dashboard.technical_evidence import (
    EvidenceContext,
    EvidenceDirection,
    TechnicalEvidenceBuilder,
    TechnicalEvidencePacket,
    adapt_fvg_engulfing,
    adapt_ham,
    adapt_liquidity,
    adapt_volatility,
)
from financial_dashboard.technical_evidence.models import NormalizedLevel, make_level_id


def _ctx(**changes):
    values = {
        "timeframe": "2h",
        "known_bar": 120,
        "timestamp": "2026-08-20T18:00:00+03:00",
        "source_data_quality": "OK",
        "is_closed": True,
        "is_complete": True,
    }
    values.update(changes)
    return EvidenceContext(**values)


def test_source_gap_blocks_builder_even_if_flags_are_true():
    builder = TechnicalEvidenceBuilder()
    good = adapt_ham(HamDashboardExport(2, 60.0, 1, 25.0), _ctx())
    assert builder.update(_ctx(), good) == good

    gap_context = _ctx(
        known_bar=121,
        timestamp="2026-08-20T20:00:00+03:00",
        source_data_quality="SOURCE_GAP",
    )
    gap_packet = adapt_ham(HamDashboardExport(-2, -90.0, -2, -80.0), gap_context)
    assert builder.update(gap_context, gap_packet) == good
    assert builder.snapshot() == good


def test_invalid_and_unsupported_contexts_do_not_advance():
    for status in ("DATA_INVALID", "UNSUPPORTED_TIMEFRAME", "INCOMPLETE_BAR"):
        builder = TechnicalEvidenceBuilder()
        context = _ctx(source_data_quality=status)
        packet = adapt_ham(HamDashboardExport(2, 60.0, 1, 25.0), context)
        assert builder.update(context, packet) is None


def test_level_rejects_origin_that_is_known_only_in_future():
    context = _ctx()
    level_id = make_level_id(
        source_engine="test",
        level_type="PIVOT",
        timeframe=context.timeframe,
        source_bar=121,
        known_bar=120,
        price=100.0,
        lower=None,
        upper=None,
    )
    with pytest.raises(ValueError, match="source_bar cannot be after known_bar"):
        NormalizedLevel(
            id=level_id,
            source_engine="test",
            level_type="PIVOT",
            timeframe=context.timeframe,
            price=100.0,
            source_bar=121,
            known_bar=120,
        )


def test_packet_rejects_future_known_evidence():
    packet = adapt_ham(HamDashboardExport(2, 60.0, 1, 25.0), _ctx())
    future = replace(packet.evidence[0], known_bar=121)
    with pytest.raises(ValueError, match="known in the future"):
        TechnicalEvidencePacket(
            timeframe=packet.timeframe,
            known_bar=120,
            timestamp=packet.timestamp,
            evidence=(future,),
            levels=(),
        )


def test_quality_is_not_duplicated_into_strength_when_source_has_no_strength_port():
    liquidity = adapt_liquidity(
        LiquidityExport(nearest_bsl=108.0, latest_event_state="RECLAIM", latest_event_direction=1, quality=76.0),
        _ctx(),
    ).evidence[0]
    assert liquidity.quality == 76.0
    assert liquidity.strength is None

    fvg = adapt_fvg_engulfing(
        FvgEngulfingExport(bull_fvg=FvgSideExport(state=3, top=104.0, bottom=103.0, quality=77.0, fill=0.2)),
        _ctx(),
    ).evidence[0]
    assert fvg.quality == 77.0
    assert fvg.strength is None


def test_volatility_only_direction_port_receives_direction_metadata():
    packet = adapt_volatility(
        VolatilityBandsFibFinalExport(
            regime=3,
            direction=1.0,
            quality=68.0,
            band_state=4,
            band_agreement=1,
            fib_state=5,
            data_quality="OK",
        ),
        _ctx(),
    )
    by_type = {item.evidence_type: item for item in packet.evidence}

    assert by_type["VOLATILITY_DIRECTION"].direction is EvidenceDirection.BULL
    for name in ("VOLATILITY_REGIME", "BAND_STATE", "BAND_AGREEMENT", "FIB_STATE"):
        assert by_type[name].direction is EvidenceDirection.NEUTRAL
        assert by_type[name].strength is None
