from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from .scenario import (
    EntryScenarioAssessment,
    PreparedEntryScenario,
    ScenarioPresence,
    ScenarioStage,
    prepare_entry_scenario,
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
    """Non-action ownership decision between independently assessed LT/ST scenarios.

    Qualification has priority over mere scenario presence. A qualified SHORT_TERM
    setup may therefore proceed when LONG_TERM is blocked, developing, or unresolved.
    When both horizons are qualified, LONG_TERM remains the deterministic tie-break.
    The arbiter never compares scores and never emits a trading action.
    """

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
        """Arbitration owns horizon selection only; Entry owns actions."""

        return False


@dataclass(frozen=True, slots=True)
class PreparedEntryArbitration:
    """Internal arbitration plus the exact prepared LT/ST assessments it selected from."""

    arbitration: EntryScenarioArbitration
    long_term: PreparedEntryScenario
    short_term: PreparedEntryScenario

    @property
    def selected_prepared(self) -> PreparedEntryScenario | None:
        if self.arbitration.selected_horizon is DecisionHorizon.LONG_TERM:
            return self.long_term
        if self.arbitration.selected_horizon is DecisionHorizon.SHORT_TERM:
            return self.short_term
        return None


def _validate_horizons(
    long_term: EntryScenarioAssessment,
    short_term: EntryScenarioAssessment,
) -> None:
    if long_term.horizon is not DecisionHorizon.LONG_TERM:
        raise ValueError("long_term scenario must have LONG_TERM horizon")
    if short_term.horizon is not DecisionHorizon.SHORT_TERM:
        raise ValueError("short_term scenario must have SHORT_TERM horizon")


def _is_qualified(scenario: EntryScenarioAssessment) -> bool:
    return (
        scenario.presence is ScenarioPresence.PRESENT
        and scenario.stage is ScenarioStage.QUALIFIED
    )


def _selected(
    *,
    horizon: DecisionHorizon,
    scenario: EntryScenarioAssessment,
    long_term: EntryScenarioAssessment,
    short_term: EntryScenarioAssessment,
    suppressed_horizons: tuple[DecisionHorizon, ...],
    reasons: tuple[str, ...],
) -> EntryScenarioArbitration:
    return EntryScenarioArbitration(
        state=ArbiterState.SELECTED,
        selection=(
            ArbiterSelection.LONG_TERM
            if horizon is DecisionHorizon.LONG_TERM
            else ArbiterSelection.SHORT_TERM
        ),
        selected_horizon=horizon,
        selected_scenario=scenario,
        long_term=long_term,
        short_term=short_term,
        suppressed_horizons=suppressed_horizons,
        reasons=reasons,
        waiting_for=(),
    )


def arbitrate_entry_scenarios(
    long_term: EntryScenarioAssessment,
    short_term: EntryScenarioAssessment,
) -> EntryScenarioArbitration:
    """Select one horizon by qualification first, with an LT tie-break.

    The two horizon scenarios are evaluated independently before arbitration. Presence
    alone is not allowed to veto a qualified setup on the other horizon. LONG_TERM
    keeps deterministic priority only when it is itself qualified, or when neither
    side is qualified and an observed LT scenario still needs to own its non-action
    state. This preserves single-horizon entry ownership without suppressing a valid
    ST setup behind a weaker or unresolved LT state.
    """

    _validate_horizons(long_term, short_term)

    # Equal technical qualification keeps the existing deterministic LT tie-break.
    if _is_qualified(long_term):
        suppressed = (
            (DecisionHorizon.SHORT_TERM,)
            if short_term.presence is ScenarioPresence.PRESENT
            else ()
        )
        reasons = ["LONG_TERM_SCENARIO_HAS_PRIORITY"]
        if suppressed:
            reasons.append("SHORT_TERM_SCENARIO_SUPPRESSED_BY_LONG_TERM")
        return _selected(
            horizon=DecisionHorizon.LONG_TERM,
            scenario=long_term,
            long_term=long_term,
            short_term=short_term,
            suppressed_horizons=suppressed,
            reasons=tuple(reasons),
        )

    # A technically qualified ST setup is not vetoed by mere LT presence or an LT
    # UNKNOWN state. This is the intentional Turn 5B policy change.
    if _is_qualified(short_term):
        if long_term.presence is ScenarioPresence.PRESENT:
            return _selected(
                horizon=DecisionHorizon.SHORT_TERM,
                scenario=short_term,
                long_term=long_term,
                short_term=short_term,
                suppressed_horizons=(DecisionHorizon.LONG_TERM,),
                reasons=(
                    "SHORT_TERM_QUALIFIED_OVERRIDES_NONQUALIFIED_LONG_TERM",
                    "LONG_TERM_SCENARIO_SUPPRESSED_BY_SHORT_TERM_QUALIFICATION",
                ),
            )
        if long_term.presence is ScenarioPresence.UNKNOWN:
            return _selected(
                horizon=DecisionHorizon.SHORT_TERM,
                scenario=short_term,
                long_term=long_term,
                short_term=short_term,
                suppressed_horizons=(),
                reasons=(
                    "SHORT_TERM_QUALIFIED_WHILE_LONG_TERM_PRESENCE_UNRESOLVED",
                    "LONG_TERM_UNKNOWN_DOES_NOT_VETO_QUALIFIED_SHORT_TERM",
                ),
            )
        return _selected(
            horizon=DecisionHorizon.SHORT_TERM,
            scenario=short_term,
            long_term=long_term,
            short_term=short_term,
            suppressed_horizons=(),
            reasons=(
                "LONG_TERM_SCENARIO_ABSENT",
                "SHORT_TERM_FALLBACK_SELECTED",
            ),
        )

    # No qualified setup exists. Preserve deterministic ownership of an observed LT
    # non-action state rather than swapping between two blocked/developing scenarios.
    if long_term.presence is ScenarioPresence.PRESENT:
        suppressed = (
            (DecisionHorizon.SHORT_TERM,)
            if short_term.presence is ScenarioPresence.PRESENT
            else ()
        )
        reasons = ["LONG_TERM_SCENARIO_HAS_PRIORITY"]
        if suppressed:
            reasons.append("SHORT_TERM_SCENARIO_SUPPRESSED_BY_LONG_TERM")
        return _selected(
            horizon=DecisionHorizon.LONG_TERM,
            scenario=long_term,
            long_term=long_term,
            short_term=short_term,
            suppressed_horizons=suppressed,
            reasons=tuple(reasons),
        )

    # UNKNOWN remains unresolved when there is no independently qualified ST setup.
    if long_term.presence is ScenarioPresence.UNKNOWN:
        return EntryScenarioArbitration(
            state=ArbiterState.WAITING_FOR_LONG_TERM_RESOLUTION,
            selection=ArbiterSelection.UNRESOLVED,
            selected_horizon=None,
            selected_scenario=None,
            long_term=long_term,
            short_term=short_term,
            suppressed_horizons=(
                (DecisionHorizon.SHORT_TERM,)
                if short_term.presence is ScenarioPresence.PRESENT
                else ()
            ),
            reasons=("LONG_TERM_PRESENCE_UNRESOLVED_NO_QUALIFIED_SHORT_TERM",),
            waiting_for=("LONG_TERM_SCENARIO_PRESENCE_TO_RESOLVE",),
        )

    # LT is explicitly absent. A present ST scenario owns its own non-action state,
    # including BLOCKED/DEVELOPING, exactly as before.
    if short_term.presence is ScenarioPresence.PRESENT:
        return _selected(
            horizon=DecisionHorizon.SHORT_TERM,
            scenario=short_term,
            long_term=long_term,
            short_term=short_term,
            suppressed_horizons=(),
            reasons=(
                "LONG_TERM_SCENARIO_ABSENT",
                "SHORT_TERM_FALLBACK_SELECTED",
            ),
        )

    if short_term.presence is ScenarioPresence.UNKNOWN:
        return EntryScenarioArbitration(
            state=ArbiterState.WAITING_FOR_SHORT_TERM_RESOLUTION,
            selection=ArbiterSelection.UNRESOLVED,
            selected_horizon=None,
            selected_scenario=None,
            long_term=long_term,
            short_term=short_term,
            suppressed_horizons=(),
            reasons=(
                "LONG_TERM_SCENARIO_ABSENT",
                "SHORT_TERM_SCENARIO_PRESENCE_UNRESOLVED",
            ),
            waiting_for=("SHORT_TERM_SCENARIO_PRESENCE_TO_RESOLVE",),
        )

    return EntryScenarioArbitration(
        state=ArbiterState.NO_SCENARIO,
        selection=ArbiterSelection.NONE,
        selected_horizon=None,
        selected_scenario=None,
        long_term=long_term,
        short_term=short_term,
        suppressed_horizons=(),
        reasons=("NO_LONG_OR_SHORT_TERM_ENTRY_SCENARIO",),
        waiting_for=(),
    )


def prepare_entry_arbitration(
    snapshot: "DecisionInputSnapshot",
    *,
    config: "DecisionEngineConfig | None" = None,
) -> PreparedEntryArbitration:
    """Prepare LT/ST once, then apply qualification-aware horizon ownership."""

    long_term = prepare_entry_scenario(snapshot, DecisionHorizon.LONG_TERM, config=config)
    short_term = prepare_entry_scenario(snapshot, DecisionHorizon.SHORT_TERM, config=config)
    arbitration = arbitrate_entry_scenarios(long_term.scenario, short_term.scenario)
    return PreparedEntryArbitration(
        arbitration=arbitration,
        long_term=long_term,
        short_term=short_term,
    )


def assess_entry_arbitration(
    snapshot: "DecisionInputSnapshot",
    *,
    config: "DecisionEngineConfig | None" = None,
) -> EntryScenarioArbitration:
    """Build both causal scenarios then apply qualification-aware ownership."""

    return prepare_entry_arbitration(snapshot, config=config).arbitration


__all__ = [
    "ArbiterSelection",
    "ArbiterState",
    "EntryScenarioArbitration",
    "arbitrate_entry_scenarios",
    "assess_entry_arbitration",
]
