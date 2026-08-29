from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from .scenario import (
    EntryScenarioAssessment,
    ScenarioKind,
    ScenarioPresence,
    ScenarioStage,
    ScenarioUnknownReason,
)
from .structural import DecisionHorizon

if TYPE_CHECKING:
    from financial_dashboard.decision_input import DecisionInputSnapshot
    from .engine import DecisionEngineConfig


class ArbiterSelection(StrEnum):
    LONG_TERM = "LONG_TERM"
    SHORT_TERM = "SHORT_TERM"
    NONE = "NONE"
    UNRESOLVED = "UNRESOLVED"


class ArbiterState(StrEnum):
    SELECTED = "SELECTED"
    NO_SCENARIO = "NO_SCENARIO"
    WAITING_FOR_LONG_TERM_RESOLUTION = "WAITING_FOR_LONG_TERM_RESOLUTION"
    WAITING_FOR_SHORT_TERM_RESOLUTION = "WAITING_FOR_SHORT_TERM_RESOLUTION"


@dataclass(frozen=True, slots=True)
class EntryScenarioArbitration:
    state: ArbiterState
    selection: ArbiterSelection
    selected_horizon: DecisionHorizon | None
    selected_scenario: EntryScenarioAssessment | None
    long_term: EntryScenarioAssessment
    short_term: EntryScenarioAssessment
    suppressed_horizons: tuple[DecisionHorizon, ...]
    reasons: tuple[str, ...]
    waiting_for: tuple[str, ...]

    @property
    def is_actionable_signal(self) -> bool:
        return False


def _validate_horizons(long_term: EntryScenarioAssessment, short_term: EntryScenarioAssessment) -> None:
    if long_term.horizon is not DecisionHorizon.LONG_TERM:
        raise ValueError("long_term scenario must have LONG_TERM horizon")
    if short_term.horizon is not DecisionHorizon.SHORT_TERM:
        raise ValueError("short_term scenario must have SHORT_TERM horizon")


def _lt_unknown_allows_st(long_term: EntryScenarioAssessment, short_term: EntryScenarioAssessment) -> bool:
    reason = long_term.unknown_reason
    if reason is ScenarioUnknownReason.OPPORTUNITY_UNOBSERVED:
        return True
    if reason is ScenarioUnknownReason.WARMUP:
        return short_term.kind is ScenarioKind.SHORT_TERM_STANDALONE
    return False


def arbitrate_entry_scenarios(
    long_term: EntryScenarioAssessment,
    short_term: EntryScenarioAssessment,
) -> EntryScenarioArbitration:
    """Apply semantic horizon ownership without using UNKNOWN as blanket permission."""

    _validate_horizons(long_term, short_term)
    st_qualified = (
        short_term.presence is ScenarioPresence.PRESENT
        and short_term.stage is ScenarioStage.QUALIFIED
    )

    if long_term.presence is ScenarioPresence.PRESENT:
        lt_pullback = long_term.kind is ScenarioKind.PULLBACK_CONTINUATION
        if st_qualified and (long_term.stage is not ScenarioStage.QUALIFIED or lt_pullback):
            reasons = (
                (
                    "LONG_TERM_PULLBACK_IS_SHORT_TERM_TRADE",
                    "SHORT_TERM_OWNS_PULLBACK_CONTINUATION",
                )
                if lt_pullback and long_term.stage is ScenarioStage.QUALIFIED
                else (
                    f"LONG_TERM_SCENARIO_{long_term.stage.value}_NOT_QUALIFIED",
                    "SHORT_TERM_FALLBACK_WHILE_LONG_TERM_BLOCKED",
                )
            )
            return EntryScenarioArbitration(
                ArbiterState.SELECTED, ArbiterSelection.SHORT_TERM,
                DecisionHorizon.SHORT_TERM, short_term, long_term, short_term,
                (), reasons, (),
            )
        suppressed = (
            (DecisionHorizon.SHORT_TERM,)
            if short_term.presence is ScenarioPresence.PRESENT
            else ()
        )
        reasons = ["LONG_TERM_SCENARIO_HAS_PRIORITY"]
        if suppressed:
            reasons.append("SHORT_TERM_SCENARIO_SUPPRESSED_BY_LONG_TERM")
        return EntryScenarioArbitration(
            ArbiterState.SELECTED, ArbiterSelection.LONG_TERM,
            DecisionHorizon.LONG_TERM, long_term, long_term, short_term,
            suppressed, tuple(reasons), (),
        )

    if long_term.presence is ScenarioPresence.UNKNOWN:
        reason = long_term.unknown_reason
        if st_qualified and _lt_unknown_allows_st(long_term, short_term):
            return EntryScenarioArbitration(
                ArbiterState.SELECTED, ArbiterSelection.SHORT_TERM,
                DecisionHorizon.SHORT_TERM, short_term, long_term, short_term,
                (),
                (
                    f"LONG_TERM_NONAUTHORITATIVE:{reason.value}",
                    "SHORT_TERM_FALLBACK_WHILE_LONG_TERM_NONAUTHORITATIVE",
                ),
                (),
            )
        suppressed = (
            (DecisionHorizon.SHORT_TERM,)
            if short_term.presence is ScenarioPresence.PRESENT
            else ()
        )
        unsafe = reason in {
            ScenarioUnknownReason.DATA_UNAVAILABLE,
            ScenarioUnknownReason.STRUCTURE_UNRESOLVED,
            ScenarioUnknownReason.NONE,
        }
        return EntryScenarioArbitration(
            ArbiterState.WAITING_FOR_LONG_TERM_RESOLUTION,
            ArbiterSelection.UNRESOLVED,
            None,
            None,
            long_term,
            short_term,
            suppressed,
            (
                f"LONG_TERM_AUTHORITY_{'UNSAFE' if unsafe else 'UNRESOLVED'}:{reason.value}",
            ),
            (
                "LONG_TERM_STRUCTURAL_AUTHORITY_TO_RESOLVE"
                if unsafe
                else "LONG_TERM_SCENARIO_PRESENCE_TO_RESOLVE",
            ),
        )

    if short_term.presence is ScenarioPresence.PRESENT:
        return EntryScenarioArbitration(
            ArbiterState.SELECTED, ArbiterSelection.SHORT_TERM,
            DecisionHorizon.SHORT_TERM, short_term, long_term, short_term,
            (), ("LONG_TERM_SCENARIO_ABSENT", "SHORT_TERM_FALLBACK_SELECTED"), (),
        )

    if short_term.presence is ScenarioPresence.UNKNOWN:
        return EntryScenarioArbitration(
            ArbiterState.WAITING_FOR_SHORT_TERM_RESOLUTION,
            ArbiterSelection.UNRESOLVED,
            None, None, long_term, short_term, (),
            ("LONG_TERM_SCENARIO_ABSENT", "SHORT_TERM_SCENARIO_PRESENCE_UNRESOLVED"),
            ("SHORT_TERM_SCENARIO_PRESENCE_TO_RESOLVE",),
        )

    return EntryScenarioArbitration(
        ArbiterState.NO_SCENARIO, ArbiterSelection.NONE,
        None, None, long_term, short_term, (),
        ("NO_LONG_OR_SHORT_TERM_ENTRY_SCENARIO",), (),
    )


def assess_entry_arbitration(
    snapshot: "DecisionInputSnapshot",
    *,
    config: "DecisionEngineConfig | None" = None,
    scenarios: tuple[EntryScenarioAssessment, EntryScenarioAssessment] | None = None,
) -> EntryScenarioArbitration:
    if scenarios is None:
        long_term = snapshot.entry_scenario(DecisionHorizon.LONG_TERM, config=config)
        short_term = snapshot.entry_scenario(DecisionHorizon.SHORT_TERM, config=config)
    else:
        long_term, short_term = scenarios
    return arbitrate_entry_scenarios(long_term, short_term)


__all__ = [
    "ArbiterSelection",
    "ArbiterState",
    "EntryScenarioArbitration",
    "arbitrate_entry_scenarios",
    "assess_entry_arbitration",
]
