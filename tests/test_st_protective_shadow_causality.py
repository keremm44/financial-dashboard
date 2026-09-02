from types import SimpleNamespace

import pandas as pd

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
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


def _ref(domain, native_id, *, confirmed_at=NOW):
    return FactRef(
        domain=domain,
        fact_type="TEST",
        symbol="TEST",
        timeframe="1h",
        native_id=native_id,
        native_state="TEST",
        origin_time=confirmed_at,
        confirmed_at=confirmed_at,
        available_at=NOW,
        lineage_id=native_id,
        causal_family=CausalFamily.STRUCTURAL_LEVEL,
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=ContextDataQuality.VALID,
    )


def _state():
    memory = STTradeMemory(
        thesis_family=STThesisFamily.FAILED_SELL_RECLAIM,
        economic_mission=STEconomicMission.CAPTURE_FAILED_SELL_RECLAIM,
        initial_defended_anchor=STInitialDefendedAnchor(
            kind=STDefendedAnchorKind.FAILED_SELL_RECLAIM_LEVEL,
            identity="SR_FAILED_SELL:7",
            timeframe="1h",
            low=100.0,
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
        st_trade_memory=memory,
    )
    return TradeLifecycleState(
        position=PositionState.OPEN,
        exit_stage=ExitStage.MONITOR,
        trade_id="trade:1",
        entry_as_of=ENTRY,
        entry_metadata=metadata,
    )


def _structure():
    ref = _ref(ContextDomain.MARKET_STRUCTURE, "downside")
    event = SimpleNamespace(
        ref=ref,
        direction=-1,
        confirmation_status="CONFIRMED",
        validity="VALID",
        relevance="CURRENT",
        broken_level=99.5,
    )
    row = SimpleNamespace(
        timeframe="1h",
        data_quality=ContextDataQuality.VALID,
        events=(event,),
    )
    return SimpleNamespace(timeframe_facts=(row,))


def _snapshot(fvg):
    return SimpleNamespace(
        as_of=NOW,
        current_price=99.0,
        structure=_structure(),
        support_resistance=None,
        order_block_behavior=None,
        fvg_engulfing_lifecycle=SimpleNamespace(fvg=(fvg,), engulfing=()),
        participation_behavior=None,
        pattern_behavior=None,
    )


def _failed_fvg(*, confirmed_at, low, high, identity):
    return SimpleNamespace(
        ref=_ref(ContextDomain.FVG, f"fvg:{identity}", confirmed_at=confirmed_at),
        identity=identity,
        direction=1,
        lower_boundary=low,
        upper_boundary=high,
        failed_reaction=True,
        full_fill=False,
        invalid=False,
        reaction_confirmed=False,
    )


def test_pre_entry_reclaim_failure_cannot_be_reused_as_current_protective_evidence():
    fvg = _failed_fvg(
        confirmed_at=ENTRY - pd.Timedelta(minutes=30),
        low=99.5,
        high=100.5,
        identity="old-failure",
    )

    shadow = assess_st_protective_shadow(_snapshot(fvg), _state())

    assert shadow.state is STProtectiveShadowState.UNRESOLVED
    assert shadow.protective_intent is False
    assert shadow.reasons == ("ST_PROTECTIVE_BUYER_RECLAIM_STATUS_UNRESOLVED",)


def test_spatially_unrelated_reaction_failure_cannot_substitute_for_anchor_reclaim_failure():
    fvg = _failed_fvg(
        confirmed_at=NOW,
        low=105.0,
        high=106.0,
        identity="unrelated-failure",
    )

    shadow = assess_st_protective_shadow(_snapshot(fvg), _state())

    assert shadow.state is STProtectiveShadowState.UNRESOLVED
    assert shadow.protective_intent is False
    assert shadow.reasons == ("ST_PROTECTIVE_BUYER_RECLAIM_STATUS_UNRESOLVED",)
