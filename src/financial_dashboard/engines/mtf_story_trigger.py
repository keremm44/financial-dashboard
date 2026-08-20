from __future__ import annotations

from .models import Direction
from .mtf_story_models import (
    ConflictSeverity,
    StoryConflict,
    TimeframeStoryState,
    TriggerAssessment,
    TriggerState,
)

_TRIGGER_TFS = ("1h", "30m", "15m")
_TRANSITION_UP = "STATE_TRANSITION_UP"
_TRANSITION_DOWN = "STATE_TRANSITION_DOWN"
_CONFIRMED_BREAK_STATES = {
    "KIRILIM_TEYITLI",
    "RETEST_BEKLENIYOR",
    "RETEST_EDILIYOR",
    "RETEST_BASARILI",
    "FORMASYON_TAMAMLANDI",
}


class MTFStoryTriggerError(ValueError):
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


def _structure_direction(state: TimeframeStoryState | None) -> Direction:
    transition = _transition_direction(state)
    return transition if transition is not Direction.NEUTRAL else (
        state.structural_direction if state is not None else Direction.NEUTRAL
    )


def _confirmed_breakout(state: TimeframeStoryState | None) -> Direction:
    if state is None or state.pattern_state not in _CONFIRMED_BREAK_STATES:
        return Direction.NEUTRAL
    return state.breakout_direction


def _directional_trigger(direction: Direction) -> TriggerState:
    return TriggerState.BULLISH_TRIGGER if direction is Direction.UP else TriggerState.BEARISH_TRIGGER


def classify_trigger(states: tuple[TimeframeStoryState, ...] | list[TimeframeStoryState]) -> TriggerAssessment:
    """Classify tactical 1H/30m/15m evidence without using higher-TF context.

    1H is the tactical structural anchor when available. Pattern breakouts may
    confirm or oppose structure, but an opposing breakout alone is never promoted
    to REVERSAL_TRIGGER. A reversal trigger requires a 1H structural transition.
    """

    by_tf: dict[str, TimeframeStoryState] = {}
    for state in states:
        timeframe = _key(state.timeframe)
        if timeframe not in _TRIGGER_TFS:
            raise MTFStoryTriggerError(f"unsupported trigger timeframe: {state.timeframe}")
        if timeframe in by_tf:
            raise MTFStoryTriggerError(f"duplicate trigger timeframe: {state.timeframe}")
        by_tf[timeframe] = state

    usable = {tf: state for tf, state in by_tf.items() if state.usable}
    usable_ordered = tuple(tf for tf in _TRIGGER_TFS if tf in usable)
    reasons: list[str] = []
    conflicts: list[StoryConflict] = []

    for tf in _TRIGGER_TFS:
        state = by_tf.get(tf)
        if state is None:
            reasons.append(f"{tf.upper()}:MISSING")
        elif not state.usable:
            reasons.append(f"{tf.upper()}:DATA_INVALID")
        elif state.data_quality.value == "DATA_LIMITED":
            reasons.append(f"{tf.upper()}:DATA_LIMITED")

    if len(usable) < 2:
        return TriggerAssessment(
            state=TriggerState.INSUFFICIENT_DATA,
            direction=Direction.NEUTRAL,
            anchor_timeframe=None,
            usable_timeframes=usable_ordered,
            reasons=tuple(reasons + ["TRIGGER_REQUIRES_AT_LEAST_TWO_USABLE_TIMEFRAMES"]),
            conflicts=tuple(conflicts),
        )

    one_hour = usable.get("1h")
    thirty = usable.get("30m")
    fifteen = usable.get("15m")

    if one_hour is not None:
        anchor_direction = _structure_direction(one_hour)
        transition_direction = _transition_direction(one_hour)
        reasons.append(f"1H:ANCHOR:{anchor_direction.name}")

        lower_states = [state for state in (thirty, fifteen) if state is not None]
        lower_structure = [_structure_direction(state) for state in lower_states]
        lower_breakouts = [_confirmed_breakout(state) for state in lower_states]

        if transition_direction is not Direction.NEUTRAL:
            support = any(direction is transition_direction for direction in lower_structure + lower_breakouts)
            if support:
                reasons.append(f"1H:TRANSITION_CONFIRMED_BY_LOWER_TF:{transition_direction.name}")
                return TriggerAssessment(
                    state=TriggerState.REVERSAL_TRIGGER,
                    direction=transition_direction,
                    anchor_timeframe="1h",
                    usable_timeframes=usable_ordered,
                    reasons=tuple(reasons),
                    conflicts=tuple(conflicts),
                )
            return TriggerAssessment(
                state=TriggerState.NO_TRIGGER,
                direction=Direction.NEUTRAL,
                anchor_timeframe="1h",
                usable_timeframes=usable_ordered,
                reasons=tuple(reasons + ["1H:TRANSITION_LACKS_LOWER_TF_CONFIRMATION"]),
                conflicts=tuple(conflicts),
            )

        opposing_breakouts = [direction for direction in lower_breakouts if direction not in {Direction.NEUTRAL, anchor_direction}]
        if opposing_breakouts:
            breakout_direction = opposing_breakouts[0]
            conflicts.append(
                StoryConflict(
                    code="LOWER_TF_BREAKOUT_OPPOSES_1H_STRUCTURE",
                    message="Confirmed lower-timeframe breakout opposes 1H structure",
                    severity=ConflictSeverity.INFO,
                    timeframes=("1h", "30m", "15m"),
                )
            )
            reasons.append(f"LOWER_TF:COUNTER_STRUCTURE_BREAKOUT:{breakout_direction.name}")
            return TriggerAssessment(
                state=TriggerState.BREAKOUT_TRIGGER,
                direction=breakout_direction,
                anchor_timeframe="1h",
                usable_timeframes=usable_ordered,
                reasons=tuple(reasons),
                conflicts=tuple(conflicts),
            )

        if anchor_direction is not Direction.NEUTRAL:
            structural_support = any(direction is anchor_direction for direction in lower_structure)
            breakout_support = any(direction is anchor_direction for direction in lower_breakouts)
            if structural_support or breakout_support:
                reasons.append(f"LOWER_TF:CONFIRMS_1H:{anchor_direction.name}")
                return TriggerAssessment(
                    state=_directional_trigger(anchor_direction),
                    direction=anchor_direction,
                    anchor_timeframe="1h",
                    usable_timeframes=usable_ordered,
                    reasons=tuple(reasons),
                    conflicts=tuple(conflicts),
                )

        confirmed_breakouts = [direction for direction in lower_breakouts if direction is not Direction.NEUTRAL]
        if confirmed_breakouts and all(direction is confirmed_breakouts[0] for direction in confirmed_breakouts):
            return TriggerAssessment(
                state=TriggerState.BREAKOUT_TRIGGER,
                direction=confirmed_breakouts[0],
                anchor_timeframe="1h",
                usable_timeframes=usable_ordered,
                reasons=tuple(reasons + [f"LOWER_TF:CONFIRMED_BREAKOUT:{confirmed_breakouts[0].name}"]),
                conflicts=tuple(conflicts),
            )

        return TriggerAssessment(
            state=TriggerState.NO_TRIGGER,
            direction=Direction.NEUTRAL,
            anchor_timeframe="1h",
            usable_timeframes=usable_ordered,
            reasons=tuple(reasons),
            conflicts=tuple(conflicts),
        )

    # Conservative fallback: without 1H, 30m and 15m must agree structurally.
    assert thirty is not None and fifteen is not None
    d30, d15 = _structure_direction(thirty), _structure_direction(fifteen)
    b30, b15 = _confirmed_breakout(thirty), _confirmed_breakout(fifteen)

    if d30 is not Direction.NEUTRAL and d30 is d15:
        return TriggerAssessment(
            state=_directional_trigger(d30),
            direction=d30,
            anchor_timeframe=None,
            usable_timeframes=usable_ordered,
            reasons=tuple(reasons + [f"30M_15M:FALLBACK_ALIGNMENT:{d30.name}"]),
            conflicts=tuple(conflicts),
        )

    if b30 is not Direction.NEUTRAL and b30 is b15:
        return TriggerAssessment(
            state=TriggerState.BREAKOUT_TRIGGER,
            direction=b30,
            anchor_timeframe=None,
            usable_timeframes=usable_ordered,
            reasons=tuple(reasons + [f"30M_15M:BREAKOUT_ALIGNMENT:{b30.name}"]),
            conflicts=tuple(conflicts),
        )

    conflicts.append(
        StoryConflict(
            code="NO_1H_ANCHOR_AND_LOWER_TF_DISAGREE",
            message="1H is unavailable and 30m/15m do not provide aligned trigger evidence",
            severity=ConflictSeverity.WARNING,
            timeframes=("30m", "15m"),
        )
    )
    return TriggerAssessment(
        state=TriggerState.NO_TRIGGER,
        direction=Direction.NEUTRAL,
        anchor_timeframe=None,
        usable_timeframes=usable_ordered,
        reasons=tuple(reasons),
        conflicts=tuple(conflicts),
    )
