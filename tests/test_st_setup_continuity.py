from dataclasses import fields
from types import SimpleNamespace

import pandas as pd

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.execution import ExecutionTriggerEvent, ExecutionTriggerState
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
    deserialize_trade_lifecycle_state,
    serialize_trade_lifecycle_state,
)
from financial_dashboard.decision.lifecycle_replay import replay_canonical_trade_lifecycle
from financial_dashboard.decision.position_metadata import (
    PositionEntryMetadata,
    STInitialDefendedAnchor,
    STInitialTargetContext,
    STTradeMemory,
)
from financial_dashboard.decision.scenario import ScenarioKind, ScenarioPresence, ScenarioStage
from financial_dashboard.decision.st_economic_history import (
    STAcceptedAreaEvent,
    STEarnedDefenseEvent,
    STEconomicHistory,
    STMissionCompletionMilestone,
    STProgressEvent,
)
from financial_dashboard.decision.st_exit_intent import STClosedExitRecord, STExitFamily
from financial_dashboard.decision.st_setup_continuity import (
    STClosedMovementRecord,
    STMovementRiskBoundary,
    STSetupContinuityState,
    apply_st_reentry_novelty_policy,
    assess_st_setup_continuity,
    summarize_st_reentry_novelty,
)
from financial_dashboard.decision.st_thesis_identity import (
    STDefendedAnchorKind,
    STEconomicMission,
    STThesisFamily,
)
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection
from financial_dashboard.decision.target_path import TargetPathRole


ENTRY = pd.Timestamp("2026-01-05 09:00")
EXIT = pd.Timestamp("2026-01-05 11:00")


def _ref(
    as_of,
    *,
    native_id,
    domain=ContextDomain.SUPPORT_RESISTANCE,
    confirmed_at=None,
):
    confirmed = as_of if confirmed_at is None else confirmed_at
    return FactRef(
        domain=domain,
        fact_type="TEST_FACT",
        symbol="TEST",
        timeframe="1h",
        native_id=native_id,
        native_state="TEST",
        origin_time=confirmed,
        confirmed_at=confirmed,
        available_at=as_of,
        lineage_id=native_id,
        causal_family=(
            CausalFamily.STRUCTURAL_LEVEL
            if domain in {ContextDomain.SUPPORT_RESISTANCE, ContextDomain.MARKET_STRUCTURE}
            else CausalFamily.IMPULSE
        ),
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=ContextDataQuality.VALID,
    )


def _sr(as_of, *, range_id, low, high, boundary, confirmed_at=None):
    ref = _ref(
        as_of,
        native_id=f"sr:{range_id}",
        confirmed_at=confirmed_at,
    )
    row = SimpleNamespace(
        timeframe="1h",
        ref=ref,
        state="RANGE_BREAK_CONFIRMED",
        range_identity=range_id,
        break_direction=1,
        break_candidate_index=10,
        break_boundary=boundary,
        role_reversal_support_low=low,
        role_reversal_support_high=high,
    )
    return SimpleNamespace(timeframe_facts=(row,)), ref


def _fvg(as_of, *, identity, low, high, confirmed_at=None):
    ref = _ref(
        as_of,
        native_id=f"fvg:{identity}",
        domain=ContextDomain.FVG,
        confirmed_at=confirmed_at,
    )
    row = SimpleNamespace(
        ref=ref,
        identity=identity,
        direction=1,
        lower_boundary=low,
        upper_boundary=high,
        reaction_confirmed=True,
        failed_reaction=False,
        full_fill=False,
        invalid=False,
    )
    return SimpleNamespace(fvg=(row,), engulfing=()), ref


def _scenario(*, target="target:1", kind=ScenarioKind.SHORT_TERM_STANDALONE):
    return SimpleNamespace(
        horizon=DecisionHorizon.SHORT_TERM,
        presence=ScenarioPresence.PRESENT,
        stage=ScenarioStage.QUALIFIED,
        structural_direction=StructuralDirection.LONG,
        kind=kind,
        active_target_identity=target,
    )


def _entry_decision(*, event=False, target="target:1", kind=ScenarioKind.SHORT_TERM_STANDALONE):
    scenario = _scenario(target=target, kind=kind)
    return SimpleNamespace(
        action=DecisionAction.BUY if event else DecisionAction.READY,
        selected_horizon=DecisionHorizon.SHORT_TERM,
        scenario_stage=ScenarioStage.QUALIFIED,
        execution_state=ExecutionTriggerState.CONFIRMED if event else ExecutionTriggerState.ABSENT,
        execution_event_consumed=event,
        arbitration=SimpleNamespace(selected_scenario=scenario),
        reasons=("ENTRY_FIXTURE",),
        blockers=(),
        waiting_for=() if event else ("FRESH_EXECUTION_EVENT",),
        source_lineage=("entry:fixture",),
    )


def _movement(
    *,
    exit_family=STExitFamily.PROFIT_HARVEST,
    thesis=STThesisFamily.BREAKOUT_ACCEPTANCE,
    mission=STEconomicMission.EXPAND_FROM_ACCEPTED_HIGHER_AREA,
    risk_identity="SR_BREAKOUT:7",
    low=99.0,
    high=100.0,
    target="target:1",
):
    risk = STMovementRiskBoundary(
        kind=STDefendedAnchorKind.BREAKOUT_ROLE_SUPPORT.value,
        identity=risk_identity,
        timeframe="1h",
        low=low,
        high=high,
    )
    return STClosedMovementRecord(
        trade_id="trade:prior",
        entry_as_of=ENTRY,
        exit_as_of=EXIT,
        exit_family=exit_family,
        thesis_family=thesis,
        economic_mission=mission,
        initial_risk=risk,
        terminal_risk=risk,
        initial_target_identity=target,
    )


def _closed_state(movement):
    exit_record = STClosedExitRecord(
        trade_id=movement.trade_id,
        entry_as_of=movement.entry_as_of,
        exit_as_of=movement.exit_as_of,
        family=movement.exit_family,
        intent_committed_at=movement.exit_as_of,
        reasons=("PRIOR_EXIT",),
        source_lineage=(),
    )
    return TradeLifecycleState(
        last_closed_st_exit=exit_record,
        last_closed_st_movement=movement,
    )


class _Snapshot(SimpleNamespace):
    def entry_decision(self, *, config=None, execution_event=None):
        return _entry_decision(
            event=execution_event is not None,
            target=self.target_identity,
            kind=self.scenario_kind,
        )

    def target_path(self, direction):
        node = SimpleNamespace(
            identity=self.target_identity,
            direction=StructuralDirection.LONG,
            low=self.target_low,
            high=self.target_high,
            anchor_price=(self.target_low + self.target_high) * 0.5,
            roles=(TargetPathRole.OBJECTIVE,),
            source_refs=(),
        )
        return SimpleNamespace(as_of=self.as_of, nodes=(node,))


def _snapshot(
    as_of,
    *,
    price,
    sr=None,
    fvg=None,
    refs=(),
    target="target:1",
    target_low=110.0,
    target_high=112.0,
    kind=ScenarioKind.SHORT_TERM_STANDALONE,
):
    return _Snapshot(
        symbol="TEST",
        as_of=as_of,
        current_price=price,
        support_resistance=sr,
        order_block_behavior=None,
        fvg_engulfing_lifecycle=fvg,
        source_refs=refs,
        target_identity=target,
        target_low=target_low,
        target_high=target_high,
        scenario_kind=kind,
    )


def _event(as_of, *, reason="ENTRY_CONFIRMED"):
    return ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=StructuralDirection.LONG,
        timeframe="30m",
        observed_at=as_of,
        available_at=as_of,
        reason=reason,
        source_refs=(),
    )


def _metadata():
    return PositionEntryMetadata(
        symbol="TEST",
        entry_horizon=DecisionHorizon.SHORT_TERM,
        scenario_kind=ScenarioKind.SHORT_TERM_STANDALONE,
        entry_as_of=ENTRY,
        entry_price=100.0,
        active_target_identity="target:1",
        execution_timeframe="30m",
        execution_observed_at=ENTRY,
        execution_available_at=ENTRY,
        execution_reason="CONFIRMED",
        source_lineage=(),
        st_trade_memory=STTradeMemory(
            thesis_family=STThesisFamily.BREAKOUT_ACCEPTANCE,
            economic_mission=STEconomicMission.EXPAND_FROM_ACCEPTED_HIGHER_AREA,
            initial_defended_anchor=STInitialDefendedAnchor(
                kind=STDefendedAnchorKind.BREAKOUT_ROLE_SUPPORT,
                identity="SR_BREAKOUT:7",
                timeframe="1h",
                low=99.0,
                high=100.0,
            ),
            initial_target_context=STInitialTargetContext(
                identity="target:1",
                low=110.0,
                high=112.0,
                anchor_price=111.0,
                roles=(TargetPathRole.OBJECTIVE,),
            ),
        ),
    )


def test_10a_closed_movement_freezes_minimal_facts_and_survives_restart():
    accepted = STAcceptedAreaEvent(
        event_id="area:1",
        observed_at=ENTRY + pd.Timedelta(hours=1),
        timeframe="1h",
        low=105.0,
        high=106.0,
        break_boundary=105.0,
    )
    earned = STEarnedDefenseEvent(
        event_id="earned:1",
        observed_at=accepted.observed_at,
        accepted_area_id=accepted.event_id,
        low=105.0,
        high=106.0,
    )
    progress = STProgressEvent(
        event_id="progress:1",
        observed_at=accepted.observed_at,
        accepted_area_id=accepted.event_id,
        accepted_floor=105.0,
        distance_from_entry=5.0,
    )
    mission = STMissionCompletionMilestone(
        event_id="mission:1",
        observed_at=accepted.observed_at,
        target_identity="target:1",
        accepted_area_id=accepted.event_id,
    )
    state = TradeLifecycleState(
        position=PositionState.OPEN,
        exit_stage=ExitStage.EXIT_READY,
        trade_id="trade:prior",
        entry_as_of=ENTRY,
        entry_metadata=_metadata(),
        st_economic_history=STEconomicHistory(
            accepted_areas=(accepted,),
            earned_defenses=(earned,),
            progress_events=(progress,),
            mission_completion=mission,
        ),
    )
    state = transition_st_exit_intent(
        state,
        STExitFamily.PROFIT_HARVEST,
        as_of=EXIT,
        reasons=("ST_CONSUMED_POLICY_COMMITTED",),
    )
    closed = transition_trade_lifecycle(
        state,
        SimpleNamespace(action=DecisionAction.SELL),
        as_of=EXIT,
        exit_stage=ExitStage.EXIT_READY,
        exit_policy_mandated=True,
    ).current

    movement = closed.last_closed_st_movement
    assert movement is not None
    assert movement.thesis_family is STThesisFamily.BREAKOUT_ACCEPTANCE
    assert movement.initial_risk.identity == "SR_BREAKOUT:7"
    assert movement.terminal_risk.identity == "earned:1"
    assert (movement.terminal_risk.low, movement.terminal_risk.high) == (105.0, 106.0)
    assert movement.last_progress_area_id == "area:1"
    assert movement.mission_completed_target_identity == "target:1"
    assert not hasattr(movement, "cooldown")
    assert not hasattr(movement, "same_movement")
    assert {item.name for item in fields(STClosedMovementRecord)}.isdisjoint(
        {"maturity", "healthy_base", "consumed"}
    )

    restored = deserialize_trade_lifecycle_state(serialize_trade_lifecycle_state(closed))
    assert restored == closed
    assert restored.last_closed_st_movement == movement
    assert TRADE_LIFECYCLE_STATE_SCHEMA_VERSION == 6
    assert CANONICAL_LIFECYCLE_CONTRACT_VERSION == 9


def test_10a_same_old_movement_is_identified_independently_of_fresh_execution_event():
    as_of = EXIT + pd.Timedelta(minutes=30)
    sr, ref = _sr(
        as_of,
        range_id=7,
        low=99.0,
        high=100.0,
        boundary=100.0,
        confirmed_at=EXIT - pd.Timedelta(minutes=30),
    )
    snapshot = _snapshot(as_of, price=104.0, sr=sr, refs=(ref,))
    pre = _entry_decision(event=False)
    assessment = assess_st_setup_continuity(snapshot, pre, _movement())

    assert assessment.state is STSetupContinuityState.SAME_MOVEMENT
    assert assessment.new_information is False
    assert assessment.new_risk_boundary is False
    assert assessment.new_economic_move is False

    # Even an already-CONFIRMED execution-looking decision cannot turn old movement
    # continuity into novelty; the policy strips consumption and waits for new setup.
    gated = apply_st_reentry_novelty_policy(_entry_decision(event=True), assessment)
    assert gated.action is DecisionAction.WAIT
    assert gated.execution_event_consumed is False
    assert "ST_REENTRY_NOVELTY_TO_ESTABLISH" in gated.waiting_for


def test_10b_harvest_new_post_exit_base_risk_and_move_is_immediately_tradable_without_cooldown():
    as_of = EXIT + pd.Timedelta(minutes=1)
    sr, ref = _sr(
        as_of,
        range_id=8,
        low=102.0,
        high=103.0,
        boundary=103.0,
        confirmed_at=as_of,
    )
    snapshot = _snapshot(
        as_of,
        price=105.0,
        sr=sr,
        refs=(ref,),
        target="target:2",
        target_low=115.0,
        target_high=117.0,
    )
    pre = _entry_decision(event=False, target="target:2")
    assessment = assess_st_setup_continuity(snapshot, pre, _movement())

    assert assessment.state is STSetupContinuityState.NOVEL_SETUP
    assert assessment.new_information is True
    assert assessment.new_risk_boundary is True
    assert assessment.new_economic_move is True
    assert assessment.protective_reversal_confirmed is True

    released = apply_st_reentry_novelty_policy(_entry_decision(event=True, target="target:2"), assessment)
    assert released.action is DecisionAction.BUY
    assert released.execution_event_consumed is True


def test_10b_protective_immediate_rebound_is_not_enough_but_new_acceptance_can_reopen():
    movement = _movement(exit_family=STExitFamily.PROTECTIVE_EXIT)
    as_of = EXIT + pd.Timedelta(minutes=30)
    fvg, fvg_ref = _fvg(
        as_of,
        identity="rebound",
        low=101.0,
        high=102.0,
        confirmed_at=as_of,
    )
    rebound = _snapshot(
        as_of,
        price=103.0,
        fvg=fvg,
        refs=(fvg_ref,),
        target="target:2",
        kind=ScenarioKind.CONTINUATION,
    )
    rebound_assessment = assess_st_setup_continuity(
        rebound,
        _entry_decision(event=False, target="target:2", kind=ScenarioKind.CONTINUATION),
        movement,
    )
    assert rebound_assessment.state is STSetupContinuityState.SAME_MOVEMENT
    assert rebound_assessment.new_information is True
    assert rebound_assessment.new_risk_boundary is True
    assert rebound_assessment.protective_reversal_confirmed is False
    assert "ST_REENTRY_PROTECTIVE_INVALIDATION_NOT_REVERSED" in rebound_assessment.reasons

    accepted_at = as_of + pd.Timedelta(minutes=30)
    sr, sr_ref = _sr(
        accepted_at,
        range_id=9,
        low=103.0,
        high=104.0,
        boundary=104.0,
        confirmed_at=accepted_at,
    )
    accepted = _snapshot(
        accepted_at,
        price=106.0,
        sr=sr,
        refs=(sr_ref,),
        target="target:2",
    )
    accepted_assessment = assess_st_setup_continuity(
        accepted,
        _entry_decision(event=False, target="target:2"),
        movement,
    )
    assert accepted_assessment.state is STSetupContinuityState.NOVEL_SETUP
    assert accepted_assessment.protective_reversal_confirmed is True


def test_10b_replay_does_not_consume_fresh_event_for_same_movement():
    as_of = EXIT + pd.Timedelta(minutes=30)
    sr, ref = _sr(
        as_of,
        range_id=7,
        low=99.0,
        high=100.0,
        boundary=100.0,
        confirmed_at=EXIT - pd.Timedelta(minutes=15),
    )
    snapshot = _snapshot(as_of, price=104.0, sr=sr, refs=(ref,))
    replay = replay_canonical_trade_lifecycle(
        (snapshot,),
        initial_state=_closed_state(_movement()),
        entry_execution_events={as_of: _event(as_of, reason="FRESH_BUT_OLD_MOVEMENT")},
    )

    row = replay.rows[0]
    assert row.action is DecisionAction.WAIT
    assert row.entry_decision.execution_event_consumed is False
    assert replay.final_state.position is PositionState.FLAT
    assert "ST_REENTRY_SAME_ECONOMIC_MOVEMENT" in row.entry_decision.reasons
    assert "ST_REENTRY_NOVELTY_TO_ESTABLISH" in row.entry_decision.waiting_for


def test_10b_genuine_new_setup_replays_buy_and_restart_identically():
    as_of = EXIT + pd.Timedelta(minutes=30)
    sr, ref = _sr(
        as_of,
        range_id=8,
        low=102.0,
        high=103.0,
        boundary=103.0,
        confirmed_at=as_of,
    )
    snapshot = _snapshot(
        as_of,
        price=105.0,
        sr=sr,
        refs=(ref,),
        target="target:2",
        target_low=115.0,
        target_high=117.0,
    )
    initial = _closed_state(_movement())
    events = {as_of: _event(as_of)}

    cold = replay_canonical_trade_lifecycle(
        (snapshot,),
        initial_state=initial,
        entry_execution_events=events,
    )
    restored = deserialize_trade_lifecycle_state(serialize_trade_lifecycle_state(initial))
    restarted = replay_canonical_trade_lifecycle(
        (snapshot,),
        initial_state=restored,
        entry_execution_events=events,
    )

    assert cold.rows[0].action is DecisionAction.BUY
    assert cold.rows[0].entry_decision.execution_event_consumed is True
    assert cold.final_state.position is PositionState.OPEN
    assert "ST_REENTRY_NOVEL_ECONOMIC_SETUP_CONFIRMED" in cold.rows[0].entry_decision.reasons
    assert restarted.rows[0].action == cold.rows[0].action
    assert restarted.rows[0].entry_decision.reasons == cold.rows[0].entry_decision.reasons
    assert restarted.final_state == cold.final_state


def test_step10_metrics_measure_churn_control_and_new_setup_release_together():
    same = SimpleNamespace(
        action=DecisionAction.WAIT,
        reasons=("ST_REENTRY_SAME_ECONOMIC_MOVEMENT",),
    )
    unresolved = SimpleNamespace(
        action=DecisionAction.WAIT,
        reasons=("ST_REENTRY_SETUP_CONTINUITY_UNRESOLVED",),
    )
    novel_buy = SimpleNamespace(
        action=DecisionAction.BUY,
        reasons=("ST_REENTRY_NOVEL_ECONOMIC_SETUP_CONFIRMED",),
    )
    novel_ready = SimpleNamespace(
        action=DecisionAction.READY,
        reasons=("ST_REENTRY_NOVEL_ECONOMIC_SETUP_CONFIRMED",),
    )
    metrics = summarize_st_reentry_novelty((same, unresolved, novel_buy, novel_ready))

    assert metrics.same_movement_blocks == 1
    assert metrics.unresolved_blocks == 1
    assert metrics.novel_setups_released == 2
    assert metrics.novel_setups_executed == 1
    assert metrics.novel_setups_waiting_execution == 1
