from financial_dashboard.context.envelope import CausalFamily, ContextDataQuality, ContextDomain, FactRef, SourceFamily
from financial_dashboard.context.volatility_environment_projection import ExpansionCharacter, VolatilityRangeRegime
from financial_dashboard.decision.conflict import ConflictState, assess_conflict
from financial_dashboard.decision.environment import EnvironmentAlignment, EnvironmentAssessment, EnvironmentRisk
from financial_dashboard.decision.participation import ParticipationAssessment, ParticipationState
from financial_dashboard.decision.reaction import ReactionAssessment, ReactionState
from financial_dashboard.decision.structural import StructuralDirection


def _ref(domain: ContextDomain, native_id: str):
    return FactRef(domain, "TEST", "THYAO", "1h", native_id, "TEST", 1, 1, 1, native_id, CausalFamily.IMPULSE, SourceFamily.PRICE_GEOMETRY, ContextDataQuality.VALID)


def _reaction(*, failed=False, confirmed=False):
    state = ReactionState.CONFIRMED if confirmed else ReactionState.FAILED if failed else ReactionState.ABSENT
    return ReactionAssessment(state, failed, confirmed, False, ContextDataQuality.VALID, (), (_ref(ContextDomain.FVG, "REACTION:1"),))


def _participation(state=ParticipationState.NEUTRAL, *, heavy=False, unsupported=False):
    return ParticipationAssessment(state, heavy, unsupported, ContextDataQuality.VALID, (), (_ref(ContextDomain.VOLUME, "VOL:1"),))


def _environment(*, risk=EnvironmentRisk.NORMAL, alignment=EnvironmentAlignment.NEUTRAL, regime=VolatilityRangeRegime.BALANCED):
    return EnvironmentAssessment(regime, ExpansionCharacter.NEUTRAL, alignment, risk, ContextDataQuality.VALID, (), (_ref(ContextDomain.VOLATILITY, "ENV:1"),))


def test_weak_participation_alone_is_low_not_material():
    result = assess_conflict(
        StructuralDirection.LONG,
        reaction=_reaction(),
        participation=_participation(ParticipationState.WEAK),
        environment=_environment(),
    )
    assert result.state is ConflictState.LOW


def test_one_failed_reaction_family_is_material_not_high():
    result = assess_conflict(
        StructuralDirection.LONG,
        reaction=_reaction(failed=True),
        participation=_participation(),
        environment=_environment(),
    )
    assert result.state is ConflictState.MATERIAL


def test_two_independent_material_families_can_be_high():
    result = assess_conflict(
        StructuralDirection.LONG,
        reaction=_reaction(failed=True),
        participation=_participation(),
        environment=_environment(
            alignment=EnvironmentAlignment.OPPOSING,
            regime=VolatilityRangeRegime.EXPANDING,
        ),
    )
    assert result.state is ConflictState.HIGH


def test_shock_is_not_double_counted_as_high_conflict():
    result = assess_conflict(
        StructuralDirection.LONG,
        reaction=_reaction(),
        participation=_participation(),
        environment=_environment(risk=EnvironmentRisk.HARD_BLOCK, regime=VolatilityRangeRegime.SHOCK),
    )
    assert result.state is ConflictState.NONE


def test_opposing_expansion_is_material_environment_conflict():
    result = assess_conflict(
        StructuralDirection.LONG,
        reaction=_reaction(),
        participation=_participation(),
        environment=_environment(alignment=EnvironmentAlignment.OPPOSING, regime=VolatilityRangeRegime.EXPANDING),
    )
    assert result.state is ConflictState.MATERIAL


def test_unresolved_structure_means_unresolved_conflict():
    result = assess_conflict(
        StructuralDirection.UNRESOLVED,
        reaction=_reaction(failed=True),
        participation=_participation(ParticipationState.OPPOSING),
        environment=_environment(),
    )
    assert result.state is ConflictState.UNRESOLVED
