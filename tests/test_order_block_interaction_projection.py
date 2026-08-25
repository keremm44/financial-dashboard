from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDomain
from financial_dashboard.context.order_block_behavior_projection import (
    project_order_block_behavior,
)
from financial_dashboard.engines.order_block_behavior import (
    OrderBlockBehaviorSnapshot,
    OrderBlockBehaviorState,
    OrderBlockInteractionState,
)


NOW = datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc)
AVAILABLE = NOW + timedelta(hours=1)


def _behavior() -> OrderBlockBehaviorSnapshot:
    return OrderBlockBehaviorSnapshot(
        identity="OB:12:1",
        bullish=True,
        top=110.0,
        bottom=100.0,
        state=OrderBlockBehaviorState.PARTIALLY_MITIGATED,
        interaction=OrderBlockInteractionState.REACTION_CONFIRMED,
        active=True,
        age_bars=18,
        bars_since_confirmation=14,
        mitigation_count=1,
        visit_count=1,
        deepest_fill_ratio=0.35,
        distance_atr=1.4,
        total_inside_bars=5,
        inside_close_bars=4,
        current_visit_bars=5,
        close_inside=False,
        range_intersects=False,
        first_entry_index=20,
        last_entry_index=20,
        favorable_exit_index=25,
        bars_held_favorable=3,
        max_favorable_move_atr=0.85,
        terminal_reason=None,
    )


def _replay():
    snapshot = SimpleNamespace(as_of=NOW, available_at=AVAILABLE)
    return SimpleNamespace(
        symbol="ASELS",
        timeframes=("1h",),
        snapshots={"1h": snapshot},
        order_block_behavior={"1h": (_behavior(),)},
    )


def test_projection_keeps_dwell_visit_and_favorable_acceptance_facts() -> None:
    projection = project_order_block_behavior(
        _replay(),
        data_quality_by_timeframe={"1h": "DATA_OK"},
    )
    assert projection is not None
    rows = projection.for_timeframe("1h")
    assert len(rows) == 1
    item = rows[0]

    assert item.ref.domain is ContextDomain.ORDER_BLOCK
    assert item.ref.fact_type == "ORDER_BLOCK_BEHAVIOR"
    assert item.ref.native_state == "PARTIALLY_MITIGATED:REACTION_CONFIRMED"
    assert item.ref.available_at == AVAILABLE
    assert item.ref.lineage_id == "OB:1h:12:1"
    assert item.interaction == "REACTION_CONFIRMED"
    assert item.visit_count == 1
    assert item.mitigation_count == 1
    assert item.total_inside_bars == 5
    assert item.inside_close_bars == 4
    assert item.bars_held_favorable == 3
    assert item.max_favorable_move_atr == 0.85


def test_projection_respects_knowledge_boundary() -> None:
    projection = project_order_block_behavior(
        _replay(),
        data_quality_by_timeframe={"1h": "DATA_OK"},
    )
    assert projection is not None

    assert projection.available_at(NOW).observations == ()
    assert len(projection.available_at(AVAILABLE).observations) == 1
