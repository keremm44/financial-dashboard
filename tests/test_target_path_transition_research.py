from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from financial_dashboard.decision_audit.models import DecisionAction, DecisionEvent
from financial_dashboard.decision_audit.research import LargeMarketMove
from financial_dashboard.decision_audit.target_transition_research import (
    TargetPathTransitionAuditConfig,
    audit_target_path_transitions,
    render_target_path_transition_text,
)


@dataclass(frozen=True)
class _Node:
    identity: str
    low: float
    high: float
    state: str = "ACTIVE"
    native_disposition: str = "PENDING"
    roles: tuple[str, ...] = ("BARRIER",)
    sources: tuple[str, ...] = ("SUPPORT_RESISTANCE",)
    native_states: tuple[str, ...] = ("ACTIVE",)


@dataclass(frozen=True)
class _Path:
    nodes: tuple[_Node, ...]
    status: str = "READY"

    @property
    def active_node(self):
        for node in self.nodes:
            if node.state in {"ACTIVE", "DEFENDED"}:
                return node
        return None


@dataclass(frozen=True)
class _Snapshot:
    as_of: pd.Timestamp
    current_price: float
    path: _Path

    def target_path(self, direction):
        return self.path


def _move(direction: str = "UP") -> LargeMarketMove:
    return LargeMarketMove(
        direction=direction,
        classification="MAJOR",
        start_time=pd.Timestamp("2026-01-01 10:00"),
        end_time=pd.Timestamp("2026-01-01 16:00"),
        start_price=100.0 if direction == "UP" else 120.0,
        end_price=120.0 if direction == "UP" else 100.0,
        move_pct=20.0 if direction == "UP" else -16.6667,
        duration_hours=6.0,
        four_hour_bars=2,
        trading_days=1,
        move_pct_per_4h_bar=20.0,
        move_pct_per_trading_day=20.0,
    )


def _event(timestamp: str, action: DecisionAction, price: float, waiting=(), reasons=()) -> DecisionEvent:
    return DecisionEvent(
        timestamp=pd.Timestamp(timestamp),
        action=action,
        price=price,
        waiting_for=tuple(waiting),
        reasons=tuple(reasons),
    )


def test_target_transition_tracks_cross_persistence_retest_advance_and_buy():
    first = _Node("R1", 104.0, 105.0)
    next_node = _Node("R2", 114.0, 115.0, roles=("OBJECTIVE",), sources=("LIQUIDITY",))
    snapshots = (
        _Snapshot(pd.Timestamp("2026-01-01 10:00"), 103.0, _Path((first,))),
        _Snapshot(pd.Timestamp("2026-01-01 11:00"), 106.0, _Path((next_node,))),
        _Snapshot(pd.Timestamp("2026-01-01 12:00"), 107.0, _Path((next_node,))),
        _Snapshot(pd.Timestamp("2026-01-01 13:00"), 108.0, _Path((next_node,))),
        _Snapshot(pd.Timestamp("2026-01-01 14:00"), 110.0, _Path((next_node,))),
    )
    decisions = (
        _event("2026-01-01 10:00", DecisionAction.WAIT, 103.0, waiting=("TARGET_PATH_TO_RESOLVE",)),
        _event("2026-01-01 11:00", DecisionAction.WAIT, 106.0, waiting=("SETUP_TRIGGER",)),
        _event("2026-01-01 12:00", DecisionAction.WAIT, 107.0, waiting=("SETUP_TRIGGER_CONFIRMATION",)),
        _event("2026-01-01 14:00", DecisionAction.BUY, 110.0),
    )
    micro = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2026-01-01 10:30",
                    "2026-01-01 11:30",
                    "2026-01-01 12:30",
                    "2026-01-01 13:00",
                ]
            ),
            "low": [103.0, 105.8, 104.8, 106.0],
            "close": [104.0, 106.2, 105.4, 107.0],
        }
    )

    report = audit_target_path_transitions(
        symbol="TEST",
        moves=(_move(),),
        snapshots=snapshots,
        decisions=decisions,
        micro_bars=micro,
        config=TargetPathTransitionAuditConfig(persistence_snapshots=2, retest_tolerance_pct=0.5),
    )

    assert len(report.moves) == 1
    episode = report.moves[0].episodes[0]
    assert episode.identity == "R1"
    assert episode.price_cross_at == pd.Timestamp("2026-01-01 11:00")
    assert episode.persistence_at == pd.Timestamp("2026-01-01 12:00")
    assert episode.path_advanced_at == pd.Timestamp("2026-01-01 11:00")
    assert episode.next_active_identity == "R2"
    assert episode.retest_held_at == pd.Timestamp("2026-01-01 12:30")
    assert episode.buy_after_transition_at == pd.Timestamp("2026-01-01 14:00")
    assert episode.transition_to_buy_hours == 2.0
    assert episode.dominant_waiting_after_transition[0][0] in {
        "SETUP_TRIGGER",
        "SETUP_TRIGGER_CONFIRMATION",
    }
    assert episode.diagnostic_label == "BUY_AFTER_TARGET_TRANSITION"

    text = render_target_path_transition_text(report)
    assert "price_cross=2026-01-01 11:00:00" in text
    assert "persistence=2026-01-01 12:00:00" in text
    assert "retest_held=2026-01-01 12:30:00" in text
    assert "BUY=2026-01-01 14:00:00" in text


def test_target_transition_keeps_native_clear_separate_from_price_cross():
    active = _Node("R1", 104.0, 105.0)
    cleared = _Node("R1", 104.0, 105.0, state="CLEARED", native_disposition="CLEARED")
    next_node = _Node("R2", 114.0, 115.0)
    snapshots = (
        _Snapshot(pd.Timestamp("2026-01-01 10:00"), 103.0, _Path((active,))),
        _Snapshot(pd.Timestamp("2026-01-01 11:00"), 104.5, _Path((cleared, next_node))),
        _Snapshot(pd.Timestamp("2026-01-01 12:00"), 106.0, _Path((cleared, next_node))),
        _Snapshot(pd.Timestamp("2026-01-01 13:00"), 107.0, _Path((next_node,))),
    )
    micro = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-01-01 12:30"]),
            "low": [104.9],
            "close": [105.5],
        }
    )

    report = audit_target_path_transitions(
        symbol="TEST",
        moves=(_move(),),
        snapshots=snapshots,
        decisions=(),
        micro_bars=micro,
        config=TargetPathTransitionAuditConfig(persistence_snapshots=2),
    )
    episode = report.moves[0].episodes[0]
    assert episode.native_clear_at == pd.Timestamp("2026-01-01 11:00")
    assert episode.price_cross_at == pd.Timestamp("2026-01-01 12:00")
    assert episode.persistence_at == pd.Timestamp("2026-01-01 13:00")


def test_down_moves_are_not_treated_as_long_entry_target_transition_opportunities():
    snapshot = _Snapshot(
        pd.Timestamp("2026-01-01 10:00"),
        110.0,
        _Path((_Node("S1", 114.0, 115.0),)),
    )
    micro = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-01-01 10:00"]),
            "low": [109.0],
            "close": [110.0],
        }
    )
    report = audit_target_path_transitions(
        symbol="TEST",
        moves=(_move("DOWN"),),
        snapshots=(snapshot,),
        decisions=(),
        micro_bars=micro,
    )
    assert report.moves == ()
