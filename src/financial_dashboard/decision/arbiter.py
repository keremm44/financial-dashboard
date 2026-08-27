from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from .scenario import EntryScenarioAssessment, ScenarioPresence
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
    """Non-action ownership decision between LT and ST entry scenarios.

    The arbiter never compares scores or readiness strength. LONG_TERM has semantic
    priority. SHORT_TERM is considered only after LONG_TERM is proven ABSENT;
    UNKNOWN is not absence and therefore cannot trigger a fallback.
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
        """Arbitration owns horizon selection only; Turn 6 owns entry actions."""

        return False


def _validate_horizons(
    long_term: EntryScenarioAssessment,
    short_term: EntryScenarioAssessment,
) -> None:
    if long_term.horizon is not DecisionHorizon.LONG_TERM:
        raise ValueError("long_term scenario must have LONG_TERM horizon")
    if short_term.horizon is not DecisionHorizon.SHORT_TERM:
        raise ValueError("short_term scenario must have SHORT_TERM horizon")


def arbitrate_entry_scenarios(
    long_term: EntryScenarioAssessment,
    short_term: EntryScenarioAssessment,
) -> EntryScenarioArbitration:
    """Apply LONG_TERM-first, SHORT_TERM-fallback ownership deterministically."""

    _validate_horizons(long_term, short_term)

    if long_term.presence is ScenarioPresence.PRESENT:
        suppressed = (
            (DecisionHorizon.SHORT_TERM,)
            if short_term.presence is ScenarioPresence.PRESENT
            else ()
        )
        reasons = ["LONG_TERM_SCENARIO_HAS_PRIORITY"]
        if suppressed:
            reasons.append("SHORT_TERM_SCENARIO_SUPPRESSED_BY_LONG_TERM")
        return EntryScenarioArbitration(
            state=ArbiterState.SELECTED,
            selection=ArbiterSelection.LONG_TERM,
            selected_horizon=DecisionHorizon.LONG_TERM,
            selected_scenario=long_term,
            long_term=long_term,
            short_term=short_term,
            suppressed_horizons=suppressed,
            reasons=tuple(reasons),
            waiting_for=(),
        )

    # UNKNOWN is not a negative vote. The ST path may be visible for diagnostics,
    # but it cannot own the decision until LT is proven absent.
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
            reasons=("LONG_TERM_PRESENCE_UNRESOLVED_NO_SHORT_TERM_FALLBACK",),
            waiting_for=("LONG_TERM_SCENARIO_PRESENCE_TO_RESOLVE",),
        )

    # Only explicit LT absence opens the ST fallback branch.
    if short_term.presence is ScenarioPresence.PRESENT:
        return EntryScenarioArbitration(
            state=ArbiterState.SELECTED,
            selection=ArbiterSelection.SHORT_TERM,
            selected_horizon=DecisionHorizon.SHORT_TERM,
            selected_scenario=short_term,
            long_term=long_term,
            short_term=short_term,
            suppressed_horizons=(),
            reasons=(
                "LONG_TERM_SCENARIO_ABSENT",
                "SHORT_TERM_FALLBACK_SELECTED",
            ),
            waiting_for=(),
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


def assess_entry_arbitration(
    snapshot: "DecisionInputSnapshot",
    *,
    config: "DecisionEngineConfig | None" = None,
    scenarios: tuple[EntryScenarioAssessment, EntryScenarioAssessment] | None = None,
) -> EntryScenarioArbitration:
    """Build both causal scenarios then apply strict horizon ownership.

    ``scenarios`` allows callers that already built the two entry scenarios for this
    snapshot/config to inject them as ``(long_term, short_term)`` instead of forcing
    a second full evaluation chain.
    """

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
