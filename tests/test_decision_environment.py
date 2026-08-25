from types import SimpleNamespace

from financial_dashboard.context.envelope import CausalFamily, ContextDataQuality, ContextDomain, FactRef, SourceFamily
from financial_dashboard.context.volatility_environment_projection import ExpansionCharacter, VolatilityRangeRegime
from financial_dashboard.decision.environment import EnvironmentAlignment, EnvironmentRisk, assess_environment
from financial_dashboard.decision.structural import StructuralDirection


def _ref(quality=ContextDataQuality.VALID):
    return FactRef(ContextDomain.VOLATILITY, "VOLATILITY_ENVIRONMENT", "THYAO", "1h", "VOLATILITY:1", "TEST", 1, 1, 1, "VOLATILITY:1", CausalFamily.REGIME, SourceFamily.PRICE_DERIVED_INDICATOR, quality)


def _projection(*, regime=VolatilityRangeRegime.BALANCED, character=ExpansionCharacter.NEUTRAL, expansion_direction=0, quality=ContextDataQuality.VALID):
    row = SimpleNamespace(ref=_ref(quality), range_regime=regime, expansion_character=character, expansion_direction=expansion_direction)
    return SimpleNamespace(for_timeframe=lambda timeframe: row)


def test_balanced_environment_is_normal():
    result = assess_environment(StructuralDirection.LONG, _projection(), timeframe="1h")
    assert result.risk is EnvironmentRisk.NORMAL
    assert result.alignment is EnvironmentAlignment.NEUTRAL


def test_aligned_expansion_is_not_direction_authority():
    result = assess_environment(StructuralDirection.LONG, _projection(regime=VolatilityRangeRegime.EXPANDING, expansion_direction=1), timeframe="1h")
    assert result.alignment is EnvironmentAlignment.ALIGNED
    assert result.risk is EnvironmentRisk.NORMAL


def test_opposing_expansion_is_descriptive_only():
    result = assess_environment(StructuralDirection.LONG, _projection(regime=VolatilityRangeRegime.EXPANDING, expansion_direction=-1), timeframe="1h")
    assert result.alignment is EnvironmentAlignment.OPPOSING


def test_unstable_conflict_is_elevated_not_hard_gate():
    result = assess_environment(StructuralDirection.LONG, _projection(character=ExpansionCharacter.UNSTABLE_CONFLICT), timeframe="1h")
    assert result.risk is EnvironmentRisk.ELEVATED


def test_false_excursion_remains_descriptive_until_calibrated():
    result = assess_environment(StructuralDirection.LONG, _projection(character=ExpansionCharacter.FALSE_EXCURSION), timeframe="1h")
    assert result.risk is EnvironmentRisk.NORMAL
    assert result.character is ExpansionCharacter.FALSE_EXCURSION


def test_shock_is_marked_hard_block_for_later_gate_layer():
    result = assess_environment(StructuralDirection.LONG, _projection(regime=VolatilityRangeRegime.SHOCK), timeframe="1h")
    assert result.risk is EnvironmentRisk.HARD_BLOCK


def test_missing_or_degraded_volatility_is_unknown():
    assert assess_environment(StructuralDirection.LONG, None, timeframe="1h").risk is EnvironmentRisk.UNKNOWN
    result = assess_environment(StructuralDirection.LONG, _projection(quality=ContextDataQuality.DATA_LIMITED), timeframe="1h")
    assert result.risk is EnvironmentRisk.UNKNOWN
