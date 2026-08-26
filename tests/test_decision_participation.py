from types import SimpleNamespace

from financial_dashboard.context.envelope import CausalFamily, ContextDataQuality, ContextDomain, FactRef, SourceFamily
from financial_dashboard.context.participation_behavior_projection import BreakParticipationBehavior, EffortResultBehavior, ParticipationTrend
from financial_dashboard.decision.participation import ParticipationState, assess_participation
from financial_dashboard.decision.structural import StructuralDirection


def _ref(quality=ContextDataQuality.VALID):
    return FactRef(ContextDomain.VOLUME, "PARTICIPATION_BEHAVIOR", "THYAO", "1h", "VOL:1", "TEST", 1, 1, 1, "VOL:1", CausalFamily.PARTICIPATION, SourceFamily.VOLUME_SERIES, quality)


def _projection(**overrides):
    values = dict(
        timeframe="1h", ref=_ref(), status="OK", evidence_direction=1,
        participation_trend=ParticipationTrend.CONFIRMED,
        effort_result=EffortResultBehavior.NEUTRAL,
        break_participation=BreakParticipationBehavior.NONE,
        participation_direction=1, break_direction=0, heavy_conflict=False,
    )
    values.update(overrides)
    row = SimpleNamespace(**values)
    return SimpleNamespace(for_timeframe=lambda timeframe: row)


def test_aligned_participation_is_supportive():
    result = assess_participation(StructuralDirection.LONG, _projection(), timeframe="1h")
    assert result.state is ParticipationState.SUPPORTIVE


def test_weak_volume_is_not_opposing():
    result = assess_participation(
        StructuralDirection.LONG,
        _projection(status="LOW_PARTICIPATION", evidence_direction=0, participation_direction=0, participation_trend=ParticipationTrend.NONE),
        timeframe="1h",
    )
    assert result.state is ParticipationState.WEAK


def test_low_participation_remains_weak_even_if_generic_direction_is_opposite():
    result = assess_participation(
        StructuralDirection.LONG,
        _projection(status="LOW_PARTICIPATION", evidence_direction=-1, participation_direction=-1, participation_trend=ParticipationTrend.NONE),
        timeframe="1h",
    )
    assert result.state is ParticipationState.WEAK


def test_opposing_participation_is_material_input():
    result = assess_participation(StructuralDirection.LONG, _projection(evidence_direction=-1, participation_direction=-1), timeframe="1h")
    assert result.state is ParticipationState.OPPOSING


def test_same_side_unsupported_break_is_weak():
    result = assess_participation(
        StructuralDirection.LONG,
        _projection(evidence_direction=0, participation_direction=0, break_direction=1, break_participation=BreakParticipationBehavior.UNSUPPORTED),
        timeframe="1h",
    )
    assert result.state is ParticipationState.WEAK
    assert result.unsupported_break
    assert "UNSUPPORTED_BREAK_ALIGNS_STRUCTURE" in result.reasons


def test_opposing_unsupported_break_does_not_weaken_structural_side():
    result = assess_participation(
        StructuralDirection.LONG,
        _projection(
            evidence_direction=0,
            participation_direction=0,
            participation_trend=ParticipationTrend.NONE,
            break_direction=-1,
            break_participation=BreakParticipationBehavior.UNSUPPORTED,
        ),
        timeframe="1h",
    )
    assert result.state is ParticipationState.NEUTRAL
    assert not result.unsupported_break
    assert "OPPOSING_BREAK_UNSUPPORTED" in result.reasons


def test_missing_or_degraded_is_unknown_not_neutral():
    assert assess_participation(StructuralDirection.LONG, None, timeframe="1h").state is ParticipationState.UNKNOWN
    result = assess_participation(StructuralDirection.LONG, _projection(ref=_ref(ContextDataQuality.DATA_LIMITED)), timeframe="1h")
    assert result.state is ParticipationState.UNKNOWN
