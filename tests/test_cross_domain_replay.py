from __future__ import annotations

from dataclasses import replace

import pytest

from financial_dashboard.context.axes import (
    ConflictState,
    ContextAxes,
    ContextDirection,
    ContinuationContext,
    HamReadinessContext,
    MTFContext,
    ObjectiveContext,
    ParticipationContext,
    PatternReadiness,
    ReactionContext,
    ReversalContext,
    StructuralThesis,
    VolatilityContext,
)
from financial_dashboard.context.builder import CrossDomainBuildResult
from financial_dashboard.context.permissions import resolve_permission
from financial_dashboard.context.snapshot import build_context_snapshot
from financial_dashboard.cross_domain_replay import (
    assert_prefix_stable,
    build_cross_domain_historical_replay,
    replay_signature,
    validate_replay_result,
)
from _context_step4_test_data import ref, zone_snapshot


def _result(as_of: int, *, thesis: StructuralThesis = StructuralThesis.DOWN) -> CrossDomainBuildResult:
    axes = ContextAxes(
        anchor_timeframe="4h",
        structural_thesis=thesis,
        structural_direction=ContextDirection.DOWN if thesis is StructuralThesis.DOWN else ContextDirection.UP,
        continuation=ContinuationContext.ABSENT,
        reaction=ReactionContext.NONE,
        reaction_direction=ContextDirection.NONE,
        reversal=ReversalContext.NOT_PRESENT,
        reversal_direction=ContextDirection.NONE,
        objective=ObjectiveContext.NONE,
        participation=ParticipationContext.UNAVAILABLE,
        volatility=VolatilityContext.UNAVAILABLE,
        pattern_readiness=PatternReadiness.UNAVAILABLE,
        mtf=MTFContext.UNRESOLVED,
        ham_readiness=HamReadinessContext.UNAVAILABLE,
        conflict=ConflictState.NONE,
        reasons=(),
    )
    zones = replace(zone_snapshot(price=100.0), as_of=as_of)
    known = ref(f"known-{as_of}", available_at=as_of, confirmed_at=as_of)
    future = ref(f"future-{as_of}", available_at=as_of + 1, confirmed_at=as_of + 1)
    context = build_context_snapshot(
        symbol="ASELS",
        as_of=as_of,
        anchor_timeframe="4h",
        axes=axes,
        zones=zones,
        all_fact_refs=(known, future),
    )
    return CrossDomainBuildResult(context=context, permission=resolve_permission(context))


def test_replay_signature_is_deterministic_and_excludes_future_source_refs() -> None:
    first = _result(10)
    second = _result(10)
    assert replay_signature(first) == replay_signature(second)
    assert tuple(ref.native_id for ref in first.context.source_refs) == ("known-10",)
    assert first.context.knowledge_boundary.excluded_future_fact_ids == ("future-10",)
    validate_replay_result(first, expected_as_of=10)


def test_historical_replay_records_transitions_without_action_authority() -> None:
    def build_at(as_of: int) -> CrossDomainBuildResult:
        return _result(as_of, thesis=StructuralThesis.DOWN if as_of < 12 else StructuralThesis.UP)

    replay = build_cross_domain_historical_replay((10, 11, 12), build_at=build_at)
    assert len(replay.points) == 3
    assert replay.latest is replay.points[-1].result
    assert any(
        item.field == "structural_thesis" and item.previous == "DOWN" and item.current == "UP"
        for item in replay.transitions
    )
    assert all(not point.result.permission.is_actionable_signal for point in replay.points)


def test_extended_future_points_do_not_rewrite_prefix_signatures() -> None:
    prefix = build_cross_domain_historical_replay((10, 11), build_at=_result)
    extended = build_cross_domain_historical_replay((10, 11, 12, 13), build_at=_result)
    assert_prefix_stable(prefix, extended)


def test_prefix_instability_is_detected() -> None:
    prefix = build_cross_domain_historical_replay((10, 11), build_at=_result)

    def changed(as_of: int) -> CrossDomainBuildResult:
        return _result(as_of, thesis=StructuralThesis.UP if as_of == 10 else StructuralThesis.DOWN)

    extended = build_cross_domain_historical_replay((10, 11, 12), build_at=changed)
    with pytest.raises(AssertionError, match="prefix instability"):
        assert_prefix_stable(prefix, extended)


def test_replay_points_must_be_strictly_increasing() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        build_cross_domain_historical_replay((10, 10), build_at=_result)
