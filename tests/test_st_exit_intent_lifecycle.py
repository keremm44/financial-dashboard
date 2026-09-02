import json
from types import SimpleNamespace

import pandas as pd
import pytest

from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.engine import DecisionEngineConfig
from financial_dashboard.decision.lifecycle import (
    ExitStage,
    PositionState,
    TradeLifecycleState,
    transition_st_exit_intent,
    transition_trade_lifecycle,
)
from financial_dashboard.decision.lifecycle_persistence import (
    CANONICAL_LIFECYCLE_CONTRACT_VERSION,
    TRADE_LIFECYCLE_STATE_SCHEMA_VERSION,
    LifecycleCheckpointStatus,
    PersistentTradeLifecycleStore,
    TradeLifecycleCheckpoint,
    decision_config_digest,
    deserialize_trade_lifecycle_state,
    serialize_trade_lifecycle_checkpoint,
    serialize_trade_lifecycle_state,
)
from financial_dashboard.decision.position_metadata import PositionEntryMetadata, STTradeMemory
from financial_dashboard.decision.scenario import ScenarioKind
from financial_dashboard.decision.st_economic_history import STEconomicHistory
from financial_dashboard.decision.st_exit_intent import STExitFamily
from financial_dashboard.decision.st_thesis_identity import STEconomicMission, STThesisFamily
from financial_dashboard.decision.structural import DecisionHorizon


ENTRY = pd.Timestamp("2026-01-05 10:00")
T1 = pd.Timestamp("2026-01-05 12:00")
T2 = pd.Timestamp("2026-01-05 13:00")


def _metadata():
    return PositionEntryMetadata(
        symbol="TEST",
        entry_horizon=DecisionHorizon.SHORT_TERM,
        scenario_kind=ScenarioKind.SHORT_TERM_STANDALONE,
        entry_as_of=ENTRY,
        entry_price=101.0,
        active_target_identity=None,
        execution_timeframe="30m",
        execution_observed_at=ENTRY,
        execution_available_at=ENTRY,
        execution_reason="ENTRY_CONFIRMED",
        source_lineage=(),
        st_trade_memory=STTradeMemory(
            thesis_family=STThesisFamily.UNRESOLVED,
            economic_mission=STEconomicMission.UNRESOLVED,
            initial_defended_anchor=None,
            initial_target_context=None,
        ),
    )


def _open_state(*, stage=ExitStage.MONITOR):
    return TradeLifecycleState(
        position=PositionState.OPEN,
        exit_stage=stage,
        trade_id="trade:step7",
        entry_as_of=ENTRY,
        entry_metadata=_metadata(),
        st_economic_history=STEconomicHistory(),
    )


def _decision(action):
    return SimpleNamespace(action=action)


def _checkpoint(state, *, last_as_of=T1):
    return TradeLifecycleCheckpoint(
        symbol="TEST",
        state=state,
        prefix_count=2,
        last_as_of=last_as_of,
        causal_prefix_digest="a" * 64,
        decision_config_digest=decision_config_digest(DecisionEngineConfig()),
    )


def test_predecision_consumed_is_not_persisted_as_lifecycle_state():
    state = _open_state()

    assert not hasattr(state, "consumed")
    assert not hasattr(state, "healthy_base")
    assert not hasattr(state, "mature")
    assert state.st_exit_intent is None
    assert serialize_trade_lifecycle_state(state)["st_exit_intent"] is None


def test_final_harvest_intent_survives_later_hold_or_repeated_harvest_evaluation():
    state = transition_st_exit_intent(
        _open_state(),
        STExitFamily.PROFIT_HARVEST,
        as_of=T1,
        reasons=("ST_CONSUMED_POLICY_COMMITTED",),
        source_lineage=("z", "a"),
    )
    original = state.st_exit_intent

    held = transition_st_exit_intent(state, None)
    repeated = transition_st_exit_intent(
        held,
        STExitFamily.PROFIT_HARVEST,
        as_of=T2,
        reasons=("LATER_HARVEST_REEVALUATION",),
    )

    assert held.st_exit_intent == original
    assert repeated.st_exit_intent == original
    assert original.family is STExitFamily.PROFIT_HARVEST
    assert original.committed_at == T1
    assert original.reasons == ("ST_CONSUMED_POLICY_COMMITTED",)
    assert original.source_lineage == ("a", "z")


def test_harvest_can_escalate_to_protective_but_protective_cannot_downgrade():
    harvested = transition_st_exit_intent(
        _open_state(),
        STExitFamily.PROFIT_HARVEST,
        as_of=T1,
        reasons=("ST_CONSUMED_POLICY_COMMITTED",),
    )
    protective = transition_st_exit_intent(
        harvested,
        STExitFamily.PROTECTIVE_EXIT,
        as_of=T2,
        reasons=("ST_THESIS_INVALIDATED_AFTER_HARVEST",),
        source_lineage=("protective:1",),
    )
    attempted_downgrade = transition_st_exit_intent(
        protective,
        STExitFamily.PROFIT_HARVEST,
        as_of=T2 + pd.Timedelta(hours=1),
        reasons=("LATER_HARVEST_SIGNAL",),
    )

    assert protective.st_exit_intent.family is STExitFamily.PROTECTIVE_EXIT
    assert protective.st_exit_intent.committed_at == T2
    assert protective.st_exit_intent.reasons == ("ST_THESIS_INVALIDATED_AFTER_HARVEST",)
    assert attempted_downgrade.st_exit_intent == protective.st_exit_intent


def test_terminal_intent_cannot_predate_entry_or_move_backward_in_time():
    with pytest.raises(ValueError, match="cannot predate trade entry"):
        transition_st_exit_intent(
            _open_state(),
            STExitFamily.PROFIT_HARVEST,
            as_of=ENTRY - pd.Timedelta(minutes=1),
            reasons=("INVALID_PRE_ENTRY_INTENT",),
        )

    harvested = transition_st_exit_intent(
        _open_state(),
        STExitFamily.PROFIT_HARVEST,
        as_of=T2,
        reasons=("ST_CONSUMED_POLICY_COMMITTED",),
    )
    with pytest.raises(ValueError, match="cannot move backward in time"):
        transition_st_exit_intent(
            harvested,
            STExitFamily.PROTECTIVE_EXIT,
            as_of=T1,
            reasons=("STALE_INVALIDATION",),
        )


def test_terminal_intent_does_not_change_exit_stage_or_execution_timing():
    committed = transition_st_exit_intent(
        _open_state(stage=ExitStage.MONITOR),
        STExitFamily.PROTECTIVE_EXIT,
        as_of=T1,
        reasons=("ST_PROTECTIVE_POLICY_COMMITTED",),
    )

    held = transition_trade_lifecycle(
        committed,
        _decision(DecisionAction.HOLD),
        as_of=T1,
        exit_stage=ExitStage.MONITOR,
        exit_execution_confirmed=False,
    )
    assert held.action is DecisionAction.HOLD
    assert held.current.position is PositionState.OPEN
    assert held.current.exit_stage is ExitStage.MONITOR
    assert held.current.st_exit_intent == committed.st_exit_intent

    with pytest.raises(ValueError, match="requires EXIT_READY"):
        transition_trade_lifecycle(
            committed,
            _decision(DecisionAction.SELL),
            as_of=T1,
            exit_stage=ExitStage.MONITOR,
            exit_execution_confirmed=True,
        )


def test_confirmed_sell_copies_terminal_reason_to_closed_record_without_pnl_inference():
    ready = transition_st_exit_intent(
        _open_state(stage=ExitStage.EXIT_READY),
        STExitFamily.PROFIT_HARVEST,
        as_of=T1,
        reasons=("ST_CONSUMED_POLICY_COMMITTED", "MISSION_COMPLETE_NO_NEW_EXPANSION"),
        source_lineage=("history:mission", "history:continuation"),
    )

    transition = transition_trade_lifecycle(
        ready,
        _decision(DecisionAction.SELL),
        as_of=T2,
        exit_stage=ExitStage.EXIT_READY,
        exit_execution_confirmed=True,
    )

    assert transition.action is DecisionAction.SELL
    assert transition.current.position is PositionState.FLAT
    assert transition.current.st_exit_intent is None
    closed = transition.current.last_closed_st_exit
    assert closed is not None
    assert closed.trade_id == "trade:step7"
    assert closed.entry_as_of == ENTRY
    assert closed.exit_as_of == T2
    assert closed.family is STExitFamily.PROFIT_HARVEST
    assert closed.intent_committed_at == T1
    assert closed.reasons == (
        "ST_CONSUMED_POLICY_COMMITTED",
        "MISSION_COMPLETE_NO_NEW_EXPANSION",
    )
    assert not hasattr(closed, "pnl")


def test_confirmed_sell_cannot_time_travel_before_terminal_intent():
    ready = transition_st_exit_intent(
        _open_state(stage=ExitStage.EXIT_READY),
        STExitFamily.PROTECTIVE_EXIT,
        as_of=T2,
        reasons=("ST_THESIS_INVALIDATED",),
    )

    with pytest.raises(ValueError, match="execution cannot predate terminal intent"):
        transition_trade_lifecycle(
            ready,
            _decision(DecisionAction.SELL),
            as_of=T1,
            exit_stage=ExitStage.EXIT_READY,
            exit_execution_confirmed=True,
        )


def test_open_intent_and_closed_reason_round_trip_restart_exactly(tmp_path):
    open_state = transition_st_exit_intent(
        _open_state(stage=ExitStage.EXIT_READY),
        STExitFamily.PROTECTIVE_EXIT,
        as_of=T1,
        reasons=("ST_BREAKOUT_ACCEPTANCE_INVALIDATED",),
        source_lineage=("structure:1", "reaction:1"),
    )
    store = PersistentTradeLifecycleStore(tmp_path)
    store.save(_checkpoint(open_state))
    loaded_open = store.load("TEST")

    assert loaded_open.status is LifecycleCheckpointStatus.LOADED
    assert loaded_open.checkpoint.state == open_state

    closed_state = transition_trade_lifecycle(
        loaded_open.checkpoint.state,
        _decision(DecisionAction.SELL),
        as_of=T2,
        exit_stage=ExitStage.EXIT_READY,
        exit_execution_confirmed=True,
    ).current
    store.save(_checkpoint(closed_state, last_as_of=T2))
    loaded_closed = store.load("TEST")

    assert loaded_closed.status is LifecycleCheckpointStatus.LOADED
    assert loaded_closed.checkpoint.state == closed_state
    assert loaded_closed.checkpoint.state.last_closed_st_exit.family is STExitFamily.PROTECTIVE_EXIT
    assert loaded_closed.checkpoint.state.last_closed_st_exit.reasons == (
        "ST_BREAKOUT_ACCEPTANCE_INVALIDATED",
    )


def test_schema_and_contract_boundary_are_explicit_and_old_checkpoint_fails_closed(tmp_path):
    assert TRADE_LIFECYCLE_STATE_SCHEMA_VERSION == 5
    assert CANONICAL_LIFECYCLE_CONTRACT_VERSION == 6

    payload = serialize_trade_lifecycle_checkpoint(_checkpoint(_open_state()))
    assert "st_exit_intent" in payload["state"]
    assert "last_closed_st_exit" in payload["state"]

    payload["schema_version"] = 4
    payload["contract_version"] = 5
    store = PersistentTradeLifecycleStore(tmp_path)
    store.path_for("TEST").write_text(json.dumps(payload), encoding="utf-8")
    loaded = store.load("TEST")

    assert loaded.status is LifecycleCheckpointStatus.INVALID
    assert loaded.checkpoint is None


def test_state_payload_round_trip_preserves_previous_closed_reason_across_new_open_state():
    classified = transition_st_exit_intent(
        _open_state(stage=ExitStage.EXIT_READY),
        STExitFamily.PROTECTIVE_EXIT,
        as_of=T1,
        reasons=("ST_THESIS_INVALIDATED",),
    )
    flat = transition_trade_lifecycle(
        classified,
        _decision(DecisionAction.SELL),
        as_of=T2,
        exit_stage=ExitStage.EXIT_READY,
        exit_execution_confirmed=True,
    ).current
    reopened = TradeLifecycleState(
        position=PositionState.OPEN,
        exit_stage=ExitStage.MONITOR,
        trade_id="trade:new",
        entry_as_of=T2,
        entry_metadata=PositionEntryMetadata(
            symbol="TEST",
            entry_horizon=DecisionHorizon.SHORT_TERM,
            scenario_kind=ScenarioKind.SHORT_TERM_STANDALONE,
            entry_as_of=T2,
            entry_price=105.0,
            active_target_identity=None,
            execution_timeframe="30m",
            execution_observed_at=T2,
            execution_available_at=T2,
            execution_reason="ENTRY_CONFIRMED",
            source_lineage=(),
            st_trade_memory=STTradeMemory(
                thesis_family=STThesisFamily.UNRESOLVED,
                economic_mission=STEconomicMission.UNRESOLVED,
                initial_defended_anchor=None,
                initial_target_context=None,
            ),
        ),
        st_economic_history=STEconomicHistory(),
        last_closed_st_exit=flat.last_closed_st_exit,
    )

    assert deserialize_trade_lifecycle_state(serialize_trade_lifecycle_state(reopened)) == reopened
    assert reopened.last_closed_st_exit.family is STExitFamily.PROTECTIVE_EXIT
