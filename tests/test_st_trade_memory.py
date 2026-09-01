from dataclasses import FrozenInstanceError, dataclass, fields
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.engine import DecisionEngineConfig
from financial_dashboard.decision.execution import ExecutionTriggerEvent, ExecutionTriggerState
from financial_dashboard.decision.lifecycle import (
    ExitStage,
    PositionState,
    TradeLifecycleState,
    transition_entry_lifecycle,
    transition_trade_lifecycle,
)
from financial_dashboard.decision.lifecycle_persistence import (
    CANONICAL_LIFECYCLE_CONTRACT_VERSION,
    TRADE_LIFECYCLE_STATE_SCHEMA_VERSION,
    LifecycleCheckpointStatus,
    PersistentTradeLifecycleStore,
    TradeLifecycleCheckpoint,
    decision_config_digest,
    serialize_trade_lifecycle_checkpoint,
)
from financial_dashboard.decision.lifecycle_replay import replay_canonical_trade_lifecycle
from financial_dashboard.decision.persistent_lifecycle_replay import PersistentCanonicalLifecycleReplayRunner
from financial_dashboard.decision.position_metadata import (
    PositionEntryMetadata,
    STInitialDefendedAnchor,
    STTradeMemory,
    build_position_entry_metadata,
)
from financial_dashboard.decision.scenario import ScenarioKind, ScenarioPresence, ScenarioStage
from financial_dashboard.decision.st_thesis_identity import (
    STDefendedAnchorKind,
    STEconomicMission,
    STThesisFamily,
)
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection


def _ref(as_of, *, native_id="sr:1"):
    return FactRef(
        domain=ContextDomain.SUPPORT_RESISTANCE,
        fact_type="RANGE_EXPORT",
        symbol="TEST",
        timeframe="1h",
        native_id=native_id,
        native_state="RANGE_BREAK_CONFIRMED",
        origin_time=as_of,
        confirmed_at=as_of,
        available_at=as_of,
        lineage_id=native_id,
        causal_family=CausalFamily.STRUCTURAL_LEVEL,
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=ContextDataQuality.VALID,
    )


def _sr_projection(
    as_of,
    *,
    state="RANGE_BREAK_CONFIRMED",
    direction=1,
    boundary=100.0,
    role_support=(99.0, 100.0),
    native_id="sr:1",
):
    ref = _ref(as_of, native_id=native_id)
    row = SimpleNamespace(
        timeframe="1h",
        ref=ref,
        state=state,
        range_identity=7,
        break_direction=direction,
        break_candidate_index=10,
        break_boundary=boundary,
        role_reversal_support_low=role_support[0],
        role_reversal_support_high=role_support[1],
    )
    return SimpleNamespace(timeframe_facts=(row,)), ref


def _snapshot(as_of, *, price=104.0, support_resistance=None, source_refs=()):
    return SimpleNamespace(
        symbol="TEST",
        as_of=as_of,
        current_price=price,
        support_resistance=support_resistance,
        order_block_behavior=None,
        fvg_engulfing_lifecycle=None,
        source_refs=source_refs,
    )


def _entry(*, target_identity="target:st:1"):
    scenario = SimpleNamespace(
        horizon=DecisionHorizon.SHORT_TERM,
        presence=ScenarioPresence.PRESENT,
        stage=ScenarioStage.QUALIFIED,
        structural_direction=StructuralDirection.LONG,
        kind=ScenarioKind.SHORT_TERM_STANDALONE,
        active_target_identity=target_identity,
    )
    return SimpleNamespace(
        action=DecisionAction.BUY,
        selected_horizon=DecisionHorizon.SHORT_TERM,
        scenario_stage=ScenarioStage.QUALIFIED,
        execution_state=ExecutionTriggerState.CONFIRMED,
        execution_event_consumed=True,
        arbitration=SimpleNamespace(selected_scenario=scenario),
        reasons=("ENTRY_TEST",),
        blockers=(),
        waiting_for=(),
        source_lineage=("entry:test",),
    )


def _event(as_of):
    return ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=StructuralDirection.LONG,
        timeframe="30m",
        observed_at=as_of,
        available_at=as_of,
        reason="ENTRY_CONFIRMED",
        source_refs=(),
    )


def test_resolved_st_entry_freezes_minimal_breakout_trade_memory():
    as_of = pd.Timestamp("2026-01-05 10:00")
    sr, ref = _sr_projection(as_of)
    metadata = build_position_entry_metadata(
        _snapshot(as_of, support_resistance=sr, source_refs=(ref,)),
        _entry(),
        execution_event=_event(as_of),
    )

    memory = metadata.st_trade_memory
    assert memory is not None
    assert memory.thesis_family is STThesisFamily.BREAKOUT_ACCEPTANCE
    assert memory.economic_mission is STEconomicMission.EXPAND_FROM_ACCEPTED_HIGHER_AREA
    assert memory.initial_defended_anchor is not None
    assert memory.initial_defended_anchor.kind is STDefendedAnchorKind.BREAKOUT_ROLE_SUPPORT
    assert (memory.initial_defended_anchor.low, memory.initial_defended_anchor.high) == (99.0, 100.0)
    assert metadata.entry_price == 104.0
    assert metadata.entry_as_of == as_of
    assert metadata.initial_target_identity == "target:st:1"

    # Persistent memory is deliberately compact: no domain snapshot or FactRef copy.
    assert {item.name for item in fields(STTradeMemory)} == {
        "thesis_family",
        "economic_mission",
        "initial_defended_anchor",
    }
    assert {item.name for item in fields(STInitialDefendedAnchor)} == {
        "kind",
        "identity",
        "timeframe",
        "low",
        "high",
    }
    assert not hasattr(memory.initial_defended_anchor, "source_refs")

    forbidden_policy_flags = {
        "maturity",
        "healthy_base",
        "continuation_failure_count",
        "consumed",
        "CONSUMED",
    }
    assert forbidden_policy_flags.isdisjoint({item.name for item in fields(STTradeMemory)})


def test_ambiguous_or_missing_entry_evidence_persists_unresolved_without_inventing_anchor():
    as_of = pd.Timestamp("2026-01-05 10:00")
    metadata = build_position_entry_metadata(
        _snapshot(as_of, price=101.0),
        _entry(),
        execution_event=_event(as_of),
    )

    memory = metadata.st_trade_memory
    assert memory is not None
    assert memory.thesis_family is STThesisFamily.UNRESOLVED
    assert memory.economic_mission is STEconomicMission.UNRESOLVED
    assert memory.initial_defended_anchor is None
    assert metadata.initial_target_identity == "target:st:1"


def test_entry_anchor_is_immutable_and_later_buy_cannot_rewrite_trade_memory():
    t1 = pd.Timestamp("2026-01-05 10:00")
    sr1, ref1 = _sr_projection(t1)
    first_snapshot = _snapshot(t1, support_resistance=sr1, source_refs=(ref1,))
    opened = transition_entry_lifecycle(
        TradeLifecycleState(),
        _entry(),
        first_snapshot,
        execution_event=_event(t1),
    ).current
    original = opened.entry_metadata
    assert original is not None and original.st_trade_memory is not None

    with pytest.raises(FrozenInstanceError):
        original.st_trade_memory.initial_defended_anchor.low = 1.0

    t2 = pd.Timestamp("2026-01-05 11:00")
    sr2, ref2 = _sr_projection(
        t2,
        state="RANGE_BREAK_FAILED",
        direction=-1,
        boundary=96.0,
        role_support=(None, None),
        native_id="sr:later",
    )
    conflicting = build_position_entry_metadata(
        _snapshot(t2, price=97.0, support_resistance=sr2, source_refs=(ref2,)),
        _entry(target_identity="target:later"),
        execution_event=_event(t2),
    )
    assert conflicting.st_trade_memory.thesis_family is STThesisFamily.FAILED_SELL_RECLAIM

    repeated = transition_trade_lifecycle(
        opened,
        SimpleNamespace(action=DecisionAction.BUY),
        as_of=t2,
        entry_metadata=conflicting,
    )
    assert repeated.action is DecisionAction.HOLD
    assert repeated.current.entry_metadata is original
    assert repeated.current.entry_metadata.st_trade_memory.thesis_family is STThesisFamily.BREAKOUT_ACCEPTANCE
    assert repeated.current.entry_metadata.initial_target_identity == "target:st:1"


def _checkpoint_for_open_state(state):
    return TradeLifecycleCheckpoint(
        symbol="TEST",
        state=state,
        prefix_count=1,
        last_as_of=state.entry_as_of,
        causal_prefix_digest="a" * 64,
        decision_config_digest=decision_config_digest(DecisionEngineConfig()),
    )


def test_v3_checkpoint_requires_st_trade_memory_and_v2_cannot_be_silently_migrated(tmp_path):
    assert TRADE_LIFECYCLE_STATE_SCHEMA_VERSION == 3
    assert CANONICAL_LIFECYCLE_CONTRACT_VERSION == 3

    as_of = pd.Timestamp("2026-01-05 10:00")
    legacy_metadata = PositionEntryMetadata(
        symbol="TEST",
        entry_horizon=DecisionHorizon.SHORT_TERM,
        scenario_kind=ScenarioKind.SHORT_TERM_STANDALONE,
        entry_as_of=as_of,
        entry_price=100.0,
        active_target_identity="target:legacy",
        execution_timeframe="30m",
        execution_observed_at=as_of,
        execution_available_at=as_of,
        execution_reason="CONFIRMED",
        source_lineage=(),
    )
    legacy_state = TradeLifecycleState(
        position=PositionState.OPEN,
        exit_stage=ExitStage.MONITOR,
        trade_id="trade:legacy",
        entry_as_of=as_of,
        entry_metadata=legacy_metadata,
    )
    with pytest.raises(ValueError, match="requires ST trade memory"):
        serialize_trade_lifecycle_checkpoint(_checkpoint_for_open_state(legacy_state))

    sr, ref = _sr_projection(as_of)
    canonical_state = transition_entry_lifecycle(
        TradeLifecycleState(),
        _entry(),
        _snapshot(as_of, support_resistance=sr, source_refs=(ref,)),
        execution_event=_event(as_of),
    ).current
    payload = serialize_trade_lifecycle_checkpoint(_checkpoint_for_open_state(canonical_state))
    payload["schema_version"] = 2
    payload["contract_version"] = 2

    store = PersistentTradeLifecycleStore(tmp_path)
    store.path_for("TEST").write_text(json.dumps(payload), encoding="utf-8")
    loaded = store.load("TEST")
    assert loaded.status is LifecycleCheckpointStatus.INVALID
    assert loaded.checkpoint is None


@dataclass(frozen=True)
class _ReplaySnapshot:
    symbol: str
    as_of: pd.Timestamp
    current_price: float
    support_resistance: object | None
    source_refs: tuple
    entry_action: DecisionAction = DecisionAction.WAIT
    order_block_behavior: object | None = None
    fvg_engulfing_lifecycle: object | None = None

    def entry_decision(self, *, config=None, execution_event=None):
        if self.entry_action is DecisionAction.BUY and execution_event is not None:
            return _entry()
        return SimpleNamespace(
            action=DecisionAction.WAIT,
            selected_horizon=None,
            scenario_stage=None,
            execution_state=ExecutionTriggerState.ABSENT,
            execution_event_consumed=False,
            arbitration=SimpleNamespace(selected_scenario=None),
            reasons=("WAIT",),
            blockers=(),
            waiting_for=("ENTRY",),
            source_lineage=(),
        )

    def position_exit_decision(self, state, *, execution_event=None):
        return SimpleNamespace(
            action=DecisionAction.HOLD,
            entry_horizon=state.entry_metadata.entry_horizon,
            as_of=self.as_of,
            stage=ExitStage.MONITOR,
            execution_event_consumed=False,
            reasons=("HOLD",),
            blockers=(),
            waiting_for=(),
            source_lineage=(),
        )


def test_cold_warm_and_restart_replay_preserve_same_st_trade_memory(tmp_path):
    t1 = pd.Timestamp("2026-01-05 10:00")
    t2 = pd.Timestamp("2026-01-05 10:30")
    t3 = pd.Timestamp("2026-01-05 11:00")
    sr1, ref1 = _sr_projection(t1)
    snapshots = (
        _ReplaySnapshot("TEST", t1, 104.0, sr1, (ref1,), entry_action=DecisionAction.BUY),
        _ReplaySnapshot("TEST", t2, 105.0, None, ()),
        _ReplaySnapshot("TEST", t3, 106.0, None, ()),
    )
    events = {t1: _event(t1)}

    cold = replay_canonical_trade_lifecycle(snapshots, entry_execution_events=events)
    cold_memory = cold.final_state.entry_metadata.st_trade_memory
    assert cold_memory is not None
    assert cold_memory.thesis_family is STThesisFamily.BREAKOUT_ACCEPTANCE

    runner = PersistentCanonicalLifecycleReplayRunner(tmp_path)
    prefix = runner.run("TEST", snapshots[:2], entry_execution_events=events)
    assert prefix.checkpoint.state.entry_metadata.st_trade_memory == cold_memory

    resumed = runner.run("TEST", snapshots, entry_execution_events=events)
    assert resumed.resumed is True
    assert resumed.processed_count == 1
    assert resumed.replay.initial_state.entry_metadata.st_trade_memory == cold_memory
    assert resumed.replay.final_state.entry_metadata.st_trade_memory == cold_memory

    reconstructed_actions = tuple(
        row.action for row in (*prefix.replay.rows, *resumed.replay.rows)
    )
    assert reconstructed_actions == tuple(row.action for row in cold.rows)
    assert resumed.replay.final_state == cold.final_state
