from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.context.volatility_environment_projection import (
    ExpansionCharacter,
    VolatilityRangeRegime,
)
from financial_dashboard.decision.conflict import ConflictSeverity, ConflictState, assess_conflict
from financial_dashboard.decision.environment import (
    EnvironmentAlignment,
    EnvironmentAssessment,
    EnvironmentRisk,
)
from financial_dashboard.decision.participation import ParticipationAssessment, ParticipationState
from financial_dashboard.decision.reaction import ReactionAssessment, ReactionState
from financial_dashboard.decision.structural import StructuralDirection


def _environment() -> EnvironmentAssessment:
    return EnvironmentAssessment(
        regime=VolatilityRangeRegime.NORMAL,
        character=ExpansionCharacter.BALANCED,
        alignment=EnvironmentAlignment.NEUTRAL,
        risk=EnvironmentRisk.NORMAL,
        data_quality=ContextDataQuality.VALID,
        reasons=(),
        source_refs=(),
    )


def _participation(
    state: ParticipationState = ParticipationState.NEUTRAL,
    *,
    heavy_conflict: bool = False,
    unsupported_break: bool = False,
) -> ParticipationAssessment:
    return ParticipationAssessment(
        state=state,
        heavy_conflict=heavy_conflict,
        unsupported_break=unsupported_break,
        data_quality=ContextDataQuality.VALID,
        reasons=(),
        source_refs=(),
    )


def _reaction(
    state: ReactionState,
    *,
    failed: bool,
    confirmed: bool = False,
    developing: bool = False,
) -> ReactionAssessment:
    return ReactionAssessment(
        state=state,
        failure_present=failed,
        confirmation_present=confirmed,
        developing_present=developing,
        data_quality=ContextDataQuality.VALID,
        reasons=(),
        source_refs=(),
    )


def test_historical_failed_reaction_does_not_materially_veto_active_confirmed_path() -> None:
    result = assess_conflict(
        StructuralDirection.LONG,
        reaction=_reaction(ReactionState.CONFIRMED, failed=True, confirmed=True),
        participation=_participation(),
        environment=_environment(),
    )

    reaction = next(item for item in result.families if item.family == "REACTION")
    assert reaction.severity is ConflictSeverity.LOW
    assert result.state is ConflictState.LOW


def test_current_failed_reaction_remains_material() -> None:
    result = assess_conflict(
        StructuralDirection.LONG,
        reaction=_reaction(ReactionState.FAILED, failed=True),
        participation=_participation(),
        environment=_environment(),
    )

    reaction = next(item for item in result.families if item.family == "REACTION")
    assert reaction.severity is ConflictSeverity.MATERIAL
    assert result.state is ConflictState.MATERIAL


def test_volume_opposition_is_quality_not_directional_material_veto() -> None:
    result = assess_conflict(
        StructuralDirection.LONG,
        reaction=_reaction(ReactionState.ABSENT, failed=False),
        participation=_participation(ParticipationState.OPPOSING, heavy_conflict=True),
        environment=_environment(),
    )

    participation = next(item for item in result.families if item.family == "PARTICIPATION")
    assert participation.severity is ConflictSeverity.LOW
    assert result.state is ConflictState.LOW


def test_unsupported_structure_side_break_remains_material_quality_conflict() -> None:
    result = assess_conflict(
        StructuralDirection.LONG,
        reaction=_reaction(ReactionState.ABSENT, failed=False),
        participation=_participation(ParticipationState.WEAK, unsupported_break=True),
        environment=_environment(),
    )

    participation = next(item for item in result.families if item.family == "PARTICIPATION")
    assert participation.severity is ConflictSeverity.MATERIAL
    assert result.state is ConflictState.MATERIAL
