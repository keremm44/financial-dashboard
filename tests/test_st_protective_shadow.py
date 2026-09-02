from types import SimpleNamespace

import pandas as pd

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.context.projections import (
    StructuralEventProjection,
    StructuralFactsProjection,
    StructuralScopeProjection,
    StructuralTimeframeProjection,
)
from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.exit import assess_position_exit_decision
from financial_dashboard.decision.lifecycle import ExitStage, PositionState, TradeLifecycleState
from financial_dashboard.decision.position_metadata import (
    PositionEntryMetadata,
    STInitialDefendedAnchor,
    STInitialTargetContext,
    STTradeMemory,
)
from financial_dashboard.decision.scenario import ScenarioKind
from financial_dashboard.decision.st_protective import (
    STProtectiveShadowState,
    STProtectiveTimingRelation,
    assess_st_protective_shadow,
)
from financial_dashboard.decision.st_thesis_identity import (
    STDefendedAnchorKind,
    STEconomicMission,
    STThesisFamily,
)
from financial_dashboard.decision.structural import DecisionHorizon
from financial_dashboard.decision.target_path import TargetPathRole


ENTRY = pd.Timestamp("2026-01-05 10:00")
NOW = pd.Timestamp("2026-01-05 12:00")


def _ref(
    domain,
    *,
    native_id,
    timeframe="1h",
    confirmed_at=NOW,
    available_at=NOW,
    quality=ContextDataQuality.VALID,
):
    return FactRef(
        domain=domain,
        fact_type="TEST_FACT",
        symbol="TEST",
        timeframe=timeframe,
        native_id=native_id,
        native_state="TEST",
        origin_time=confirmed_at or NOW,
        confirmed_at=confirmed_at,
        available_at=available_at,
        lineage_id=native_id,
        causal_family=(
            CausalFamily.STRUCTURAL_LEVEL
            if domain in {ContextDomain.MARKET_STRUCTURE, ContextDomain.SUPPORT_RESISTANCE}
            else CausalFamily.IMPULSE
        ),
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=quality,
    )


def _structure(*, aggregate="BULLISH", downside=True, event_confirmed_at=NOW):
    external = StructuralScopeProjection(
        scope="EXTERNAL",
        state=aggregate,
        direction=1 if aggregate == "BULLISH" else -1 if aggregate == "BEARISH" else 0,
        protected_high=None,
        protected_low=99.0,
        weak_high=110.0,
        weak_low=None,
        strong_high_identity=1,
        strong_low_identity=2,
        protected_high_identity=0,
        protected_low_identity=2,
        weak_high_identity=1,
        weak_low_identity=0,
    )
    events = ()
    if downside:
        ref = _ref(
            ContextDomain.MARKET_STRUCTURE,
            native_id="downside-progress",
            confirmed_at=event_confirmed_at,
            available_at=NOW,
        )
        events = (
            StructuralEventProjection(
                ref=ref,
                scope="EXTERNAL",
                event_type="BOS",
                direction=-1,
                broken_level=99.5,
                origin_price=101.0,
                confirmation_status="CONFIRMED",
                validity="VALID",
                relevance="CURRENT",
                outcome="CONTINUATION",
                bos_maturity="CONFIRMED",
            ),
        )
    row = StructuralTimeframeProjection(
        timeframe="1h",
        as_of=NOW,
        data_quality=ContextDataQuality.VALID,
        external=external,
        internal=None,
        events=events,
    )
    return StructuralFactsProjection(
        symbol="TEST",
        timeframes=("1h",),
        timeframe_facts=(row,),
    )


def _failed_bullish_fvg(*, identity="buyer-zone", quality=ContextDataQuality.VALID):
    ref = _ref(ContextDomain.FVG, native_id=f"fvg:{identity}", quality=quality)
    item = SimpleNamespace(
        ref=ref,
        identity=identity,
        direction=1,
        lower_boundary=99.0,
        upper_boundary=100.0,
        failed_reaction=True,
        invalid=False,
        full_fill=False,
        reaction_confirmed=False,
        first_test_index=20,
    )
    return SimpleNamespace(fvg=(item,), engulfing=())


def _sr_range(*, anchor_low=99.0, anchor_high=100.0, valid=True, location="INSIDE_RANGE"):
    ref = _ref(
        ContextDomain.SUPPORT_RESISTANCE,
        native_id="sr:current",
        quality=ContextDataQuality.VALID if valid else ContextDataQuality.UNAVAILABLE,
    )
    row = SimpleNamespace(
        timeframe="1h",
        ref=ref,
        state="RANGE_ACTIVE",
        range_identity=12,
        price_location=location,
        role_reversal_support_low=anchor_low,
        role_reversal_support_high=anchor_high,
    )
    return SimpleNamespace(timeframe_facts=(row,))


def _memory(family, *, anchor_identity, low=99.0, high=100.0):
    mission = {
        STThesisFamily.PULLBACK_CONTINUATION: STEconomicMission.CONTINUE_AFTER_BUYER_REGAIN,
        STThesisFamily.BREAKOUT_ACCEPTANCE: STEconomicMission.EXPAND_FROM_ACCEPTED_HIGHER_AREA,
        STThesisFamily.FAILED_SELL_RECLAIM: STEconomicMission.CAPTURE_FAILED_SELL_RECLAIM,
    }[family]
    kind = {
        STThesisFamily.PULLBACK_CONTINUATION: STDefendedAnchorKind.REACTION_ZONE,
        STThesisFamily.BREAKOUT_ACCEPTANCE: STDefendedAnchorKind.BREAKOUT_ROLE_SUPPORT,
        STThesisFamily.FAILED_SELL_RECLAIM: STDefendedAnchorKind.FAILED_SELL_RECLAIM_LEVEL,
    }[family]
    return STTradeMemory(
        thesis_family=family,
        economic_mission=mission,
        initial_defended_anchor=STInitialDefendedAnchor(
            kind=kind,
            identity=anchor_identity,
            timeframe="1h",
            low=low,
            high=high,
        ),
        initial_target_context=STInitialTargetContext(
            identity="target:1",
            low=110.0,
            high=112.0,
            anchor_price=111.0,
            roles=(TargetPathRole.OBJECTIVE,),
        ),
    )


def _state(family, *, anchor_identity, low=99.0, high=100.0):
    metadata = PositionEntryMetadata(
        symbol="TEST",
        entry_horizon=DecisionHorizon.SHORT_TERM,
        scenario_kind=ScenarioKind.SHORT_TERM_STANDALONE,
        entry_as_of=ENTRY,
        entry_price=101.0,
        active_target_identity="target:1",
        execution_timeframe="30m",
        execution_observed_at=ENTRY,
        execution_available_at=ENTRY,
        execution_reason="ENTRY_CONFIRMED",
        source_lineage=(),
        st_trade_memory=_memory(
            family,
            anchor_identity=anchor_identity,
            low=low,
            high=high,
        ),
    )
    return TradeLifecycleState(
        position=PositionState.OPEN,
        exit_stage=ExitStage.MONITOR,
        trade_id="trade:1",
        entry_as_of=ENTRY,
        entry_metadata=metadata,
    )


class _Snapshot(SimpleNamespace):
    def quality_for_timeframe(self, timeframe):
        return ContextDataQuality.VALID


def _snapshot(
    *,
    price,
    structure=None,
    reaction=None,
    support_resistance=None,
    volatility_environment=None,
):
    return _Snapshot(
        symbol="TEST",
        as_of=NOW,
        current_price=price,
        structure=structure or _structure(),
        support_resistance=support_resistance,
        order_block_behavior=None,
        fvg_engulfing_lifecycle=reaction,
        participation_behavior=None,
        pattern_behavior=None,
        volatility_environment=volatility_environment,
    )


def test_pullback_invalidation_can_shadow_before_full_bearish_structure_without_changing_canonical_action():
    state = _state(
        STThesisFamily.PULLBACK_CONTINUATION,
        anchor_identity="FVG:pullback-zone",
    )
    snapshot = _snapshot(
        price=98.5,
        reaction=_failed_bullish_fvg(identity="pullback-zone"),
    )

    canonical = assess_position_exit_decision(snapshot, state)
    shadow = assess_st_protective_shadow(
        snapshot,
        state,
        canonical_stage=canonical.stage,
    )

    assert canonical.action is DecisionAction.HOLD
    assert canonical.stage is ExitStage.MONITOR
    assert canonical.execution_event_consumed is False
    assert shadow.state is STProtectiveShadowState.PROTECTIVE_INTENT
    assert shadow.timing_relation is STProtectiveTimingRelation.SHADOW_EARLIER
    assert shadow.reasons == ("ST_PULLBACK_CONTINUATION_INVALIDATED",)
    assert set(shadow.primary_evidence) >= {
        "DEFENDED_GROUND_LOST",
        "ACCEPTANCE_BELOW_DEFENDED_GROUND",
        "BUYER_RECLAIM_FAILED",
        "SELLER_DOWNSIDE_PROGRESS",
    }


def test_breakout_acceptance_requires_old_range_reentry_in_addition_to_loss_reclaim_failure_and_downside_progress():
    state = _state(
        STThesisFamily.BREAKOUT_ACCEPTANCE,
        anchor_identity="SR_BREAKOUT:7",
    )
    snapshot = _snapshot(
        price=98.5,
        reaction=_failed_bullish_fvg(),
        support_resistance=_sr_range(),
    )

    shadow = assess_st_protective_shadow(snapshot, state, canonical_stage=ExitStage.MONITOR)

    assert shadow.state is STProtectiveShadowState.PROTECTIVE_INTENT
    assert shadow.reasons == ("ST_BREAKOUT_ACCEPTANCE_INVALIDATED",)
    assert "OLD_RANGE_REENTERED" in shadow.primary_evidence
    assert "BREAKOUT_FAILED_EXCURSION" in shadow.primary_evidence


def test_failed_sell_reclaim_has_its_own_loss_reclaim_failure_and_downside_progress_chain():
    state = _state(
        STThesisFamily.FAILED_SELL_RECLAIM,
        anchor_identity="SR_FAILED_SELL:7",
        low=100.0,
        high=100.0,
    )
    snapshot = _snapshot(
        price=99.0,
        reaction=_failed_bullish_fvg(),
    )

    shadow = assess_st_protective_shadow(snapshot, state, canonical_stage=ExitStage.MONITOR)

    assert shadow.state is STProtectiveShadowState.PROTECTIVE_INTENT
    assert shadow.reasons == ("ST_FAILED_SELL_RECLAIM_INVALIDATED",)
    assert shadow.timing_relation is STProtectiveTimingRelation.SHADOW_EARLIER


def test_single_secondary_reaction_failure_does_not_create_protective_intent_while_ground_is_intact():
    state = _state(
        STThesisFamily.PULLBACK_CONTINUATION,
        anchor_identity="FVG:pullback-zone",
    )
    snapshot = _snapshot(
        price=100.5,
        reaction=_failed_bullish_fvg(identity="pullback-zone"),
    )

    shadow = assess_st_protective_shadow(snapshot, state, canonical_stage=ExitStage.MONITOR)

    assert shadow.state is STProtectiveShadowState.NO_INTENT
    assert shadow.protective_intent is False
    assert shadow.primary_evidence == ()
    assert shadow.reasons == ("ST_PROTECTIVE_DEFENDED_GROUND_INTACT",)


def test_unknown_reclaim_evidence_is_not_promoted_to_confirmation():
    state = _state(
        STThesisFamily.FAILED_SELL_RECLAIM,
        anchor_identity="SR_FAILED_SELL:7",
        low=100.0,
        high=100.0,
    )
    snapshot = _snapshot(price=99.0, reaction=None)

    shadow = assess_st_protective_shadow(snapshot, state, canonical_stage=ExitStage.MONITOR)

    assert shadow.state is STProtectiveShadowState.UNRESOLVED
    assert shadow.protective_intent is False
    assert shadow.reasons == ("ST_PROTECTIVE_BUYER_RECLAIM_STATUS_UNRESOLVED",)


def test_pre_entry_downside_event_is_not_reused_as_new_protective_evidence():
    state = _state(
        STThesisFamily.FAILED_SELL_RECLAIM,
        anchor_identity="SR_FAILED_SELL:7",
        low=100.0,
        high=100.0,
    )
    snapshot = _snapshot(
        price=99.0,
        structure=_structure(event_confirmed_at=ENTRY - pd.Timedelta(minutes=30)),
        reaction=_failed_bullish_fvg(),
    )

    shadow = assess_st_protective_shadow(snapshot, state, canonical_stage=ExitStage.MONITOR)

    assert shadow.state is STProtectiveShadowState.NO_INTENT
    assert shadow.protective_intent is False
    assert shadow.reasons == ("ST_PROTECTIVE_DOWNSIDE_PROGRESS_NOT_CONFIRMED",)


def test_breakout_range_relation_unknown_fails_closed_instead_of_inventing_old_range_reentry():
    state = _state(
        STThesisFamily.BREAKOUT_ACCEPTANCE,
        anchor_identity="SR_BREAKOUT:7",
    )
    snapshot = _snapshot(
        price=98.5,
        reaction=_failed_bullish_fvg(),
        support_resistance=None,
    )

    shadow = assess_st_protective_shadow(snapshot, state, canonical_stage=ExitStage.MONITOR)

    assert shadow.state is STProtectiveShadowState.UNRESOLVED
    assert shadow.protective_intent is False
    assert shadow.reasons == ("ST_PROTECTIVE_BREAKOUT_RANGE_RELATION_UNRESOLVED",)


def test_environment_shock_alone_has_no_protective_authority():
    state = _state(
        STThesisFamily.PULLBACK_CONTINUATION,
        anchor_identity="FVG:pullback-zone",
    )
    snapshot = _snapshot(
        price=100.5,
        reaction=None,
        volatility_environment=SimpleNamespace(regime="SHOCK"),
    )

    shadow = assess_st_protective_shadow(snapshot, state, canonical_stage=ExitStage.MONITOR)

    assert shadow.state is STProtectiveShadowState.NO_INTENT
    assert shadow.primary_evidence == ()


def test_timing_report_can_show_canonical_exit_ready_before_shadow_without_changing_canonical_hold_gate():
    state = _state(
        STThesisFamily.FAILED_SELL_RECLAIM,
        anchor_identity="SR_FAILED_SELL:7",
        low=100.0,
        high=100.0,
    )
    snapshot = _snapshot(
        price=101.0,
        structure=_structure(aggregate="BEARISH", downside=True),
    )

    canonical = assess_position_exit_decision(snapshot, state)
    shadow = assess_st_protective_shadow(snapshot, state, canonical_stage=canonical.stage)

    assert canonical.stage is ExitStage.EXIT_READY
    assert canonical.action is DecisionAction.HOLD
    assert canonical.execution_event_consumed is False
    assert shadow.state is STProtectiveShadowState.NO_INTENT
    assert shadow.timing_relation is STProtectiveTimingRelation.CANONICAL_EARLIER
