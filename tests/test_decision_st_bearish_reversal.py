from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

import financial_dashboard.decision.st_bearish_reversal as bearish_reversal
from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.exit import _short_term_position_exit, refine_short_term_exit_with_stabil
from financial_dashboard.decision.lifecycle import ExitStage
from financial_dashboard.decision.participation import ParticipationState
from financial_dashboard.decision.st_bearish_reversal import (
    STBearishReversalState,
    assess_st_bearish_reversal,
    refine_short_term_exit_with_bearish_reversal,
)
from financial_dashboard.decision.stabil_authority import StabilDecisionAssessment, StabilDecisionState
from financial_dashboard.decision.structural import StructuralDirection, ThesisState
from financial_dashboard.decision.trade_exit import LongExitAssessment, PositionHealth


def _ref(label: str, as_of: pd.Timestamp):
    return SimpleNamespace(
        data_quality=ContextDataQuality.VALID,
        available_at=None,
        confirmed_at=as_of,
        deterministic_key=("TEST", label, as_of.isoformat()),
    )


def _event(direction: int, event_type: str, as_of: pd.Timestamp):
    return SimpleNamespace(
        scope="EXTERNAL",
        confirmation_status="CONFIRMED",
        validity="VALID",
        direction=direction,
        event_type=event_type,
        ref=_ref(f"{event_type}:{direction}", as_of),
    )


def _snapshot(*events, as_of: pd.Timestamp):
    row = SimpleNamespace(events=tuple(events))
    structure = SimpleNamespace(for_timeframe=lambda timeframe: row)
    return SimpleNamespace(
        as_of=as_of,
        current_price=100.0,
        structure=structure,
        order_block_behavior=None,
        fvg_engulfing_lifecycle=None,
        participation_behavior=None,
        stabil_support=None,
    )


def _native(*, direction=StructuralDirection.LONG, thesis_state=ThesisState.INTACT):
    return SimpleNamespace(
        data_quality=ContextDataQuality.VALID,
        direction=direction,
        thesis_state=thesis_state,
    )


def _patch_evidence(
    monkeypatch,
    *,
    reaction_confirmed: bool,
    reaction_developing: bool = False,
    participation_state: ParticipationState = ParticipationState.SUPPORTIVE,
    heavy_conflict: bool = False,
    stabil_breakdown_developing: bool = False,
    stabil_breakdown_confirmed: bool = False,
):
    monkeypatch.setattr(
        bearish_reversal,
        "normalize_decision_reaction_projections",
        lambda *_args, **_kwargs: (None, None),
    )
    monkeypatch.setattr(
        bearish_reversal,
        "assess_reaction",
        lambda *_args, **_kwargs: SimpleNamespace(
            confirmation_present=reaction_confirmed,
            developing_present=reaction_developing,
            source_refs=(),
        ),
    )
    monkeypatch.setattr(
        bearish_reversal,
        "assess_participation",
        lambda *_args, **_kwargs: SimpleNamespace(
            state=participation_state,
            heavy_conflict=heavy_conflict,
            source_refs=(),
        ),
    )
    monkeypatch.setattr(
        bearish_reversal,
        "assess_stabil_authority",
        lambda *_args, **_kwargs: SimpleNamespace(
            breakdown_developing=stabil_breakdown_developing,
            breakdown_confirmed=stabil_breakdown_confirmed,
            source_refs=(),
        ),
    )


def test_multi_family_bearish_reversal_arms_short_term_exit(monkeypatch) -> None:
    as_of = pd.Timestamp("2026-07-24 13:00:00+03:00")
    _patch_evidence(monkeypatch, reaction_confirmed=True)
    snapshot = _snapshot(_event(-1, "EVENT_CHOCH", as_of), as_of=as_of)

    reversal = assess_st_bearish_reversal(
        snapshot,
        _native(),
        reaction_relevance=None,
    )

    assert reversal.state is STBearishReversalState.STRONG
    assert reversal.can_arm_exit

    base = LongExitAssessment(
        ExitStage.MONITOR,
        PositionHealth.HEALTHY,
        ("ST_LONG_THESIS_INTACT",),
        (),
        (),
    )
    refined = refine_short_term_exit_with_bearish_reversal(base, reversal)
    assert refined.stage is ExitStage.EXIT_READY
    assert refined.waiting_for == ("FRESH_LONG_EXIT_EXECUTION_EVENT",)


def test_incomplete_bearish_reversal_does_not_change_position_stage(monkeypatch) -> None:
    as_of = pd.Timestamp("2026-07-24 13:00:00+03:00")
    _patch_evidence(
        monkeypatch,
        reaction_confirmed=True,
        participation_state=ParticipationState.NEUTRAL,
    )
    snapshot = _snapshot(_event(-1, "EVENT_CHOCH", as_of), as_of=as_of)

    reversal = assess_st_bearish_reversal(
        snapshot,
        _native(),
        reaction_relevance=None,
    )

    assert reversal.state is STBearishReversalState.DEVELOPING
    base = LongExitAssessment(ExitStage.MONITOR, PositionHealth.HEALTHY, (), (), ())
    refined = refine_short_term_exit_with_bearish_reversal(base, reversal)
    assert refined.stage is ExitStage.MONITOR
    assert refined.waiting_for == ()


def test_stabil_breakdown_can_confirm_but_is_not_required_when_participation_supports(monkeypatch) -> None:
    as_of = pd.Timestamp("2026-07-24 13:00:00+03:00")
    _patch_evidence(
        monkeypatch,
        reaction_confirmed=True,
        participation_state=ParticipationState.NEUTRAL,
        stabil_breakdown_developing=True,
    )
    snapshot = _snapshot(_event(-1, "EVENT_CHOCH", as_of), as_of=as_of)

    reversal = assess_st_bearish_reversal(
        snapshot,
        _native(),
        reaction_relevance=None,
    )

    assert reversal.state is STBearishReversalState.STRONG
    assert reversal.stabil_breakdown_supportive


def test_opposing_participation_blocks_early_exit_arm_even_with_stabil_breakdown(monkeypatch) -> None:
    as_of = pd.Timestamp("2026-07-24 13:00:00+03:00")
    _patch_evidence(
        monkeypatch,
        reaction_confirmed=True,
        participation_state=ParticipationState.OPPOSING,
        stabil_breakdown_confirmed=True,
    )
    snapshot = _snapshot(_event(-1, "EVENT_CHOCH", as_of), as_of=as_of)

    reversal = assess_st_bearish_reversal(
        snapshot,
        _native(),
        reaction_relevance=None,
    )

    assert reversal.state is STBearishReversalState.DEVELOPING
    assert not reversal.can_arm_exit


def test_bullish_reset_supersedes_older_bearish_choch(monkeypatch) -> None:
    bearish_time = pd.Timestamp("2026-07-24 12:00:00+03:00")
    bullish_time = pd.Timestamp("2026-07-24 13:00:00+03:00")
    _patch_evidence(monkeypatch, reaction_confirmed=True)
    snapshot = _snapshot(
        _event(-1, "EVENT_CHOCH", bearish_time),
        _event(1, "EVENT_CHOCH", bullish_time),
        as_of=bullish_time,
    )

    reversal = assess_st_bearish_reversal(
        snapshot,
        _native(),
        reaction_relevance=None,
    )

    assert reversal.state is STBearishReversalState.WATCH
    assert not reversal.current_bearish_choch
    assert not reversal.can_arm_exit


def test_native_short_transitioning_toward_long_remains_watch_not_exit_ready() -> None:
    short_term = SimpleNamespace(
        data_quality=ContextDataQuality.VALID,
        direction=StructuralDirection.SHORT,
        thesis_state=ThesisState.TRANSITIONING,
        transition_target=StructuralDirection.LONG,
        source_refs=(),
    )
    result = _short_term_position_exit(SimpleNamespace(short_term=short_term))

    assert result.stage is ExitStage.EXIT_WATCH
    assert result.waiting_for == ("ST_TRANSITION_TO_RESOLVE",)


def test_bullish_stabil_cannot_veto_a_strong_reversal_exit_arm() -> None:
    reversal = bearish_reversal.STBearishReversalAssessment(
        STBearishReversalState.STRONG,
        True,
        True,
        True,
        False,
        False,
        ("ST_BEARISH_REVERSAL_MULTI_FAMILY_CONFIRMED",),
        (),
    )
    base = LongExitAssessment(ExitStage.MONITOR, PositionHealth.HEALTHY, (), (), ())
    structural = refine_short_term_exit_with_bearish_reversal(base, reversal)
    stabil = StabilDecisionAssessment(
        StabilDecisionState.BULLISH_SUPPORTED,
        ContextDataQuality.VALID,
        ("TEST_STABIL",),
        (),
    )
    st = SimpleNamespace(direction=StructuralDirection.LONG, thesis_state=ThesisState.INTACT)

    refined = refine_short_term_exit_with_stabil(structural, st, stabil)

    assert refined.stage is ExitStage.EXIT_READY
    assert "STABIL_STILL_SUPPORTIVE_BUT_NO_EXIT_VETO:BULLISH_SUPPORTED" in refined.reasons
