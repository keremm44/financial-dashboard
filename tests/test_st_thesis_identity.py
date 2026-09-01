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
from financial_dashboard.decision.execution import ExecutionTriggerEvent, ExecutionTriggerState
from financial_dashboard.decision.lifecycle_replay import replay_canonical_trade_lifecycle
from financial_dashboard.decision.scenario import ScenarioKind, ScenarioPresence, ScenarioStage
from financial_dashboard.decision.st_thesis_identity import (
    STDefendedAnchorKind,
    STEconomicMission,
    STThesisFamily,
    audit_st_thesis_identity_shadow,
    classify_executed_st_thesis,
)
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection


def _ref(
    as_of,
    *,
    domain=ContextDomain.SUPPORT_RESISTANCE,
    native_id="fact:1",
    native_state="VALID",
    available_at=None,
):
    return FactRef(
        domain=domain,
        fact_type="TEST_FACT",
        symbol="TEST",
        timeframe="1h",
        native_id=native_id,
        native_state=native_state,
        origin_time=as_of,
        confirmed_at=as_of,
        available_at=as_of if available_at is None else available_at,
        lineage_id=native_id,
        causal_family=CausalFamily.STRUCTURAL_LEVEL,
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=ContextDataQuality.VALID,
    )


def _entry(
    *,
    kind=ScenarioKind.SHORT_TERM_STANDALONE,
    horizon=DecisionHorizon.SHORT_TERM,
    target_identity="target:st:1",
):
    scenario = SimpleNamespace(
        horizon=horizon,
        presence=ScenarioPresence.PRESENT,
        stage=ScenarioStage.QUALIFIED,
        structural_direction=StructuralDirection.LONG,
        kind=kind,
        active_target_identity=target_identity,
    )
    return SimpleNamespace(
        action=DecisionAction.BUY,
        selected_horizon=horizon,
        scenario_stage=ScenarioStage.QUALIFIED,
        execution_state=ExecutionTriggerState.CONFIRMED,
        execution_event_consumed=True,
        arbitration=SimpleNamespace(selected_scenario=scenario),
        reasons=("ENTRY_TEST",),
        blockers=(),
        waiting_for=(),
        source_lineage=("entry:test",),
    )


def _sr_snapshot(
    as_of,
    *,
    state,
    direction,
    boundary,
    price,
    role_support=(None, None),
    break_candidate_index=10,
    ref=None,
):
    sr_ref = ref or _ref(as_of)
    row = SimpleNamespace(
        timeframe="1h",
        ref=sr_ref,
        state=state,
        range_identity=7,
        break_direction=direction,
        break_candidate_index=break_candidate_index,
        break_boundary=boundary,
        role_reversal_support_low=role_support[0],
        role_reversal_support_high=role_support[1],
    )
    return SimpleNamespace(
        symbol="TEST",
        as_of=as_of,
        current_price=price,
        support_resistance=SimpleNamespace(timeframe_facts=(row,)),
        order_block_behavior=None,
        fvg_engulfing_lifecycle=None,
        source_refs=(sr_ref,),
    )


def _with_confirmed_ob(snapshot, *, low=98.0, high=100.0):
    ref = _ref(
        snapshot.as_of,
        domain=ContextDomain.ORDER_BLOCK,
        native_id="ob:pullback:1",
        native_state="REACTION_CONFIRMED",
    )
    ob = SimpleNamespace(
        timeframe="1h",
        ref=ref,
        identity="pullback:1",
        bullish=True,
        bottom=low,
        top=high,
        state="ACTIVE",
        interaction="REACTION_CONFIRMED",
    )
    return SimpleNamespace(
        **{
            **snapshot.__dict__,
            "order_block_behavior": SimpleNamespace(observations=(ob,)),
            "source_refs": tuple(snapshot.source_refs) + (ref,),
        }
    )


def test_breakout_acceptance_shadow_uses_confirmed_new_area_and_role_support():
    as_of = pd.Timestamp("2026-01-05 10:00")
    snapshot = _sr_snapshot(
        as_of,
        state="RANGE_BREAK_CONFIRMED",
        direction=1,
        boundary=100.0,
        price=104.0,
        role_support=(99.0, 100.0),
    )

    shadow = classify_executed_st_thesis(snapshot, _entry())

    assert shadow is not None
    assert shadow.family is STThesisFamily.BREAKOUT_ACCEPTANCE
    assert shadow.economic_mission is STEconomicMission.EXPAND_FROM_ACCEPTED_HIGHER_AREA
    assert shadow.initial_defended_anchor is not None
    assert shadow.initial_defended_anchor.kind is STDefendedAnchorKind.BREAKOUT_ROLE_SUPPORT
    assert (shadow.initial_defended_anchor.low, shadow.initial_defended_anchor.high) == (99.0, 100.0)
    assert shadow.initial_target_identity == "target:st:1"


def test_failed_sell_reclaim_shadow_requires_down_break_failure_and_reclaim():
    as_of = pd.Timestamp("2026-01-05 10:00")
    snapshot = _sr_snapshot(
        as_of,
        state="RANGE_BREAK_FAILED",
        direction=-1,
        boundary=96.0,
        price=97.0,
    )

    shadow = classify_executed_st_thesis(snapshot, _entry())

    assert shadow is not None
    assert shadow.family is STThesisFamily.FAILED_SELL_RECLAIM
    assert shadow.economic_mission is STEconomicMission.CAPTURE_FAILED_SELL_RECLAIM
    assert shadow.initial_defended_anchor is not None
    assert shadow.initial_defended_anchor.kind is STDefendedAnchorKind.FAILED_SELL_RECLAIM_LEVEL
    assert shadow.initial_defended_anchor.low == 96.0
    assert shadow.initial_defended_anchor.high == 96.0


def test_pullback_continuation_requires_existing_long_context_and_confirmed_1h_buyer_regain():
    as_of = pd.Timestamp("2026-01-05 10:00")
    base = SimpleNamespace(
        symbol="TEST",
        as_of=as_of,
        current_price=102.0,
        support_resistance=None,
        order_block_behavior=None,
        fvg_engulfing_lifecycle=None,
        source_refs=(),
    )
    snapshot = _with_confirmed_ob(base)

    shadow = classify_executed_st_thesis(
        snapshot,
        _entry(kind=ScenarioKind.CONTINUATION),
    )

    assert shadow is not None
    assert shadow.family is STThesisFamily.PULLBACK_CONTINUATION
    assert shadow.economic_mission is STEconomicMission.CONTINUE_AFTER_BUYER_REGAIN
    assert shadow.initial_defended_anchor is not None
    assert shadow.initial_defended_anchor.kind is STDefendedAnchorKind.REACTION_ZONE
    assert (shadow.initial_defended_anchor.low, shadow.initial_defended_anchor.high) == (98.0, 100.0)


def test_ambiguous_family_evidence_stays_unresolved():
    as_of = pd.Timestamp("2026-01-05 10:00")
    breakout = _sr_snapshot(
        as_of,
        state="RANGE_BREAK_CONFIRMED",
        direction=1,
        boundary=100.0,
        price=104.0,
        role_support=(99.0, 100.0),
    )
    snapshot = _with_confirmed_ob(breakout, low=99.0, high=100.0)

    shadow = classify_executed_st_thesis(
        snapshot,
        _entry(kind=ScenarioKind.CONTINUATION),
    )

    assert shadow is not None
    assert shadow.family is STThesisFamily.UNRESOLVED
    assert shadow.economic_mission is STEconomicMission.UNRESOLVED
    assert shadow.initial_defended_anchor is None
    assert shadow.reasons == (
        "ST_THESIS_AMBIGUOUS_FAMILIES:BREAKOUT_ACCEPTANCE,PULLBACK_CONTINUATION",
    )


def test_future_unavailable_evidence_cannot_resolve_thesis():
    as_of = pd.Timestamp("2026-01-05 10:00")
    future_ref = _ref(
        as_of,
        available_at=as_of + pd.Timedelta(minutes=30),
    )
    snapshot = _sr_snapshot(
        as_of,
        state="RANGE_BREAK_CONFIRMED",
        direction=1,
        boundary=100.0,
        price=104.0,
        role_support=(99.0, 100.0),
        ref=future_ref,
    )

    shadow = classify_executed_st_thesis(snapshot, _entry())

    assert shadow is not None
    assert shadow.family is STThesisFamily.UNRESOLVED
    assert shadow.reasons == ("ST_THESIS_CAUSAL_EVIDENCE_INSUFFICIENT",)


@dataclass(frozen=True)
class _ReplaySnapshot:
    symbol: str
    as_of: pd.Timestamp
    current_price: float
    support_resistance: object
    source_refs: tuple
    order_block_behavior: object | None = None
    fvg_engulfing_lifecycle: object | None = None

    def entry_decision(self, *, config=None, execution_event=None):
        return _entry()

    def position_exit_decision(self, state, *, execution_event=None):
        raise AssertionError("single-bar cold replay must not evaluate OPEN exit")


def _entry_event(as_of):
    return ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=StructuralDirection.LONG,
        timeframe="30m",
        observed_at=as_of,
        available_at=as_of,
        reason="ENTRY_CONFIRMED",
        source_refs=(),
    )


def test_cold_replay_shadow_is_deterministic_and_cannot_change_canonical_action():
    as_of = pd.Timestamp("2026-01-05 10:00")
    source = _sr_snapshot(
        as_of,
        state="RANGE_BREAK_CONFIRMED",
        direction=1,
        boundary=100.0,
        price=104.0,
        role_support=(99.0, 100.0),
    )
    snapshot = _ReplaySnapshot(
        symbol=source.symbol,
        as_of=source.as_of,
        current_price=source.current_price,
        support_resistance=source.support_resistance,
        source_refs=source.source_refs,
    )
    events = {as_of: _entry_event(as_of)}

    first = replay_canonical_trade_lifecycle((snapshot,), entry_execution_events=events)
    second = replay_canonical_trade_lifecycle((snapshot,), entry_execution_events=events)
    canonical_actions = tuple(row.action for row in first.rows)

    first_report = audit_st_thesis_identity_shadow(first)
    second_report = audit_st_thesis_identity_shadow(second)

    assert first_report == second_report
    assert tuple(row.action for row in first.rows) == canonical_actions == (DecisionAction.BUY,)
    assert first.rows[0].current_state == second.rows[0].current_state
    assert first_report.coverage.executed_st_entries == 1
    assert first_report.coverage.resolved_entries == 1
    assert first_report.coverage.unresolved_entries == 0
    assert dict(first_report.coverage.family_counts)[STThesisFamily.BREAKOUT_ACCEPTANCE] == 1
