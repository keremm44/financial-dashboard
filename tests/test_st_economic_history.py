from dataclasses import dataclass
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
from financial_dashboard.decision.engine import DecisionEngineConfig
from financial_dashboard.decision.execution import ExecutionTriggerEvent, ExecutionTriggerState
from financial_dashboard.decision.lifecycle import ExitStage, PositionState, TradeLifecycleState
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
    STInitialTargetContext,
    STTradeMemory,
)
from financial_dashboard.decision.scenario import ScenarioKind, ScenarioPresence, ScenarioStage
from financial_dashboard.decision.st_economic_history import (
    STContinuationEpisodeState,
    STEconomicHistory,
    deserialize_st_economic_history,
    observe_st_economic_history,
    serialize_st_economic_history,
)
from financial_dashboard.decision.st_thesis_identity import (
    STDefendedAnchorKind,
    STEconomicMission,
    STThesisFamily,
)
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection
from financial_dashboard.decision.target_path import TargetPathRole


def _ref(
    as_of,
    *,
    native_id="sr:1",
    available_at=None,
    confirmed_at=None,
    domain=ContextDomain.SUPPORT_RESISTANCE,
    fact_type="RANGE_EXPORT",
    native_state="RANGE_BREAK_CONFIRMED",
    origin_time=None,
    lineage_id=None,
):
    return FactRef(
        domain=domain,
        fact_type=fact_type,
        symbol="TEST",
        timeframe="1h",
        native_id=native_id,
        native_state=native_state,
        origin_time=as_of if origin_time is None else origin_time,
        confirmed_at=as_of if confirmed_at is None else confirmed_at,
        available_at=as_of if available_at is None else available_at,
        lineage_id=lineage_id or native_id,
        causal_family=CausalFamily.STRUCTURAL_LEVEL,
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=ContextDataQuality.VALID,
    )


def _structure(
    as_of,
    *,
    boundary,
    confirmed_at=None,
    available_at=None,
    native_id="bos:1",
    event_type="BOS",
    direction=1,
    validity="VALID",
    relevance="CURRENT",
):
    confirmed = as_of if confirmed_at is None else confirmed_at
    ref = _ref(
        as_of,
        native_id=native_id,
        confirmed_at=confirmed,
        available_at=available_at,
        domain=ContextDomain.MARKET_STRUCTURE,
        fact_type="BOS",
        native_state=f"{validity}:{relevance}",
        origin_time=confirmed,
        lineage_id=native_id,
    )
    event = SimpleNamespace(
        ref=ref,
        event_type=event_type,
        direction=direction,
        broken_level=boundary,
        confirmation_status="CONFIRMED",
        validity=validity,
        relevance=relevance,
        outcome="CONTINUATION",
        bos_maturity="CONFIRMED",
    )
    timeframe = SimpleNamespace(
        timeframe="1h",
        data_quality=ContextDataQuality.VALID,
        events=(event,),
    )
    return SimpleNamespace(timeframe_facts=(timeframe,)), ref


def _sr(
    as_of,
    *,
    range_identity=8,
    confirmed_index=20,
    boundary=105.0,
    support=(104.0, 105.0),
    break_buffer=0.5,
    available_at=None,
    structural_confirmed_at=None,
    structural_boundary=None,
):
    ref = _ref(as_of, native_id=f"sr:{range_identity}", available_at=available_at)
    structure, structure_ref = _structure(
        as_of,
        boundary=boundary if structural_boundary is None else structural_boundary,
        confirmed_at=as_of if structural_confirmed_at is None else structural_confirmed_at,
        native_id=f"bos:{range_identity}:{confirmed_index}",
    )
    row = SimpleNamespace(
        timeframe="1h",
        ref=ref,
        state="RANGE_BREAK_CONFIRMED",
        range_identity=range_identity,
        break_direction=1,
        break_candidate_index=confirmed_index - 1,
        break_confirmed_index=confirmed_index,
        break_boundary=boundary,
        break_buffer=break_buffer,
        role_reversal_support_low=support[0],
        role_reversal_support_high=support[1],
    )
    projection = SimpleNamespace(
        timeframe_facts=(row,),
        _structure=structure,
        _structure_ref=structure_ref,
    )
    return projection, ref


def _fvg(
    as_of,
    *,
    identity="fvg:1",
    formed_at,
    lower=104.0,
    upper=105.0,
    reaction_confirmed=False,
    failed_reaction=False,
    available_at=None,
):
    ref = _ref(
        as_of,
        native_id=f"FVG_LIFECYCLE:1h:{identity}:{as_of}",
        available_at=available_at,
        domain=ContextDomain.FVG,
        fact_type="FVG_LIFECYCLE",
        native_state="REACTION_CONFIRMED" if reaction_confirmed else "ACTIVE",
        origin_time=formed_at,
        lineage_id=identity,
    )
    row = SimpleNamespace(
        ref=ref,
        identity=identity,
        direction=1,
        lower_boundary=lower,
        upper_boundary=upper,
        first_test_index=30,
        reaction_confirmed=reaction_confirmed,
        failed_reaction=failed_reaction,
    )
    return SimpleNamespace(fvg=(row,), engulfing=()), ref


def _metadata(entry_as_of):
    return PositionEntryMetadata(
        symbol="TEST",
        entry_horizon=DecisionHorizon.SHORT_TERM,
        scenario_kind=ScenarioKind.SHORT_TERM_STANDALONE,
        entry_as_of=entry_as_of,
        entry_price=104.0,
        active_target_identity="target:st:1",
        execution_timeframe="30m",
        execution_observed_at=entry_as_of,
        execution_available_at=entry_as_of,
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
                identity="target:st:1",
                low=110.0,
                high=112.0,
                anchor_price=111.0,
                roles=(TargetPathRole.OBJECTIVE,),
            ),
        ),
    )


def _open_state(entry_as_of):
    return TradeLifecycleState(
        position=PositionState.OPEN,
        exit_stage=ExitStage.MONITOR,
        trade_id=f"trade:{entry_as_of.isoformat()}",
        entry_as_of=entry_as_of,
        entry_metadata=_metadata(entry_as_of),
        st_economic_history=STEconomicHistory(),
    )


def _snapshot(
    as_of,
    *,
    price,
    support_resistance=None,
    structure=None,
    fvg=None,
    source_refs=(),
):
    if structure is None and support_resistance is not None:
        structure = getattr(support_resistance, "_structure", None)
    return SimpleNamespace(
        symbol="TEST",
        as_of=as_of,
        current_price=price,
        structure=structure,
        support_resistance=support_resistance,
        fvg_engulfing_lifecycle=fvg,
        source_refs=source_refs,
    )


def _with_history(state, history):
    return TradeLifecycleState(
        position=state.position,
        exit_stage=state.exit_stage,
        trade_id=state.trade_id,
        entry_as_of=state.entry_as_of,
        entry_metadata=state.entry_metadata,
        st_economic_history=history,
    )


def test_favorable_price_without_accepted_area_is_not_economic_progress():
    entry = pd.Timestamp("2026-01-05 10:00")
    state = _open_state(entry)
    snapshot = _snapshot(entry + pd.Timedelta(hours=1), price=118.0)

    history = observe_st_economic_history(snapshot, state)

    assert history == STEconomicHistory()
    assert history.accepted_areas == ()
    assert history.progress_events == ()
    assert history.mission_completion is None


def test_pre_entry_confirmed_sr_state_cannot_be_recounted_as_post_entry_gained_area():
    entry = pd.Timestamp("2026-01-05 10:00")
    state = _open_state(entry)
    old_confirmation = entry - pd.Timedelta(minutes=30)
    sr, sr_ref = _sr(
        entry + pd.Timedelta(hours=1),
        support=(105.0, 106.0),
        boundary=106.0,
        structural_confirmed_at=old_confirmation,
    )

    history = observe_st_economic_history(
        _snapshot(
            entry + pd.Timedelta(hours=1),
            price=109.0,
            support_resistance=sr,
            source_refs=(sr_ref, sr._structure_ref),
        ),
        state,
    )

    assert history == STEconomicHistory()


def test_sr_acceptance_without_native_aligned_post_entry_bos_is_fail_closed():
    entry = pd.Timestamp("2026-01-05 10:00")
    state = _open_state(entry)
    as_of = entry + pd.Timedelta(hours=1)
    sr, sr_ref = _sr(
        as_of,
        boundary=106.0,
        support=(105.0, 106.0),
        break_buffer=0.25,
        structural_boundary=106.5,
    )

    history = observe_st_economic_history(
        _snapshot(as_of, price=109.0, support_resistance=sr, source_refs=(sr_ref, sr._structure_ref)),
        state,
    )

    assert history == STEconomicHistory()


def test_accepted_area_is_distinct_from_progress_and_earned_defense_never_loosens():
    entry = pd.Timestamp("2026-01-05 10:00")
    state = _open_state(entry)

    t1 = entry + pd.Timedelta(hours=1)
    sr1, ref1 = _sr(t1, support=(104.0, 105.0), boundary=105.0)
    first = observe_st_economic_history(
        _snapshot(t1, price=107.0, support_resistance=sr1, source_refs=(ref1, sr1._structure_ref)),
        state,
    )
    assert len(first.accepted_areas) == 1
    assert len(first.earned_defenses) == 1
    assert first.progress_events == ()

    state = _with_history(state, first)
    t2 = entry + pd.Timedelta(hours=2)
    sr2, ref2 = _sr(
        t2,
        range_identity=9,
        confirmed_index=21,
        support=(103.5, 106.0),
        boundary=106.0,
    )
    second = observe_st_economic_history(
        _snapshot(t2, price=108.0, support_resistance=sr2, source_refs=(ref2, sr2._structure_ref)),
        state,
    )
    assert len(second.accepted_areas) == 1
    assert second.active_earned_defense.low == 104.0

    t3 = entry + pd.Timedelta(hours=3)
    sr3, ref3 = _sr(
        t3,
        range_identity=10,
        confirmed_index=22,
        support=(105.0, 106.0),
        boundary=106.0,
    )
    state = _with_history(state, second)
    third = observe_st_economic_history(
        _snapshot(t3, price=109.0, support_resistance=sr3, source_refs=(ref3, sr3._structure_ref)),
        state,
    )
    assert len(third.accepted_areas) == 2
    assert len(third.earned_defenses) == 2
    assert len(third.progress_events) == 1
    assert third.active_earned_defense.low == 105.0
    assert state.entry_metadata.st_trade_memory.initial_defended_anchor.low == 99.0


def test_mission_completion_requires_accepted_area_at_initial_target_not_price_touch():
    entry = pd.Timestamp("2026-01-05 10:00")
    state = _open_state(entry)
    touched = observe_st_economic_history(
        _snapshot(entry + pd.Timedelta(hours=1), price=112.0),
        state,
    )
    assert touched.mission_completion is None

    t2 = entry + pd.Timedelta(hours=2)
    sr, ref = _sr(
        t2,
        range_identity=11,
        confirmed_index=23,
        support=(110.0, 111.0),
        boundary=111.0,
    )
    completed = observe_st_economic_history(
        _snapshot(t2, price=113.0, support_resistance=sr, source_refs=(ref, sr._structure_ref)),
        _with_history(state, touched),
    )
    assert completed.mission_completion is not None
    assert completed.mission_completion.target_identity == "target:st:1"


def test_duplicate_and_future_unavailable_area_evidence_are_ignored():
    entry = pd.Timestamp("2026-01-05 10:00")
    state = _open_state(entry)
    t1 = entry + pd.Timedelta(hours=1)
    sr, ref = _sr(t1, support=(105.0, 106.0), boundary=106.0)

    once = observe_st_economic_history(
        _snapshot(t1, price=108.0, support_resistance=sr, source_refs=(ref, sr._structure_ref)),
        state,
    )
    state_once = _with_history(state, once)
    twice = observe_st_economic_history(
        _snapshot(t1 + pd.Timedelta(minutes=30), price=109.0, support_resistance=sr),
        state_once,
    )
    assert twice == once

    future_at = t1 + pd.Timedelta(hours=1)
    future_sr, future_ref = _sr(
        future_at,
        range_identity=12,
        confirmed_index=24,
        support=(107.0, 108.0),
        boundary=108.0,
        available_at=future_at + pd.Timedelta(minutes=30),
    )
    future = observe_st_economic_history(
        _snapshot(future_at, price=110.0, support_resistance=future_sr),
        state_once,
    )
    assert future == once
    assert not future_ref.is_available_at(future_at)


def test_continuation_episode_is_live_then_succeeds_only_with_new_accepted_progress():
    entry = pd.Timestamp("2026-01-05 10:00")
    state = _open_state(entry)
    formed = entry + pd.Timedelta(minutes=30)
    t1 = entry + pd.Timedelta(hours=1)
    fvg1, ref1 = _fvg(t1, formed_at=formed)

    live = observe_st_economic_history(
        _snapshot(t1, price=106.0, fvg=fvg1, source_refs=(ref1,)),
        state,
    )
    assert len(live.continuation_episodes) == 1
    assert live.continuation_episodes[0].state is STContinuationEpisodeState.LIVE

    t2 = entry + pd.Timedelta(hours=2)
    fvg2, fvg_ref2 = _fvg(t2, formed_at=formed, reaction_confirmed=True)
    sr, sr_ref = _sr(
        t2,
        range_identity=13,
        confirmed_index=25,
        support=(106.0, 107.0),
        boundary=107.0,
    )
    state = _with_history(state, live)
    succeeded = observe_st_economic_history(
        _snapshot(
            t2,
            price=109.0,
            support_resistance=sr,
            fvg=fvg2,
            source_refs=(sr_ref, sr._structure_ref, fvg_ref2),
        ),
        state,
    )
    episode = succeeded.continuation_episodes[0]
    assert episode.state is STContinuationEpisodeState.SUCCEEDED
    assert episode.accepted_area_id == succeeded.progress_events[-1].accepted_area_id


def test_continuation_episode_failure_is_terminal_and_not_a_crude_counter():
    entry = pd.Timestamp("2026-01-05 10:00")
    state = _open_state(entry)
    formed = entry + pd.Timedelta(minutes=30)
    t1 = entry + pd.Timedelta(hours=1)
    fvg, ref = _fvg(t1, formed_at=formed, failed_reaction=True)

    failed = observe_st_economic_history(
        _snapshot(t1, price=104.5, fvg=fvg, source_refs=(ref,)),
        state,
    )
    assert len(failed.continuation_episodes) == 1
    assert failed.continuation_episodes[0].state is STContinuationEpisodeState.FAILED
    assert not hasattr(failed, "failed_attempt_count")
    assert not hasattr(failed, "consumed")
    assert not hasattr(failed, "healthy_base")


def test_economic_history_round_trip_is_exact():
    entry = pd.Timestamp("2026-01-05 10:00")
    state = _open_state(entry)
    t1 = entry + pd.Timedelta(hours=1)
    sr, ref = _sr(t1, support=(105.0, 106.0), boundary=106.0)
    history = observe_st_economic_history(
        _snapshot(t1, price=108.0, support_resistance=sr, source_refs=(ref, sr._structure_ref)),
        state,
    )

    payload = serialize_st_economic_history(history)
    restored = deserialize_st_economic_history(payload)

    assert restored == history


def _target_path(as_of):
    node = SimpleNamespace(
        identity="target:st:1",
        direction=StructuralDirection.LONG,
        low=110.0,
        high=112.0,
        anchor_price=111.0,
        roles=(TargetPathRole.OBJECTIVE,),
        source_refs=(),
    )
    return SimpleNamespace(as_of=as_of, nodes=(node,))


def _entry():
    scenario = SimpleNamespace(
        horizon=DecisionHorizon.SHORT_TERM,
        presence=ScenarioPresence.PRESENT,
        stage=ScenarioStage.QUALIFIED,
        structural_direction=StructuralDirection.LONG,
        kind=ScenarioKind.SHORT_TERM_STANDALONE,
        active_target_identity="target:st:1",
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


@dataclass(frozen=True)
class _ReplaySnapshot:
    symbol: str
    as_of: pd.Timestamp
    current_price: float
    support_resistance: object | None
    source_refs: tuple
    structure: object | None = None
    entry_action: DecisionAction = DecisionAction.WAIT
    fvg_engulfing_lifecycle: object | None = None
    order_block_behavior: object | None = None

    def target_path(self, direction):
        return _target_path(self.as_of)

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


def test_cold_warm_restart_history_equivalence_and_zero_action_diff(tmp_path):
    t1 = pd.Timestamp("2026-01-05 10:00")
    t2 = pd.Timestamp("2026-01-05 11:00")
    t3 = pd.Timestamp("2026-01-05 12:00")
    entry_sr, entry_ref = _sr(
        t1,
        range_identity=7,
        confirmed_index=10,
        boundary=100.0,
        support=(99.0, 100.0),
    )
    gained_sr, gained_ref = _sr(
        t3,
        range_identity=8,
        confirmed_index=20,
        boundary=106.0,
        support=(105.0, 106.0),
    )
    snapshots = (
        _ReplaySnapshot(
            "TEST",
            t1,
            104.0,
            entry_sr,
            (entry_ref, entry_sr._structure_ref),
            structure=entry_sr._structure,
            entry_action=DecisionAction.BUY,
        ),
        _ReplaySnapshot("TEST", t2, 118.0, None, ()),
        _ReplaySnapshot(
            "TEST",
            t3,
            109.0,
            gained_sr,
            (gained_ref, gained_sr._structure_ref),
            structure=gained_sr._structure,
        ),
    )
    events = {t1: _event(t1)}

    cold = replay_canonical_trade_lifecycle(snapshots, entry_execution_events=events)
    assert tuple(row.action for row in cold.rows) == (
        DecisionAction.BUY,
        DecisionAction.HOLD,
        DecisionAction.HOLD,
    )
    assert len(cold.final_state.st_economic_history.accepted_areas) == 1
    assert len(cold.final_state.st_economic_history.progress_events) == 1

    runner = PersistentCanonicalLifecycleReplayRunner(tmp_path)
    prefix = runner.run("TEST", snapshots[:2], entry_execution_events=events)
    resumed = runner.run("TEST", snapshots, entry_execution_events=events)

    assert resumed.resumed is True
    assert resumed.processed_count == 1
    assert resumed.replay.final_state.st_economic_history == cold.final_state.st_economic_history
    assert resumed.replay.final_state.entry_metadata == cold.final_state.entry_metadata
    reconstructed_actions = tuple(row.action for row in (*prefix.replay.rows, *resumed.replay.rows))
    assert reconstructed_actions == tuple(row.action for row in cold.rows)


def test_v4_checkpoint_requires_history_and_v3_is_not_silently_migrated(tmp_path):
    assert TRADE_LIFECYCLE_STATE_SCHEMA_VERSION == 4
    assert CANONICAL_LIFECYCLE_CONTRACT_VERSION == 5

    entry = pd.Timestamp("2026-01-05 10:00")
    state = _open_state(entry)
    checkpoint = TradeLifecycleCheckpoint(
        symbol="TEST",
        state=state,
        prefix_count=1,
        last_as_of=entry,
        causal_prefix_digest="a" * 64,
        decision_config_digest=decision_config_digest(DecisionEngineConfig()),
    )
    payload = serialize_trade_lifecycle_checkpoint(checkpoint)
    assert payload["state"]["st_economic_history"] == {
        "accepted_areas": [],
        "earned_defenses": [],
        "progress_events": [],
        "mission_completion": None,
        "continuation_episodes": [],
    }

    payload["schema_version"] = 3
    payload["contract_version"] = 3
    store = PersistentTradeLifecycleStore(tmp_path)
    store.path_for("TEST").write_text(__import__("json").dumps(payload), encoding="utf-8")
    loaded = store.load("TEST")
    assert loaded.status is LifecycleCheckpointStatus.INVALID
    assert loaded.checkpoint is None
