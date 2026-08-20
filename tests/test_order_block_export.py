from __future__ import annotations

import pandas as pd
import pytest

from financial_dashboard.engines import (
    OrderBlockDataQuality,
    OrderBlockEngine,
    OrderBlockExport,
    OrderBlockSideExport,
)
from financial_dashboard.engines.order_block_engine import OrderBlockRecord

TZ = "Europe/Istanbul"


def _record(
    *,
    source: int,
    bullish: bool,
    top: float,
    bottom: float,
    boundary: float,
    confirmed: bool = True,
) -> OrderBlockRecord:
    return OrderBlockRecord(
        source_index=source,
        source_time=pd.Timestamp("2026-01-02 10:00", tz=TZ) + pd.Timedelta(minutes=5 * source),
        top=top,
        bottom=bottom,
        bullish=bullish,
        base_score=2,
        has_imbalance=confirmed,
        anchor_high=top,
        anchor_low=bottom,
        imbalance_end_index=source + 4,
        fill_boundary=boundary,
    )


def _bar(*, closed: bool = True, complete: bool = True) -> dict:
    return {
        "timestamp": pd.Timestamp("2026-01-02 12:00", tz=TZ),
        "open": 105.0,
        "high": 106.0,
        "low": 104.0,
        "close": 105.0,
        "volume": 1000.0,
        "is_closed": closed,
        "is_complete": complete,
    }


def test_export_uses_active_remaining_zone_and_keeps_sides_independent() -> None:
    engine = OrderBlockEngine()
    engine._records = [
        _record(source=1, bullish=True, top=110.0, bottom=100.0, boundary=106.0),
        _record(source=2, bullish=False, top=120.0, bottom=110.0, boundary=114.0),
    ]

    export = engine._select_export(close=108.0)

    assert export.bull == OrderBlockSideExport(
        state=1.0, top=106.0, bottom=100.0, fill=pytest.approx(0.40), source_bar=1.0
    )
    assert export.bear == OrderBlockSideExport(
        state=-1.0, top=120.0, bottom=114.0, fill=pytest.approx(0.40), source_bar=2.0
    )


def test_export_excludes_unconfirmed_candidate() -> None:
    engine = OrderBlockEngine()
    engine._records = [
        _record(source=1, bullish=True, top=110.0, bottom=100.0, boundary=110.0, confirmed=False)
    ]

    assert engine._select_export(close=105.0) == OrderBlockExport()


def test_nearest_active_zone_wins_and_visual_distance_has_no_role() -> None:
    engine = OrderBlockEngine()
    engine._records = [
        _record(source=1, bullish=True, top=90.0, bottom=80.0, boundary=90.0),
        _record(source=2, bullish=True, top=102.0, bottom=98.0, boundary=102.0),
    ]

    export = engine._select_export(close=105.0)

    assert export.bull.source_bar == 2.0
    assert export.bull.top == 102.0
    assert export.bull.bottom == 98.0


def test_equal_distance_within_minimum_tick_prefers_newer_source() -> None:
    engine = OrderBlockEngine()
    engine._records = [
        _record(source=3, bullish=True, top=110.0, bottom=100.0, boundary=110.0),
        _record(source=9, bullish=True, top=110.0, bottom=100.0, boundary=110.0),
    ]

    export = engine._select_export(close=115.0)

    assert export.bull.source_bar == 9.0


def test_open_bar_freezes_export_but_sets_audit_status() -> None:
    engine = OrderBlockEngine()
    frozen = OrderBlockExport(
        bull=OrderBlockSideExport(state=1.0, top=106.0, bottom=100.0, fill=0.4, source_bar=1.0)
    )
    engine._export = frozen

    engine.update(_bar(closed=False))

    assert engine.export == frozen
    assert engine.last_data_quality == OrderBlockDataQuality.INCOMPLETE_BAR


def test_source_gap_freezes_export_but_sets_audit_status() -> None:
    engine = OrderBlockEngine()
    frozen = OrderBlockExport(
        bear=OrderBlockSideExport(state=-1.0, top=120.0, bottom=114.0, fill=0.4, source_bar=2.0)
    )
    engine._export = frozen

    engine.update(_bar(complete=False))

    assert engine.export == frozen
    assert engine.last_data_quality == OrderBlockDataQuality.SOURCE_GAP


def test_closed_complete_bar_refreshes_empty_export_and_quality() -> None:
    engine = OrderBlockEngine()
    engine._export = OrderBlockExport(
        bull=OrderBlockSideExport(state=1.0, top=106.0, bottom=100.0, fill=0.4, source_bar=1.0)
    )

    engine.update(_bar())

    assert engine.export == OrderBlockExport()
    assert engine.last_data_quality == OrderBlockDataQuality.OK


def test_public_engine_points_to_final_order_block_facade() -> None:
    assert OrderBlockEngine.__module__ == "financial_dashboard.engines.order_block"
