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
from .st_ownership import STEconomicOwnership, classify_st_economic_ownership
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
    """Non-action economic ownership decision between LT/ST scenarios.

    Qualification still belongs to the scenario/eligibility chain. Arbitration only
    resolves which product owns one concurrent opportunity. A causally proven,
    independent ST thesis has product priority over LT; short-term behavior that is
    only LT timing stays LT-owned. The arbiter never compares scores and never emits
    a trading action.
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


def _long_term_owner(
    long_term: EntryScenarioAssessment,
    short_term: EntryScenarioAssessment,
    *,
    short_term_ownership: STEconomicOwnership,
) -> EntryScenarioArbitration:
    suppressed = (
        (DecisionHorizon.SHORT_TERM,)
        if short_term.presence is ScenarioPresence.PRESENT
        else ()
    )
    reasons = ["LONG_TERM_RETAINS_ECONOMIC_OWNERSHIP"]
    if short_term_ownership is STEconomicOwnership.LT_TIMING_ONLY:
        reasons.append("SHORT_TERM_IS_LONG_TERM_TIMING_NOT_INDEPENDENT_PRODUCT")
    elif short_term_ownership is STEconomicOwnership.UNRESOLVED and suppressed:
        reasons.append("SHORT_TERM_INDEPENDENCE_UNRESOLVED")
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


def _independent_short_term_owner(
    long_term: EntryScenarioAssessment,
    short_term: EntryScenarioAssessment,
    *,
    long_term_unknown: bool = False,
) -> EntryScenarioArbitration:
    reasons = ["SHORT_TERM_INDEPENDENT_PRODUCT_PRIORITY"]
    suppressed: tuple[DecisionHorizon, ...] = ()
    if long_term.presence is ScenarioPresence.PRESENT:
        suppressed = (DecisionHorizon.LONG_TERM,)
        reasons.append("LONG_TERM_SCENARIO_SUPPRESSED_BY_INDEPENDENT_SHORT_TERM")
    elif long_term_unknown:
        reasons.append("LONG_TERM_UNKNOWN_DOES_NOT_VETO_INDEPENDENT_SHORT_TERM")
    return _selected(
        horizon=DecisionHorizon.SHORT_TERM,
        scenario=short_term,
        long_term=long_term,
        short_term=short_term,
        suppressed_horizons=suppressed,
        reasons=tuple(reasons),
    )


def arbitrate_entry_scenarios(
    long_term: EntryScenarioAssessment,
    short_term: EntryScenarioAssessment,
    *,
    short_term_ownership: STEconomicOwnership = STEconomicOwnership.UNRESOLVED,
) -> EntryScenarioArbitration:
    """Select one economic owner without changing technical qualification.

    Independent qualified ST is the frozen product-priority winner when a concurrent
    LT opportunity exists. ST that is only LT timing, or whose independence is not
    causally resolved, cannot displace an observed LT owner. If LT is explicitly
    absent, the existing ST fallback remains unchanged because there is no collision
    to arbitrate.
    """

    _validate_horizons(long_term, short_term)
    if not isinstance(short_term_ownership, STEconomicOwnership):
        raise ValueError("short_term_ownership must be STEconomicOwnership")

    lt_qualified = _is_qualified(long_term)
    st_qualified = _is_qualified(short_term)
    independent_st = short_term_ownership is STEconomicOwnership.INDEPENDENT_ST

    if lt_qualified:
        if st_qualified and independent_st:
            return _independent_short_term_owner(long_term, short_term)
        return _long_term_owner(
            long_term,
            short_term,
            short_term_ownership=short_term_ownership,
        )

    if st_qualified:
        if long_term.presence is ScenarioPresence.PRESENT:
            if independent_st:
                return _independent_short_term_owner(long_term, short_term)
            return _long_term_owner(
                long_term,
                short_term,
                short_term_ownership=short_term_ownership,
            )
        if long_term.presence is ScenarioPresence.UNKNOWN:
            if independent_st:
                return _independent_short_term_owner(
                    long_term,
                    short_term,
                    long_term_unknown=True,
                )
            return EntryScenarioArbitration(
                state=ArbiterState.WAITING_FOR_LONG_TERM_RESOLUTION,
                selection=ArbiterSelection.UNRESOLVED,
                selected_horizon=None,
                selected_scenario=None,
                long_term=long_term,
                short_term=short_term,
                suppressed_horizons=(DecisionHorizon.SHORT_TERM,),
                reasons=(
                    "LONG_TERM_PRESENCE_UNRESOLVED",
                    "SHORT_TERM_INDEPENDENCE_NOT_PROVEN",
                ),
                waiting_for=("LONG_TERM_SCENARIO_PRESENCE_TO_RESOLVE",),
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

    # No qualified setup exists. An observed LT scenario retains its non-action
    # ownership exactly as before; Step 4 does not change qualification semantics.
    if long_term.presence is ScenarioPresence.PRESENT:
        return _long_term_owner(
            long_term,
            short_term,
            short_term_ownership=short_term_ownership,
        )

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
            reasons=("LONG_TERM_PRESENCE_UNRESOLVED_NO_INDEPENDENT_QUALIFIED_SHORT_TERM",),
            waiting_for=("LONG_TERM_SCENARIO_PRESENCE_TO_RESOLVE",),
        )

    # LT is explicitly absent. A present ST scenario owns its own state, including
    # BLOCKED/DEVELOPING, because no LT/ST economic collision exists.
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
    """Prepare LT/ST once, classify economic relation, then resolve one owner."""

    long_term = prepare_entry_scenario(snapshot, DecisionHorizon.LONG_TERM, config=config)
    short_term = prepare_entry_scenario(snapshot, DecisionHorizon.SHORT_TERM, config=config)
    short_term_ownership = classify_st_economic_ownership(
        snapshot,
        long_term.scenario,
        short_term.scenario,
    )
    arbitration = arbitrate_entry_scenarios(
        long_term.scenario,
        short_term.scenario,
        short_term_ownership=short_term_ownership,
    )
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
    """Build both causal scenarios then apply frozen ST/LT economic ownership."""

    return prepare_entry_arbitration(snapshot, config=config).arbitration


__all__ = [
    "ArbiterSelection",
    "ArbiterState",
    "EntryScenarioArbitration",
    "arbitrate_entry_scenarios",
    "assess_entry_arbitration",
]
