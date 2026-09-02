from types import SimpleNamespace

import pandas as pd

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.context.participation_behavior_projection import (
    AbsorptionBehavior,
    BreakParticipationBehavior,
    EffortResultBehavior,
    ParticipationTrend,
)
from financial_dashboard.context.pattern_behavior_projection import PatternBehaviorPhase
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
from financial_dashboard.decision.st_economic_history import (
    STAcceptedAreaEvent,
    STContinuationEpisode,
    STContinuationEpisodeState,
    STEarnedDefenseEvent,
    STEconomicHistory,
    STMissionCompletionMilestone,
    STProgressEvent,
)
from financial_dashboard.decision.st_exit_intent import STExitFamily
from financial_dashboard.decision.st_harvest import (
    STHarvestShadowState,
    STHealthyBaseState,
    assess_st_harvest_shadow,
)
from financial_dashboard.decision.st_thesis_identity import (
    STDefendedAnchorKind,
    STEconomicMission,
    STThesisFamily,
)
from financial_dashboard.decision.structural import DecisionHorizon
from financial_dashboard.decision.target_path import TargetPathRole


ENTRY = pd.Timestamp("2026-01-05 10:00")
MISSION = pd.Timestamp("2026-01-05 12:00")
NOW = pd.Timestamp("2026-01-05 14:00")


def _ref(
    domain,
    *,
    native_id,
    confirmed_at=NOW,
    available_at=NOW,
    origin_time=None,
    quality=ContextDataQuality.VALID,
    timeframe="1h",
):
    causal_family = (
        CausalFamily.PARTICIPATION
        if domain is ContextDomain.VOLUME
        else CausalFamily.STRUCTURAL_LEVEL
        if domain in {ContextDomain.MARKET_STRUCTURE, ContextDomain.SUPPORT_RESISTANCE}
        else CausalFamily.IMPULSE
    )
    source_family = (
        SourceFamily.VOLUME_SERIES
        if domain is ContextDomain.VOLUME
        else SourceFamily.PRICE_GEOMETRY
    )
    return FactRef(
        domain=domain,
        fact_type="TEST_FACT",
        symbol="TEST",
        timeframe=timeframe,
        native_id=native_id,
        native_state="TEST",
        origin_time=confirmed_at if origin_time is None else origin_time,
        confirmed_at=confirmed_at,
        available_at=available_at,
        lineage_id=native_id,
        causal_family=causal_family,
        source_family=source_family,
        data_quality=quality,
    )


def _structure(*, downside=False, downside_level=98.5):
    external = StructuralScopeProjection(
        scope="EXTERNAL",
        state="BULLISH",
        direction=1,
        protected_high=None,
        protected_low=99.0,
        weak_high=115.0,
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
            native_id="downside:post-entry",
            confirmed_at=NOW,
        )
        events = (
            StructuralEventProjection(
                ref=ref,
                scope="EXTERNAL",
                event_type="BOS",
                direction=-1,
                broken_level=downside_level,
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


def _memory(
    family=STThesisFamily.PULLBACK_CONTINUATION,
    *,
    anchor_identity="FVG:entry-anchor",
):
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
    )


def _metadata(family=STThesisFamily.PULLBACK_CONTINUATION):
    return PositionEntryMetadata(
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
        st_trade_memory=_memory(family),
    )


def _mature_history(
    *,
    later_progress=False,
    episodes=(),
):
    accepted = [
        STAcceptedAreaEvent(
            event_id="area:mission",
            observed_at=MISSION,
            timeframe="1h",
            low=110.0,
            high=111.0,
            break_boundary=110.0,
        )
    ]
    defenses = [
        STEarnedDefenseEvent(
            event_id="defense:mission",
            observed_at=MISSION,
            accepted_area_id="area:mission",
            low=110.0,
            high=111.0,
        )
    ]
    progress = [
        STProgressEvent(
            event_id="progress:mission",
            observed_at=MISSION,
            accepted_area_id="area:mission",
            accepted_floor=110.0,
            distance_from_entry=9.0,
        )
    ]
    if later_progress:
        accepted.append(
            STAcceptedAreaEvent(
                event_id="area:later",
                observed_at=pd.Timestamp("2026-01-05 13:00"),
                timeframe="1h",
                low=113.0,
                high=114.0,
                break_boundary=113.0,
            )
        )
        defenses.append(
            STEarnedDefenseEvent(
                event_id="defense:later",
                observed_at=pd.Timestamp("2026-01-05 13:00"),
                accepted_area_id="area:later",
                low=113.0,
                high=114.0,
            )
        )
        progress.append(
            STProgressEvent(
                event_id="progress:later",
                observed_at=pd.Timestamp("2026-01-05 13:00"),
                accepted_area_id="area:later",
                accepted_floor=113.0,
                distance_from_entry=12.0,
            )
        )
    return STEconomicHistory(
        accepted_areas=tuple(accepted),
        earned_defenses=tuple(defenses),
        progress_events=tuple(progress),
        mission_completion=STMissionCompletionMilestone(
            event_id="mission:1",
            observed_at=MISSION,
            target_identity="target:1",
            accepted_area_id="area:mission",
        ),
        continuation_episodes=tuple(episodes),
    )


def _failed_episode(*, identity="failed:1", formed_at=None):
    formed = pd.Timestamp("2026-01-05 13:00") if formed_at is None else pd.Timestamp(formed_at)
    return STContinuationEpisode(
        episode_id=f"FVG:1h:{identity}",
        source_identity=identity,
        timeframe="1h",
        formed_at=formed,
        first_observed_at=formed,
        lower_boundary=110.0,
        upper_boundary=111.0,
        state=STContinuationEpisodeState.FAILED,
        completed_at=formed + pd.Timedelta(minutes=15),
    )


def _live_episode(*, identity="live:1", formed_at=None):
    formed = pd.Timestamp("2026-01-05 13:15") if formed_at is None else pd.Timestamp(formed_at)
    return STContinuationEpisode(
        episode_id=f"FVG:1h:{identity}",
        source_identity=identity,
        timeframe="1h",
        formed_at=formed,
        first_observed_at=formed,
        lower_boundary=110.0,
        upper_boundary=111.0,
        state=STContinuationEpisodeState.LIVE,
    )


def _state(history, family=STThesisFamily.PULLBACK_CONTINUATION):
    return TradeLifecycleState(
        position=PositionState.OPEN,
        exit_stage=ExitStage.MONITOR,
        trade_id="trade:1",
        entry_as_of=ENTRY,
        entry_metadata=_metadata(family),
        st_economic_history=history,
    )


def _fvg(
    *,
    identity,
    formed_at,
    lower=110.0,
    upper=111.0,
    reaction_confirmed=False,
    failed_reaction=False,
    first_test_index=20,
):
    ref = _ref(
        ContextDomain.FVG,
        native_id=f"fvg:{identity}:{NOW}",
        origin_time=pd.Timestamp(formed_at),
    )
    item = SimpleNamespace(
        ref=ref,
        identity=identity,
        direction=1,
        lower_boundary=lower,
        upper_boundary=upper,
        first_test_index=first_test_index,
        reaction_confirmed=reaction_confirmed,
        failed_reaction=failed_reaction,
        full_fill=False,
        invalid=False,
    )
    return SimpleNamespace(fvg=(item,), engulfing=())


def _participation(*, controlled=False, weak=False):
    ref = _ref(ContextDomain.VOLUME, native_id=f"participation:{'weak' if weak else 'ok'}")
    row = SimpleNamespace(
        timeframe="1h",
        ref=ref,
        status="LOW_PARTICIPATION" if weak else "OK",
        participation_trend=ParticipationTrend.FADING if weak else ParticipationTrend.NONE,
        effort_result=EffortResultBehavior.NEUTRAL,
        absorption=AbsorptionBehavior.NONE,
        break_participation=BreakParticipationBehavior.NONE,
        participation_direction=0,
        evidence_direction=0,
        break_direction=0,
        heavy_conflict=False,
        controlled_pullback=False,
        controlled_reaction=controlled,
    )
    return SimpleNamespace(for_timeframe=lambda timeframe: row)


def _pattern(*, phase=PatternBehaviorPhase.MATURE_COMPRESSION, direction=1):
    ref = _ref(ContextDomain.PATTERN, native_id=f"pattern:{phase.value}")
    row = SimpleNamespace(
        timeframe="1h",
        ref=ref,
        phase=phase,
        classic_direction=direction,
    )
    return SimpleNamespace(timeframe_facts=(row,))


class _Snapshot(SimpleNamespace):
    def quality_for_timeframe(self, timeframe):
        return ContextDataQuality.VALID


def _snapshot(
    *,
    price=111.0,
    structure=None,
    fvg=None,
    participation=None,
    pattern=None,
):
    return _Snapshot(
        symbol="TEST",
        as_of=NOW,
        current_price=price,
        structure=structure or _structure(),
        support_resistance=None,
        order_block_behavior=None,
        fvg_engulfing_lifecycle=fvg,
        participation_behavior=participation,
        pattern_behavior=pattern,
        source_refs=(),
    )


def test_initial_mission_still_active_is_hold_and_not_mature():
    state = _state(STEconomicHistory())
    result = assess_st_harvest_shadow(_snapshot(price=105.0), state)

    assert result.state is STHarvestShadowState.HOLD_MISSION_ACTIVE
    assert result.mature is False
    assert result.consumed is False


def test_post_mission_real_progress_is_not_consumed_without_later_failed_continuation():
    history = _mature_history(later_progress=True)
    state = _state(history)
    result = assess_st_harvest_shadow(_snapshot(price=114.5), state)

    assert result.state is STHarvestShadowState.HOLD_PROGRESS
    assert result.mature is True
    assert result.consumed is False
    assert any(item.startswith("POST_MISSION_PROGRESS:") for item in result.primary_evidence)


def test_full_consumed_story_becomes_canonical_profit_harvest_but_waits_for_execution():
    failed = _failed_episode(identity="failed:consumed")
    history = _mature_history(episodes=(failed,))
    state = _state(history)
    snapshot = _snapshot(
        price=111.0,
        fvg=_fvg(
            identity="failed:consumed",
            formed_at=failed.formed_at,
            failed_reaction=True,
        ),
    )

    shadow = assess_st_harvest_shadow(snapshot, state)
    canonical = assess_position_exit_decision(snapshot, state)

    assert shadow.state is STHarvestShadowState.PROFIT_HARVEST
    assert shadow.consumed is True
    assert shadow.mature is True
    assert shadow.healthy_base is False
    assert canonical.action is DecisionAction.HOLD
    assert canonical.stage is ExitStage.EXIT_READY
    assert canonical.economic_exit_family is STExitFamily.PROFIT_HARVEST
    assert canonical.execution_event_consumed is False


def test_live_post_progress_continuation_cannot_be_harvested_before_it_resolves():
    live = _live_episode(identity="live:unresolved", formed_at="2026-01-05 13:00")
    history = _mature_history(episodes=(live,))
    state = _state(history)

    result = assess_st_harvest_shadow(_snapshot(price=111.0), state)

    assert result.state is STHarvestShadowState.HOLD_CONTINUATION
    assert result.consumed is False
    assert result.mature is True


def test_real_healthy_base_suspends_harvest_without_resetting_maturity():
    failed = _failed_episode(identity="failed:before-base", formed_at="2026-01-05 12:30")
    live = _live_episode(identity="live:base", formed_at="2026-01-05 13:15")
    history = _mature_history(episodes=(failed, live))
    state = _state(history)
    snapshot = _snapshot(
        price=111.0,
        fvg=_fvg(
            identity="live:base",
            formed_at=live.formed_at,
            reaction_confirmed=True,
        ),
        participation=_participation(controlled=True),
    )

    result = assess_st_harvest_shadow(snapshot, state)

    assert result.state is STHarvestShadowState.HOLD_HEALTHY_BASE
    assert result.healthy_base_state is STHealthyBaseState.CONFIRMED
    assert result.healthy_base is True
    assert result.mature is True
    assert result.consumed is False
    assert state.st_economic_history == history


def test_pattern_supported_healthy_base_is_positive_preparation_not_merely_unbroken_price():
    failed = _failed_episode(identity="failed:before-pattern-base", formed_at="2026-01-05 12:30")
    history = _mature_history(episodes=(failed,))
    state = _state(history)
    snapshot = _snapshot(
        price=111.0,
        fvg=_fvg(
            identity="reaction:pre-mission",
            formed_at="2026-01-05 11:00",
            reaction_confirmed=True,
        ),
        participation=_participation(controlled=True),
        pattern=_pattern(),
    )

    result = assess_st_harvest_shadow(snapshot, state)

    assert result.state is STHarvestShadowState.HOLD_HEALTHY_BASE
    assert result.mature is True
    assert result.healthy_base is True
    assert any(
        item.startswith("HEALTHY_BASE_PATTERN_PREPARATION:")
        for item in result.supporting_evidence
    )


def test_merely_protecting_earned_area_does_not_automatically_create_healthy_base():
    history = _mature_history()
    state = _state(history)
    snapshot = _snapshot(
        price=111.0,
        fvg=_fvg(
            identity="inactive:reaction",
            formed_at="2026-01-05 11:00",
            reaction_confirmed=False,
            failed_reaction=False,
            first_test_index=None,
        ),
        participation=_participation(controlled=True),
        pattern=_pattern(),
    )

    result = assess_st_harvest_shadow(snapshot, state)

    assert result.healthy_base_state is STHealthyBaseState.ABSENT
    assert result.state is STHarvestShadowState.HOLD_UNCERTAIN
    assert result.consumed is False


def test_unknown_healthy_base_evidence_cannot_be_interpreted_as_absence_for_harvest():
    failed = _failed_episode(identity="failed:unknown-base")
    history = _mature_history(episodes=(failed,))
    state = _state(history)
    result = assess_st_harvest_shadow(_snapshot(price=111.0), state)

    assert result.state is STHarvestShadowState.HOLD_UNCERTAIN
    assert result.healthy_base_state is STHealthyBaseState.UNRESOLVED
    assert result.consumed is False


def test_one_participation_weakness_alone_does_not_create_harvest():
    history = _mature_history()
    state = _state(history)
    result = assess_st_harvest_shadow(
        _snapshot(
            price=111.0,
            fvg=_fvg(
                identity="reaction:old",
                formed_at="2026-01-05 11:00",
                reaction_confirmed=True,
            ),
            participation=_participation(controlled=True, weak=True),
            pattern=_pattern(),
        ),
        state,
    )

    assert result.state is STHarvestShadowState.HOLD_UNCERTAIN
    assert result.consumed is False


def test_healthy_base_disappearing_does_not_reset_maturity_or_make_trade_new_again():
    live = _live_episode(identity="base:lifecycle", formed_at="2026-01-05 13:00")
    healthy_history = _mature_history(episodes=(live,))
    state = _state(healthy_history)
    healthy_snapshot = _snapshot(
        price=111.0,
        fvg=_fvg(
            identity="base:lifecycle",
            formed_at=live.formed_at,
            reaction_confirmed=True,
        ),
        participation=_participation(controlled=True),
    )
    healthy = assess_st_harvest_shadow(healthy_snapshot, state)
    assert healthy.state is STHarvestShadowState.HOLD_HEALTHY_BASE
    assert healthy.mature is True

    failed_live = STContinuationEpisode(
        episode_id=live.episode_id,
        source_identity=live.source_identity,
        timeframe=live.timeframe,
        formed_at=live.formed_at,
        first_observed_at=live.first_observed_at,
        lower_boundary=live.lower_boundary,
        upper_boundary=live.upper_boundary,
        state=STContinuationEpisodeState.FAILED,
        completed_at=pd.Timestamp("2026-01-05 13:45"),
    )
    failed_history = _mature_history(episodes=(failed_live,))
    failed_state = _state(failed_history)
    failed_snapshot = _snapshot(
        price=111.0,
        fvg=_fvg(
            identity="base:lifecycle",
            formed_at=live.formed_at,
            failed_reaction=True,
        ),
    )
    after = assess_st_harvest_shadow(failed_snapshot, failed_state)

    assert after.state is STHarvestShadowState.PROFIT_HARVEST
    assert after.mature is True
    assert after.healthy_base is False
    assert not hasattr(failed_state.st_economic_history, "mature")
    assert not hasattr(failed_state.st_economic_history, "healthy_base")
    assert not hasattr(failed_state.st_economic_history, "consumed")


def test_protective_invalidation_always_outranks_consumed_harvest_story():
    failed = _failed_episode(identity="failed:harvest-ready")
    history = _mature_history(episodes=(failed,))
    state = _state(history)
    snapshot = _snapshot(
        price=98.5,
        structure=_structure(downside=True, downside_level=99.0),
        fvg=_fvg(
            identity="entry-anchor",
            formed_at="2026-01-05 11:00",
            lower=99.0,
            upper=100.0,
            failed_reaction=True,
        ),
    )

    result = assess_st_harvest_shadow(snapshot, state)

    assert result.state is STHarvestShadowState.PROTECTIVE_PRECEDENCE
    assert result.protective_precedence is True
    assert result.consumed is False
