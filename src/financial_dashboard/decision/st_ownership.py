from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from .scenario import EntryScenarioAssessment, ScenarioKind, ScenarioPresence
from .st_thesis_identity import _pullback_candidate, _sr_candidate
from .structural import DecisionHorizon

if TYPE_CHECKING:
    from financial_dashboard.decision_input import DecisionInputSnapshot


class STEconomicOwnership(StrEnum):
    """Economic relation of the observed ST setup to concurrent LT ownership."""

    INDEPENDENT_ST = "INDEPENDENT_ST"
    LT_TIMING_ONLY = "LT_TIMING_ONLY"
    UNRESOLVED = "UNRESOLVED"


def classify_st_economic_ownership(
    snapshot: "DecisionInputSnapshot",
    long_term: EntryScenarioAssessment,
    short_term: EntryScenarioAssessment,
) -> STEconomicOwnership:
    """Classify ST/LT ownership without changing scenario qualification.

    A separate ST product is proven only when the same causal thesis evidence used by
    the Step-1 thesis shadow resolves to exactly one canonical ST family and the ST
    scenario already has target context. Generic short-term continuation with known
    target context is LT timing when an LT scenario is concurrently present but no
    independent ST thesis evidence exists. Ambiguity and missing target context fail
    closed as UNRESOLVED rather than receiving ST priority.
    """

    if long_term.horizon is not DecisionHorizon.LONG_TERM:
        raise ValueError("long_term scenario must have LONG_TERM horizon")
    if short_term.horizon is not DecisionHorizon.SHORT_TERM:
        raise ValueError("short_term scenario must have SHORT_TERM horizon")
    if short_term.presence is not ScenarioPresence.PRESENT:
        return STEconomicOwnership.UNRESOLVED

    target_identity = short_term.active_target_identity
    if target_identity is not None:
        target_identity = str(target_identity).strip() or None

    candidates = tuple(
        candidate
        for candidate in (
            _sr_candidate(snapshot),
            _pullback_candidate(snapshot, short_term),
        )
        if candidate is not None
    )
    families = {candidate.family for candidate in candidates}
    if target_identity is not None and len(candidates) == 1 and len(families) == 1:
        return STEconomicOwnership.INDEPENDENT_ST

    if (
        target_identity is not None
        and long_term.presence is ScenarioPresence.PRESENT
        and short_term.kind is ScenarioKind.CONTINUATION
        and not candidates
    ):
        return STEconomicOwnership.LT_TIMING_ONLY

    return STEconomicOwnership.UNRESOLVED


__all__ = [
    "STEconomicOwnership",
    "classify_st_economic_ownership",
]
