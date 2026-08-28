from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from financial_dashboard.context.volatility_environment_projection import VolatilityRangeRegime

from .environment import EnvironmentAlignment, EnvironmentAssessment, EnvironmentRisk
from .participation import ParticipationAssessment, ParticipationState
from .reaction import ReactionAssessment, ReactionState
from .structural import StructuralDirection


class ConflictState(StrEnum):
    NONE = "NONE"
    LOW = "LOW"
    MATERIAL = "MATERIAL"
    HIGH = "HIGH"
    UNRESOLVED = "UNRESOLVED"


class ConflictSeverity(StrEnum):
    NONE = "NONE"
    LOW = "LOW"
    MATERIAL = "MATERIAL"


@dataclass(frozen=True, slots=True)
class ConflictFamilyEvidence:
    family: str
    severity: ConflictSeverity
    reasons: tuple[str, ...]
    lineage_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConflictAssessment:
    state: ConflictState
    families: tuple[ConflictFamilyEvidence, ...]
    reasons: tuple[str, ...]


def _lineages(refs) -> tuple[str, ...]:
    values = {
        ref.lineage_id or f"{ref.domain.value}:{ref.timeframe}:{ref.native_id}"
        for ref in refs
    }
    return tuple(sorted(values))


def _reaction_evidence(reaction: ReactionAssessment) -> ConflictFamilyEvidence:
    if reaction.failure_present:
        if reaction.confirmation_present:
            # A live failure on one lineage while the current path holds a
            # confirmed reaction is a secondary-lineage failure: the market
            # rejected one zone but is confirming another. That is normal
            # price discovery during pullbacks, not a material contradiction;
            # failures that overlap a confirmed zone were already superseded
            # upstream in the reaction assessment.
            return ConflictFamilyEvidence(
                "REACTION",
                ConflictSeverity.LOW,
                ("REACTION_FAILED_SECONDARY_LINEAGE",),
                _lineages(reaction.source_refs),
            )
        return ConflictFamilyEvidence(
            "REACTION",
            ConflictSeverity.MATERIAL,
            ("REACTION_FAILED",),
            _lineages(reaction.source_refs),
        )
    return ConflictFamilyEvidence("REACTION", ConflictSeverity.NONE, (), _lineages(reaction.source_refs))


def _participation_evidence(participation: ParticipationAssessment) -> ConflictFamilyEvidence:
    if participation.state is ParticipationState.OPPOSING:
        reason = "PARTICIPATION_HEAVY_CONFLICT" if participation.heavy_conflict else "PARTICIPATION_OPPOSING"
        return ConflictFamilyEvidence(
            "PARTICIPATION",
            ConflictSeverity.MATERIAL,
            (reason,),
            _lineages(participation.source_refs),
        )
    if participation.unsupported_break:
        return ConflictFamilyEvidence(
            "PARTICIPATION",
            ConflictSeverity.MATERIAL,
            ("UNSUPPORTED_BREAK",),
            _lineages(participation.source_refs),
        )
    if participation.state is ParticipationState.WEAK:
        return ConflictFamilyEvidence(
            "PARTICIPATION",
            ConflictSeverity.LOW,
            ("PARTICIPATION_WEAK",),
            _lineages(participation.source_refs),
        )
    return ConflictFamilyEvidence(
        "PARTICIPATION",
        ConflictSeverity.NONE,
        (),
        _lineages(participation.source_refs),
    )


def _environment_evidence(environment: EnvironmentAssessment) -> ConflictFamilyEvidence:
    # SHOCK is handled later as its own hard gate. It is not double-counted as HIGH
    # conflict here. Directionally opposing expansion is a material contradiction;
    # unstable/mean-reversion risk alone remains a soft LOW conflict input.
    if (
        environment.alignment is EnvironmentAlignment.OPPOSING
        and environment.regime is VolatilityRangeRegime.EXPANDING
    ):
        return ConflictFamilyEvidence(
            "ENVIRONMENT",
            ConflictSeverity.MATERIAL,
            ("VOLATILITY_EXPANSION_OPPOSES_STRUCTURE",),
            _lineages(environment.source_refs),
        )
    if environment.risk is EnvironmentRisk.ELEVATED:
        return ConflictFamilyEvidence(
            "ENVIRONMENT",
            ConflictSeverity.LOW,
            ("VOLATILITY_ENVIRONMENT_RISK_ELEVATED",),
            _lineages(environment.source_refs),
        )
    return ConflictFamilyEvidence(
        "ENVIRONMENT",
        ConflictSeverity.NONE,
        (),
        _lineages(environment.source_refs),
    )


def assess_conflict(
    side: StructuralDirection,
    *,
    reaction: ReactionAssessment,
    participation: ParticipationAssessment,
    environment: EnvironmentAssessment,
) -> ConflictAssessment:
    """Transparent v1 conflict table over independent semantic families.

    HIGH requires at least two distinct MATERIAL families. Multiple refs inside one
    family never become multiple votes, and Context/Permission are intentionally not
    accepted as inputs.
    """

    if side is StructuralDirection.UNRESOLVED:
        return ConflictAssessment(
            ConflictState.UNRESOLVED,
            (),
            ("STRUCTURAL_SIDE_UNRESOLVED",),
        )

    families = (
        _reaction_evidence(reaction),
        _participation_evidence(participation),
        _environment_evidence(environment),
    )

    known_family_count = sum(
        1
        for known in (
            reaction.state is not ReactionState.UNKNOWN,
            participation.state is not ParticipationState.UNKNOWN,
            environment.risk is not EnvironmentRisk.UNKNOWN,
        )
        if known
    )
    if known_family_count == 0:
        return ConflictAssessment(
            ConflictState.UNRESOLVED,
            families,
            ("CONFLICT_EVIDENCE_UNAVAILABLE",),
        )

    material = tuple(item for item in families if item.severity is ConflictSeverity.MATERIAL)
    low = tuple(item for item in families if item.severity is ConflictSeverity.LOW)
    reasons = tuple(reason for item in families for reason in item.reasons)

    if len(material) >= 2:
        state = ConflictState.HIGH
    elif material:
        state = ConflictState.MATERIAL
    elif low:
        state = ConflictState.LOW
    else:
        state = ConflictState.NONE

    return ConflictAssessment(state, families, reasons or ("NO_INDEPENDENT_CONFLICT",))


__all__ = [
    "ConflictAssessment",
    "ConflictFamilyEvidence",
    "ConflictSeverity",
    "ConflictState",
    "assess_conflict",
]
