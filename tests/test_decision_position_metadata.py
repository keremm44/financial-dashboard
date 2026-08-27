from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pandas as pd
import pytest

from financial_dashboard.decision.arbiter import arbitrate_entry_scenarios
from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.eligibility import EligibilityState
from financial_dashboard.decision.entry import EntryDecision
from financial_dashboard.decision.execution import ExecutionTriggerEvent, ExecutionTriggerState
from financial_dashboard.decision.lifecycle import (
    ExitStage,
    PositionState,
    TradeLifecycleState,
    transition_entry_lifecycle,
    transition_trade_lifecycle,
)
from financial_dashboard.decision.market_state import StructuralRegime
from financial_dashboard.decision.opportunity import OpportunityState
from financial_dashboard.decision.position_metadata import (
    PositionEntryMetadata,
    build_position_entry_metadata,
)
from financial_dashboard.decision.scenario import (
    EntryScenarioAssessment,
    ScenarioKind,
    ScenarioPresence,
    ScenarioStage,
)
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection, ThesisState
from financial_dashboard.decision.target_path import TargetPathStatus


def _scenario(horizon, presence):
    present = presence is ScenarioPresence.PRESENT
    return EntryScenarioAssessment(
        horizon=horizon,
        presence=presence,
        stage=ScenarioStage.QUALIFIED if present else ScenarioStage.NOT_APPLICABLE,
        kind=ScenarioKind.CONTINUATION if present else ScenarioKind.NONE,
        structural_direction=StructuralDirection.LONG if present else StructuralDirection.UNRESOLVED,
        thesis_state=ThesisState.INTACT if present else ThesisState.UNRESOLVED,
        structural_regime=StructuralRegime.DIRECTIONAL if present else StructuralRegime.UNRESOLVED,
        opportunity_state=OpportunityState.MODERATE if present else OpportunityState.NONE,
        target_path_status=TargetPathStatus.READY if present else TargetPathStatus.NO_OBSERVED_PATH,
        active_target_identity="target:1" if present else None,
        eligibility_state=EligibilityState.ELIGIBLE if present else EligibilityState.BLOCKED,
        reasons=("SCENARIO_PRESENT",) if present else ("SCENARIO_ABSENT",),
        blockers=(),
        waiting_for=(),
        source_lineage=(f"{horizon.value}:scenario",) if present else (),
    )


def _buy_entry():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.ABSENT),
    )
    return EntryDecision(
        action=DecisionAction.BUY,
        selected_horizon=DecisionHorizon.LONG_TERM,
        arbitration=arbitration,
        scenario_stage=ScenarioStage.QUALIFIED,
        execution_state=ExecutionTriggerState.CONFIRMED,
        execution_event_consumed=True,
        reasons=("ENTRY_EXECUTED",),
        blockers=(),
        waiting_for=(),
        source_lineage=("30m:execution", "LONG_TERM:scenario"),
    )


def _event(as_of):
    return ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=StructuralDirection.LONG,
        timeframe="30m",
        observed_at=as_of,
        available_at=as_of,
        reason="FRESH_ENTRY_CONFIRMATION",
    )


def _snapshot(as_of, *, price=105.25):
    return SimpleNamespace(symbol="TEST", as_of=as_of, current_price=price)


def test_buy_freezes_entry_origin_metadata_on_open_position():
    as_of = pd.Timestamp("2026-01-05 10:00")
    transition = transition_entry_lifecycle(
        TradeLifecycleState(),
        _buy_entry(),
        _snapshot(as_of),
        execution_event=_event(as_of),
    )

    metadata = transition.current.entry_metadata
    assert transition.action is DecisionAction.BUY
    assert transition.current.position is PositionState.OPEN
    assert metadata is not None
    assert metadata.symbol == "TEST"
    assert metadata.entry_horizon is DecisionHorizon.LONG_TERM
    assert metadata.scenario_kind is ScenarioKind.CONTINUATION
    assert metadata.entry_as_of == as_of
    assert metadata.entry_price == 105.25
    assert metadata.active_target_identity == "target:1"
    assert metadata.execution_timeframe == "30m"
    assert metadata.execution_observed_at == as_of
    assert metadata.execution_reason == "FRESH_ENTRY_CONFIRMATION"
    assert metadata.source_lineage == ("30m:execution", "LONG_TERM:scenario")


def test_position_metadata_is_frozen_and_cannot_be_rewritten_in_place():
    as_of = pd.Timestamp("2026-01-05 10:00")
    metadata = build_position_entry_metadata(
        _snapshot(as_of),
        _buy_entry(),
        execution_event=_event(as_of),
    )

    with pytest.raises(FrozenInstanceError):
        metadata.entry_price = 999.0


def test_open_position_preserves_original_metadata_across_exit_stage_changes():
    as_of = pd.Timestamp("2026-01-05 10:00")
    opened = transition_entry_lifecycle(
        TradeLifecycleState(),
        _buy_entry(),
        _snapshot(as_of),
        execution_event=_event(as_of),
    ).current
    original = opened.entry_metadata

    watch = transition_trade_lifecycle(
        opened,
        SimpleNamespace(action=DecisionAction.NO_TRADE),
        as_of=pd.Timestamp("2026-01-05 11:00"),
        exit_stage=ExitStage.EXIT_WATCH,
    ).current
    ready = transition_trade_lifecycle(
        watch,
        SimpleNamespace(action=DecisionAction.NO_TRADE),
        as_of=pd.Timestamp("2026-01-05 12:00"),
        exit_stage=ExitStage.EXIT_READY,
    ).current

    assert watch.entry_metadata is original
    assert ready.entry_metadata is original
    assert ready.entry_metadata.entry_horizon is DecisionHorizon.LONG_TERM


def test_repeated_buy_cannot_promote_or_replace_original_entry_horizon_metadata():
    as_of = pd.Timestamp("2026-01-05 10:00")
    opened = transition_entry_lifecycle(
        TradeLifecycleState(),
        _buy_entry(),
        _snapshot(as_of),
        execution_event=_event(as_of),
    ).current
    original = opened.entry_metadata
    conflicting = replace(
        original,
        entry_horizon=DecisionHorizon.SHORT_TERM,
        source_lineage=("later:scenario",),
    )

    repeated = transition_trade_lifecycle(
        opened,
        SimpleNamespace(action=DecisionAction.BUY),
        as_of=pd.Timestamp("2026-01-05 10:30"),
        entry_metadata=conflicting,
    )

    assert repeated.action is DecisionAction.HOLD
    assert repeated.reason == "LIFECYCLE_REPEATED_BUY_SUPPRESSED"
    assert repeated.current.entry_metadata is original
    assert repeated.current.entry_metadata.entry_horizon is DecisionHorizon.LONG_TERM


def test_confirmed_exit_clears_entry_metadata_with_position_ownership():
    as_of = pd.Timestamp("2026-01-05 10:00")
    opened = transition_entry_lifecycle(
        TradeLifecycleState(),
        _buy_entry(),
        _snapshot(as_of),
        execution_event=_event(as_of),
    ).current

    closed = transition_trade_lifecycle(
        opened,
        SimpleNamespace(action=DecisionAction.NO_TRADE),
        as_of=pd.Timestamp("2026-01-05 12:00"),
        exit_stage=ExitStage.EXIT_READY,
        exit_execution_confirmed=True,
    )

    assert closed.action is DecisionAction.SELL
    assert closed.current == TradeLifecycleState()
    assert closed.current.entry_metadata is None


def test_metadata_requires_raw_fresh_confirmed_execution_provenance():
    as_of = pd.Timestamp("2026-01-05 10:00")
    entry = _buy_entry()

    with pytest.raises(ValueError, match="raw execution event"):
        transition_entry_lifecycle(
            TradeLifecycleState(),
            entry,
            _snapshot(as_of),
        )

    stale = _event(pd.Timestamp("2026-01-05 09:30"))
    with pytest.raises(ValueError, match="fresh"):
        build_position_entry_metadata(
            _snapshot(as_of),
            entry,
            execution_event=stale,
        )


def test_non_buy_cannot_create_position_entry_metadata():
    entry = replace(
        _buy_entry(),
        action=DecisionAction.READY,
        execution_event_consumed=False,
    )
    as_of = pd.Timestamp("2026-01-05 10:00")

    with pytest.raises(ValueError, match="only from an executed BUY"):
        build_position_entry_metadata(
            _snapshot(as_of),
            entry,
            execution_event=_event(as_of),
        )


def test_flat_lifecycle_rejects_position_entry_metadata():
    as_of = pd.Timestamp("2026-01-05 10:00")
    metadata = build_position_entry_metadata(
        _snapshot(as_of),
        _buy_entry(),
        execution_event=_event(as_of),
    )

    with pytest.raises(ValueError, match="FLAT lifecycle state"):
        TradeLifecycleState(entry_metadata=metadata)


def test_legacy_open_state_without_turn7_metadata_remains_supported_until_replay_migration():
    state = TradeLifecycleState(
        position=PositionState.OPEN,
        exit_stage=ExitStage.MONITOR,
        trade_id="legacy",
        entry_as_of=pd.Timestamp("2026-01-05 10:00"),
    )

    assert state.entry_metadata is None
