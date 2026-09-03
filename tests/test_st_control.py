from __future__ import annotations

from dataclasses import fields
from types import SimpleNamespace

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.context.participation_behavior_projection import (
    BreakParticipationBehavior,
    EffortResultBehavior,
    ParticipationTrend,
)
from financial_dashboard.context.pattern_behavior_projection import PatternBehaviorPhase
from financial_dashboard.decision.st_control import (
    ChallengerCondition,
    ControlEvidenceRole,
    IncumbentCondition,
    ShortTermControlAssessment,
    ShortTermControlState,
    assess_short_term_control,
)
from financial_dashboard.decision.structural import (
    DecisionHorizon,
    StructuralAssessment,
    StructuralDirection,
    ThesisState,
)


class _Projection:
    def __init__(self, rows: dict[str, object]):
        self._rows = rows

    def for_timeframe(self, timeframe: str):
        normalized = timeframe.strip().lower()
        if normalized not in self._rows:
            raise KeyError(normalized)
        return self._rows[normalized]


def _ref(
    *,
    domain: ContextDomain,
    fact_type: str,
    timeframe: str,
    native_id: str,
    available_at: int = 10,
    quality: ContextDataQuality = ContextDataQuality.VALID,
    lineage_id: str | None = None,
) -> FactRef:
    causal = {
        ContextDomain.MARKET_STRUCTURE: CausalFamily.STRUCTURAL_LEVEL,
        ContextDomain.SUPPORT_RESISTANCE: CausalFamily.STRUCTURAL_LEVEL,
        ContextDomain.VOLUME: CausalFamily.PARTICIPATION,
        ContextDomain.PATTERN: CausalFamily.REGIME,
        ContextDomain.ORDER_BLOCK: CausalFamily.IMPULSE,
        ContextDomain.FVG: CausalFamily.IMPULSE,
        ContextDomain.ENGULFING: CausalFamily.IMPULSE,
    }[domain]
    source = (
        SourceFamily.VOLUME_SERIES
        if domain is ContextDomain.VOLUME
        else SourceFamily.PRICE_GEOMETRY
    )
    return FactRef(
        domain=domain,
        fact_type=fact_type,
        symbol="ASELS",
        timeframe=timeframe,
        native_id=native_id,
        native_state="TEST",
        origin_time=available_at,
        confirmed_at=available_at,
        available_at=available_at,
        lineage_id=lineage_id,
        causal_family=causal,
        source_family=source,
        data_quality=quality,
    )


def _structural(
    side: StructuralDirection,
    *,
    transitioning: bool = False,
    native_state: str | None = None,
) -> StructuralAssessment:
    transition_target = (
        StructuralDirection.SHORT
        if side is StructuralDirection.LONG and transitioning
        else StructuralDirection.LONG
        if side is StructuralDirection.SHORT and transitioning
        else None
    )
    if native_state is None:
        if transitioning:
            native_state = (
                "STATE_TRANSITION_DOWN"
                if side is StructuralDirection.LONG
                else "STATE_TRANSITION_UP"
            )
        else:
            native_state = "STATE_BULLISH" if side is StructuralDirection.LONG else "STATE_BEARISH"
    return StructuralAssessment(
        horizon=DecisionHorizon.SHORT_TERM,
        authority_timeframe="1h",
        direction=side,
        thesis_state=ThesisState.TRANSITIONING if transitioning else ThesisState.INTACT,
        native_state=native_state,
        transition_target=transition_target,
        data_quality=ContextDataQuality.VALID,
        authority_as_of=10,
        protected_high=None,
        protected_low=None,
        weak_high=None,
        weak_low=None,
        source_refs=(_ref(
            domain=ContextDomain.MARKET_STRUCTURE,
            fact_type="EVENT_CHOCH" if transitioning else "EVENT_BOS",
            timeframe="1h",
            native_id=f"STRUCT:{side.value}:{'T' if transitioning else 'I'}",
        ),),
        reasons=("TEST_STRUCTURE",),
    )


def _participation_row(
    *,
    break_direction: int = 0,
    break_behavior: BreakParticipationBehavior = BreakParticipationBehavior.NONE,
    participation_direction: int = 0,
    trend: ParticipationTrend = ParticipationTrend.NONE,
    evidence_direction: int = 0,
    effort: EffortResultBehavior = EffortResultBehavior.NEUTRAL,
    quality: ContextDataQuality = ContextDataQuality.VALID,
    lineage_id: str | None = None,
    available_at: int = 10,
):
    return SimpleNamespace(
        ref=_ref(
            domain=ContextDomain.VOLUME,
            fact_type="PARTICIPATION_BEHAVIOR",
            timeframe="1h",
            native_id=(
                f"VOL:{break_direction}:{break_behavior.value}:"
                f"{participation_direction}:{trend.value}:{effort.value}:{available_at}"
            ),
            available_at=available_at,
            quality=quality,
            lineage_id=lineage_id,
        ),
        participation_trend=trend,
        effort_result=effort,
        break_participation=break_behavior,
        participation_direction=participation_direction,
        evidence_direction=evidence_direction,
        break_direction=break_direction,
    )


def _pattern_row(
    side: StructuralDirection,
    phase: PatternBehaviorPhase,
    *,
    quality: ContextDataQuality = ContextDataQuality.VALID,
    native_state: str | None = None,
):
    direction = 1 if side is StructuralDirection.LONG else -1
    if native_state is None:
        native_state = {
            PatternBehaviorPhase.BREAK_CONFIRMED: "KIRILIM_TEYITLI",
            PatternBehaviorPhase.RETEST_HELD: "RETEST_BASARILI",
            PatternBehaviorPhase.BREAK_FAILED: "BASARISIZ_KIRILIM",
            PatternBehaviorPhase.BREAK_ATTEMPT: "KIRILIM_DENEMESI",
        }.get(phase, "SIKISMA_GUCLENIYOR")
    return SimpleNamespace(
        ref=_ref(
            domain=ContextDomain.PATTERN,
            fact_type="PATTERN_BEHAVIOR",
            timeframe="30m",
            native_id=f"PATTERN:{side.value}:{phase.value}:{quality.value}",
            quality=quality,
        ),
        phase=phase,
        native_state=native_state,
        classic_direction=direction,
    )


def _structure_row(
    timeframe: str,
    side: StructuralDirection,
    *,
    transition_confirmation: bool = False,
):
    value = 1 if side is StructuralDirection.LONG else -1
    state = "STATE_BULLISH" if value > 0 else "STATE_BEARISH"
    event = SimpleNamespace(
        ref=_ref(
            domain=ContextDomain.MARKET_STRUCTURE,
            fact_type="EVENT_BOS",
            timeframe=timeframe,
            native_id=f"MS:{timeframe}:{side.value}:{transition_confirmation}",
        ),
        scope="EXTERNAL",
        event_type="EVENT_BOS",
        direction=value,
        confirmation_status="CONFIRMED",
        validity="VALID",
        relevance="CURRENT",
        outcome="OBSERVED",
        bos_maturity="TRANSITION_CONFIRMATION" if transition_confirmation else "CONTINUATION",
    )
    scope = SimpleNamespace(state=state, direction=value)
    return SimpleNamespace(
        timeframe=timeframe,
        data_quality=ContextDataQuality.VALID,
        external=scope,
        internal=scope,
        events=(event,),
    )


def _snapshot(
    *,
    structure_rows: dict[str, object] | None = None,
    participation_row=None,
    pattern_row=None,
):
    return SimpleNamespace(
        symbol="ASELS",
        as_of=10,
        structure=_Projection(structure_rows or {}),
        participation_behavior=(
            None if participation_row is None else _Projection({"1h": participation_row})
        ),
        pattern_behavior=(
            None if pattern_row is None else _Projection({"30m": pattern_row})
        ),
        order_block_behavior=None,
        fvg_engulfing_lifecycle=None,
        support_resistance=None,
    )


def _developing_case(side: StructuralDirection):
    structural = _structural(side, transitioning=True)
    challenger = structural.transition_target
    assert challenger is not None
    challenger_value = 1 if challenger is StructuralDirection.LONG else -1
    snapshot = _snapshot(
        structure_rows={"30m": _structure_row("30m", challenger)},
        participation_row=_participation_row(
            break_direction=challenger_value,
            break_behavior=BreakParticipationBehavior.PROTECTED,
            participation_direction=challenger_value,
            trend=ParticipationTrend.PROTECTED,
            evidence_direction=challenger_value,
        ),
        pattern_row=_pattern_row(challenger, PatternBehaviorPhase.BREAK_CONFIRMED),
    )
    return assess_short_term_control(snapshot, structural=structural)


def test_contract_has_no_action_score_timing_or_execution_fields() -> None:
    names = {item.name for item in fields(ShortTermControlAssessment)}
    forbidden = {
        "buy",
        "sell",
        "hold",
        "score",
        "confidence",
        "points",
        "votes",
        "timing",
        "opportunity",
        "execution",
        "qualified",
        "eligible",
        "pnl",
        "mfe",
        "mae",
    }
    assert names.isdisjoint(forbidden)


def test_transition_supported_pattern_and_migration_is_developing_not_established() -> None:
    assessment = _developing_case(StructuralDirection.SHORT)

    assert assessment.control_state is ShortTermControlState.TRANSFER_DEVELOPING
    assert assessment.control_state is not ShortTermControlState.TRANSFER_ESTABLISHED
    assert assessment.challenger_condition in {
        ChallengerCondition.GAINING_GROUND,
        ChallengerCondition.DEFENDING_GROUND,
    }
    assert ControlEvidenceRole.CHALLENGER_ACCEPTANCE in {
        item.role for item in assessment.evidence
    }
    assert ControlEvidenceRole.CONTROL_MIGRATION in {
        item.role for item in assessment.evidence
    }


def test_up_down_semantics_are_symmetric() -> None:
    up = _developing_case(StructuralDirection.SHORT)
    down = _developing_case(StructuralDirection.LONG)

    assert up.control_state is ShortTermControlState.TRANSFER_DEVELOPING
    assert down.control_state is ShortTermControlState.TRANSFER_DEVELOPING
    assert up.incumbent_condition is down.incumbent_condition
    assert up.challenger_condition is down.challenger_condition
    assert {item.role for item in up.evidence} == {item.role for item in down.evidence}
    assert up.established_side is StructuralDirection.SHORT
    assert up.challenger_side is StructuralDirection.LONG
    assert down.established_side is StructuralDirection.LONG
    assert down.challenger_side is StructuralDirection.SHORT


def test_challenger_failure_with_incumbent_progress_during_transition_is_failed() -> None:
    structural = _structural(StructuralDirection.SHORT, transitioning=True)
    snapshot = _snapshot(
        participation_row=_participation_row(
            break_direction=1,
            break_behavior=BreakParticipationBehavior.UNSUPPORTED,
            participation_direction=-1,
            trend=ParticipationTrend.CONFIRMED,
            evidence_direction=-1,
        ),
        pattern_row=_pattern_row(StructuralDirection.LONG, PatternBehaviorPhase.BREAK_FAILED),
    )
    assessment = assess_short_term_control(snapshot, structural=structural)

    assert assessment.control_state is ShortTermControlState.TRANSFER_FAILED
    assert assessment.challenger_condition is ChallengerCondition.FAILING
    assert assessment.incumbent_condition is IncumbentCondition.PROGRESSING


def test_explicit_1h_transition_confirmation_can_establish_transfer() -> None:
    structural = _structural(StructuralDirection.LONG, transitioning=False)
    snapshot = _snapshot(
        structure_rows={
            "1h": _structure_row(
                "1h",
                StructuralDirection.LONG,
                transition_confirmation=True,
            )
        }
    )

    assessment = assess_short_term_control(snapshot, structural=structural)

    assert assessment.control_state is ShortTermControlState.TRANSFER_ESTABLISHED
    assert ControlEvidenceRole.TRANSFER_CONFIRMATION in {
        item.role for item in assessment.evidence
    }


def test_intact_structure_without_transition_confirmation_remains_control_held() -> None:
    structural = _structural(StructuralDirection.LONG, transitioning=False)
    snapshot = _snapshot(structure_rows={"1h": _structure_row("1h", StructuralDirection.LONG)})

    assessment = assess_short_term_control(snapshot, structural=structural)

    assert assessment.control_state is ShortTermControlState.CONTROL_HELD


def test_data_limited_pattern_native_phase_is_recovered_without_promoting_ref_quality() -> None:
    structural = _structural(StructuralDirection.SHORT, transitioning=True)
    snapshot = _snapshot(
        structure_rows={"30m": _structure_row("30m", StructuralDirection.LONG)},
        participation_row=_participation_row(
            break_direction=1,
            break_behavior=BreakParticipationBehavior.SUPPORTED,
            participation_direction=1,
            trend=ParticipationTrend.CONFIRMED,
        ),
        pattern_row=_pattern_row(
            StructuralDirection.LONG,
            PatternBehaviorPhase.UNAVAILABLE,
            quality=ContextDataQuality.DATA_LIMITED,
            native_state="KIRILIM_TEYITLI",
        ),
    )

    assessment = assess_short_term_control(snapshot, structural=structural)

    pattern_evidence = [
        item
        for item in assessment.evidence
        if any(ref.domain is ContextDomain.PATTERN for ref in item.source_refs)
    ]
    assert pattern_evidence
    assert all(
        ref.data_quality is ContextDataQuality.DATA_LIMITED
        for item in pattern_evidence
        for ref in item.source_refs
        if ref.domain is ContextDomain.PATTERN
    )
    assert assessment.data_quality is ContextDataQuality.DATA_LIMITED


def test_unknown_lineage_is_reported_not_fabricated_as_independence() -> None:
    assessment = _developing_case(StructuralDirection.SHORT)

    assert assessment.unresolved_lineage_refs
    assert all(ref.lineage_id is None for ref in assessment.unresolved_lineage_refs)
    assert all(group.lineage_id for group in assessment.lineage_groups)
    assert "UNKNOWN_LINEAGE_NOT_PROMOTED_TO_INDEPENDENCE" in assessment.reasons


def test_future_unavailable_optional_evidence_is_ignored() -> None:
    structural = _structural(StructuralDirection.SHORT, transitioning=True)
    snapshot = _snapshot(
        participation_row=_participation_row(
            break_direction=1,
            break_behavior=BreakParticipationBehavior.PROTECTED,
            participation_direction=1,
            trend=ParticipationTrend.PROTECTED,
            evidence_direction=1,
            available_at=11,
        )
    )

    assessment = assess_short_term_control(snapshot, structural=structural)

    assert not assessment.evidence
    assert assessment.control_state is ShortTermControlState.CONTROL_WEAKENING
    assert all(ref.available_at <= snapshot.as_of for ref in assessment.source_refs)
