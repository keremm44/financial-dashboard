from __future__ import annotations

from datetime import datetime, timezone

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.context.projections import (
    StructuralFactsProjection,
    StructuralScopeProjection,
    StructuralTimeframeProjection,
)
from financial_dashboard.decision.structural import (
    HorizonRelation,
    StructuralDirection,
    ThesisState,
    assess_long_term_structure,
    assess_short_term_structure,
    build_horizon_structural_snapshot,
)


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def _scope(state: str, direction: int) -> StructuralScopeProjection:
    return StructuralScopeProjection(
        scope="EXTERNAL",
        state=state,
        direction=direction,
        protected_high=110.0 if direction <= 0 else None,
        protected_low=90.0 if direction >= 0 else None,
        weak_high=120.0 if direction >= 0 else None,
        weak_low=80.0 if direction <= 0 else None,
        strong_high_identity=0,
        strong_low_identity=0,
        protected_high_identity=1 if direction <= 0 else 0,
        protected_low_identity=1 if direction >= 0 else 0,
        weak_high_identity=2 if direction >= 0 else 0,
        weak_low_identity=2 if direction <= 0 else 0,
    )


def _row(
    timeframe: str,
    state: str,
    direction: int,
    *,
    quality: ContextDataQuality = ContextDataQuality.VALID,
) -> StructuralTimeframeProjection:
    return StructuralTimeframeProjection(
        timeframe=timeframe,
        as_of=NOW,
        data_quality=quality,
        external=_scope(state, direction),
        internal=None,
        events=(),
    )


def _projection(*rows: StructuralTimeframeProjection) -> StructuralFactsProjection:
    return StructuralFactsProjection(
        symbol="ASELS",
        timeframes=tuple(row.timeframe for row in rows),
        timeframe_facts=rows,
    )


def test_long_term_uses_daily_structure_as_primary_authority() -> None:
    structural = _projection(
        _row("1d", "STATE_BULLISH", 1),
        _row("4h", "STATE_BEARISH", -1),
        _row("1h", "STATE_BEARISH", -1),
        _row("30m", "STATE_BEARISH", -1),
    )

    result = assess_long_term_structure(structural)

    assert result.authority_timeframe == "1d"
    assert result.direction is StructuralDirection.LONG
    assert result.thesis_state is ThesisState.INTACT
    assert result.secondary_native_state == "STATE_BEARISH"


def test_short_term_uses_1h_only_and_30m_cannot_flip_direction() -> None:
    structural = _projection(
        _row("1h", "STATE_BULLISH", 1),
        _row("30m", "STATE_BEARISH", -1),
    )

    result = assess_short_term_structure(structural)

    assert result.authority_timeframe == "1h"
    assert result.direction is StructuralDirection.LONG
    assert result.thesis_state is ThesisState.INTACT


def test_missing_1h_does_not_promote_2h_to_short_term_authority() -> None:
    structural = _projection(
        _row("2h", "STATE_BULLISH", 1),
        _row("30m", "STATE_BULLISH", 1),
    )

    result = assess_short_term_structure(structural)

    assert result.direction is StructuralDirection.UNRESOLVED
    assert result.thesis_state is ThesisState.UNRESOLVED
    assert result.authority_timeframe == "1h"


def test_non_valid_critical_structure_fails_closed() -> None:
    structural = _projection(
        _row("1d", "STATE_BULLISH", 1, quality=ContextDataQuality.DATA_LIMITED),
        _row("1h", "STATE_BEARISH", -1, quality=ContextDataQuality.WARMING_UP),
    )

    lt = assess_long_term_structure(structural)
    st = assess_short_term_structure(structural)

    assert lt.direction is StructuralDirection.UNRESOLVED
    assert lt.thesis_state is ThesisState.UNRESOLVED
    assert st.direction is StructuralDirection.UNRESOLVED
    assert st.thesis_state is ThesisState.UNRESOLVED


def test_native_transition_keeps_established_side_until_structure_confirms_reversal() -> None:
    structural = _projection(
        _row("1d", "STATE_TRANSITION_DOWN", 0),
        _row("1h", "STATE_TRANSITION_UP", 0),
    )

    lt = assess_long_term_structure(structural)
    st = assess_short_term_structure(structural)

    assert lt.direction is StructuralDirection.LONG
    assert lt.thesis_state is ThesisState.TRANSITIONING
    assert lt.transition_target is StructuralDirection.SHORT
    assert st.direction is StructuralDirection.SHORT
    assert st.thesis_state is ThesisState.TRANSITIONING
    assert st.transition_target is StructuralDirection.LONG


def test_4h_opposite_transition_marks_lt_transitioning_without_flipping_lt_direction() -> None:
    structural = _projection(
        _row("1d", "STATE_BULLISH", 1),
        _row("4h", "STATE_TRANSITION_DOWN", 0),
        _row("1h", "STATE_BEARISH", -1),
    )

    result = build_horizon_structural_snapshot(structural)

    assert result.long_term.direction is StructuralDirection.LONG
    assert result.long_term.thesis_state is ThesisState.TRANSITIONING
    assert result.long_term.transition_target is StructuralDirection.SHORT
    assert result.short_term.direction is StructuralDirection.SHORT
    assert result.short_term.thesis_state is ThesisState.INTACT
    assert result.relation is HorizonRelation.EARLY_TRANSITION


def test_intact_opposite_lt_st_is_counter_reaction_not_thesis_flip() -> None:
    structural = _projection(
        _row("1d", "STATE_BULLISH", 1),
        _row("4h", "STATE_BULLISH", 1),
        _row("1h", "STATE_BEARISH", -1),
    )

    result = build_horizon_structural_snapshot(structural)

    assert result.long_term.direction is StructuralDirection.LONG
    assert result.long_term.thesis_state is ThesisState.INTACT
    assert result.short_term.direction is StructuralDirection.SHORT
    assert result.short_term.thesis_state is ThesisState.INTACT
    assert result.relation is HorizonRelation.COUNTER_REACTION


def test_counter_side_transitioning_back_to_lt_is_classified_as_pullback() -> None:
    structural = _projection(
        _row("1d", "STATE_BULLISH", 1),
        _row("4h", "STATE_BULLISH", 1),
        _row("1h", "STATE_TRANSITION_UP", 0),
    )

    result = build_horizon_structural_snapshot(structural)

    assert result.long_term.direction is StructuralDirection.LONG
    assert result.short_term.direction is StructuralDirection.SHORT
    assert result.short_term.thesis_state is ThesisState.TRANSITIONING
    assert result.short_term.transition_target is StructuralDirection.LONG
    assert result.relation is HorizonRelation.PULLBACK


def test_lt_unresolved_does_not_invalidate_valid_short_term_thesis() -> None:
    structural = _projection(
        _row("1d", "STATE_NEUTRAL", 0),
        _row("1h", "STATE_BULLISH", 1),
    )

    result = build_horizon_structural_snapshot(structural)

    assert result.long_term.direction is StructuralDirection.UNRESOLVED
    assert result.short_term.direction is StructuralDirection.LONG
    assert result.short_term.thesis_state is ThesisState.INTACT
    assert result.relation is HorizonRelation.LT_UNRESOLVED


def test_30m_missing_does_not_change_valid_lt_or_st_structure() -> None:
    structural = _projection(
        _row("1d", "STATE_BULLISH", 1),
        _row("4h", "STATE_BULLISH", 1),
        _row("1h", "STATE_BULLISH", 1),
    )

    result = build_horizon_structural_snapshot(structural)

    assert result.long_term.direction is StructuralDirection.LONG
    assert result.short_term.direction is StructuralDirection.LONG
    assert result.relation is HorizonRelation.ALIGNED
