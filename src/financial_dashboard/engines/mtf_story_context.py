from __future__ import annotations

from .models import Direction
from .mtf_story_models import (
    ConflictSeverity,
    ContextAssessment,
    ContextState,
    StoryConflict,
    TimeframeStoryState,
)

_CONTEXT_TFS = ("1d", "4h", "2h")
_TRANSITION_UP = "STATE_TRANSITION_UP"
_TRANSITION_DOWN = "STATE_TRANSITION_DOWN"


class MTFStoryContextError(ValueError):
    pass


def _key(timeframe: str) -> str:
    return timeframe.strip().lower()


def _transition_direction(state: TimeframeStoryState | None) -> Direction:
    if state is None:
        return Direction.NEUTRAL
    if state.structural_state == _TRANSITION_UP:
        return Direction.UP
    if state.structural_state == _TRANSITION_DOWN:
        return Direction.DOWN
    return Direction.NEUTRAL


def _direction(state: TimeframeStoryState | None) -> Direction:
    transition = _transition_direction(state)
    return transition if transition is not Direction.NEUTRAL else (
        state.structural_direction if state is not None else Direction.NEUTRAL
    )


def _is_transition(state: TimeframeStoryState | None) -> bool:
    return _transition_direction(state) is not Direction.NEUTRAL


def _directional_context(direction: Direction) -> ContextState:
    if direction is Direction.UP:
        return ContextState.BULLISH_CONTEXT
    if direction is Direction.DOWN:
        return ContextState.BEARISH_CONTEXT
    return ContextState.MIXED_CONTEXT


def classify_context(states: tuple[TimeframeStoryState, ...] | list[TimeframeStoryState]) -> ContextAssessment:
    """Classify hierarchical 1D/4H/2H structural context.

    Rules are intentionally hierarchical rather than vote-based:
    - 4H is the structural anchor when usable.
    - 1D constrains macro interpretation.
    - 2H confirms the anchor or represents a lower-context reaction.
    - 2H alone never flips an established 1D+4H context.
    - a 4H transition is always surfaced as TRANSITION_CONTEXT.
    """

    by_tf: dict[str, TimeframeStoryState] = {}
    for state in states:
        timeframe = _key(state.timeframe)
        if timeframe not in _CONTEXT_TFS:
            raise MTFStoryContextError(f"unsupported context timeframe: {state.timeframe}")
        if timeframe in by_tf:
            raise MTFStoryContextError(f"duplicate context timeframe: {state.timeframe}")
        by_tf[timeframe] = state

    usable = {tf: state for tf, state in by_tf.items() if state.usable}
    usable_ordered = tuple(tf for tf in _CONTEXT_TFS if tf in usable)
    reasons: list[str] = []
    conflicts: list[StoryConflict] = []

    for tf in _CONTEXT_TFS:
        state = by_tf.get(tf)
        if state is None:
            reasons.append(f"{tf.upper()}:MISSING")
        elif not state.usable:
            reasons.append(f"{tf.upper()}:DATA_INVALID")
        elif state.data_quality.value == "DATA_LIMITED":
            reasons.append(f"{tf.upper()}:DATA_LIMITED")

    if len(usable) < 2:
        return ContextAssessment(
            state=ContextState.INSUFFICIENT_DATA,
            direction=Direction.NEUTRAL,
            anchor_timeframe=None,
            usable_timeframes=usable_ordered,
            reasons=tuple(reasons + ["CONTEXT_REQUIRES_AT_LEAST_TWO_USABLE_TIMEFRAMES"]),
            conflicts=tuple(conflicts),
        )

    one_day = usable.get("1d")
    four_hour = usable.get("4h")
    two_hour = usable.get("2h")

    # 4H is the anchor whenever available. A 4H transition is never collapsed
    # back into a plain bullish/bearish context by 1D or 2H alignment.
    if four_hour is not None and _is_transition(four_hour):
        direction = _transition_direction(four_hour)
        reasons.append(f"4H:STRUCTURAL_TRANSITION:{direction.name}")
        if one_day is not None and _direction(one_day) not in {Direction.NEUTRAL, direction}:
            conflicts.append(
                StoryConflict(
                    code="MACRO_OPPOSES_4H_TRANSITION",
                    message="1D macro structure opposes the active 4H transition",
                    severity=ConflictSeverity.WARNING,
                    timeframes=("1d", "4h"),
                )
            )
        return ContextAssessment(
            state=ContextState.TRANSITION_CONTEXT,
            direction=direction,
            anchor_timeframe="4h",
            usable_timeframes=usable_ordered,
            reasons=tuple(reasons),
            conflicts=tuple(conflicts),
        )

    if four_hour is None:
        # Fallback path is deliberately conservative: without the 4H anchor,
        # 1D and 2H must agree directionally to establish context.
        assert one_day is not None and two_hour is not None
        if _is_transition(one_day) or _is_transition(two_hour):
            transition = _transition_direction(two_hour) if _is_transition(two_hour) else _transition_direction(one_day)
            reasons.append("4H:UNAVAILABLE_FALLBACK_TRANSITION")
            return ContextAssessment(
                state=ContextState.TRANSITION_CONTEXT,
                direction=transition,
                anchor_timeframe=None,
                usable_timeframes=usable_ordered,
                reasons=tuple(reasons),
                conflicts=tuple(conflicts),
            )
        d1, d2 = _direction(one_day), _direction(two_hour)
        if d1 is not Direction.NEUTRAL and d1 is d2:
            reasons.append(f"1D_2H:FALLBACK_ALIGNMENT:{d1.name}")
            return ContextAssessment(
                state=_directional_context(d1),
                direction=d1,
                anchor_timeframe=None,
                usable_timeframes=usable_ordered,
                reasons=tuple(reasons),
                conflicts=tuple(conflicts),
            )
        conflicts.append(
            StoryConflict(
                code="NO_4H_ANCHOR_AND_FALLBACK_DISAGREES",
                message="4H is unavailable and 1D/2H do not provide aligned structural context",
                severity=ConflictSeverity.WARNING,
                timeframes=("1d", "2h"),
            )
        )
        return ContextAssessment(
            state=ContextState.MIXED_CONTEXT,
            direction=Direction.NEUTRAL,
            anchor_timeframe=None,
            usable_timeframes=usable_ordered,
            reasons=tuple(reasons),
            conflicts=tuple(conflicts),
        )

    anchor_direction = _direction(four_hour)
    reasons.append(f"4H:ANCHOR:{anchor_direction.name}")
    if anchor_direction is Direction.NEUTRAL:
        return ContextAssessment(
            state=ContextState.MIXED_CONTEXT,
            direction=Direction.NEUTRAL,
            anchor_timeframe="4h",
            usable_timeframes=usable_ordered,
            reasons=tuple(reasons + ["4H:NEUTRAL_ANCHOR"]),
            conflicts=tuple(conflicts),
        )

    d1 = _direction(one_day)
    d2 = _direction(two_hour)

    # 2H is allowed to react against the anchor but cannot flip it on its own.
    if two_hour is not None and d2 not in {Direction.NEUTRAL, anchor_direction}:
        conflicts.append(
            StoryConflict(
                code="2H_REACTION_OPPOSES_4H",
                message="2H primary structure opposes the 4H structural anchor",
                severity=ConflictSeverity.INFO,
                timeframes=("4h", "2h"),
            )
        )
        reasons.append(f"2H:REACTION_AGAINST_4H:{d2.name}")

    # If 1D and 2H both oppose 4H, the anchor is isolated and context is mixed.
    if (
        one_day is not None
        and two_hour is not None
        and d1 not in {Direction.NEUTRAL, anchor_direction}
        and d2 is d1
    ):
        conflicts.append(
            StoryConflict(
                code="4H_ISOLATED_AGAINST_1D_2H",
                message="4H structural anchor is opposed by aligned 1D and 2H structure",
                severity=ConflictSeverity.WARNING,
                timeframes=("1d", "4h", "2h"),
            )
        )
        return ContextAssessment(
            state=ContextState.MIXED_CONTEXT,
            direction=Direction.NEUTRAL,
            anchor_timeframe="4h",
            usable_timeframes=usable_ordered,
            reasons=tuple(reasons),
            conflicts=tuple(conflicts),
        )

    # 4H+2H alignment against 1D is a structural transition, not a completed
    # macro flip. This preserves the distinction between reversal-building and
    # established context for later story classification.
    if (
        one_day is not None
        and two_hour is not None
        and d2 is anchor_direction
        and d1 not in {Direction.NEUTRAL, anchor_direction}
    ):
        conflicts.append(
            StoryConflict(
                code="4H_2H_OPPOSE_MACRO",
                message="4H and 2H align against the 1D macro structure",
                severity=ConflictSeverity.WARNING,
                timeframes=("1d", "4h", "2h"),
            )
        )
        reasons.append("4H_2H:ALIGNED_AGAINST_1D")
        return ContextAssessment(
            state=ContextState.TRANSITION_CONTEXT,
            direction=anchor_direction,
            anchor_timeframe="4h",
            usable_timeframes=usable_ordered,
            reasons=tuple(reasons),
            conflicts=tuple(conflicts),
        )

    # A 2H transition against an established 4H remains a lower-context reaction.
    # It becomes a transition context only after 4H itself transitions or 4H+2H
    # align against 1D as handled above.
    if two_hour is not None and _is_transition(two_hour):
        reasons.append(f"2H:TRANSITION_WITHIN_4H_CONTEXT:{_transition_direction(two_hour).name}")

    if one_day is not None and d1 not in {Direction.NEUTRAL, anchor_direction}:
        conflicts.append(
            StoryConflict(
                code="1D_OPPOSES_4H",
                message="1D macro structure opposes the 4H structural anchor",
                severity=ConflictSeverity.WARNING,
                timeframes=("1d", "4h"),
            )
        )
        # With no confirming 2H alignment for the 4H anchor, stay mixed.
        if two_hour is None or d2 is Direction.NEUTRAL:
            return ContextAssessment(
                state=ContextState.MIXED_CONTEXT,
                direction=Direction.NEUTRAL,
                anchor_timeframe="4h",
                usable_timeframes=usable_ordered,
                reasons=tuple(reasons),
                conflicts=tuple(conflicts),
            )

    if two_hour is not None and d2 is anchor_direction:
        reasons.append(f"2H:CONFIRMS_4H:{anchor_direction.name}")
    if one_day is not None and d1 is anchor_direction:
        reasons.append(f"1D:CONFIRMS_4H:{anchor_direction.name}")

    return ContextAssessment(
        state=_directional_context(anchor_direction),
        direction=anchor_direction,
        anchor_timeframe="4h",
        usable_timeframes=usable_ordered,
        reasons=tuple(reasons),
        conflicts=tuple(conflicts),
    )
