from __future__ import annotations

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.context.projections import (
    StructuralFactsProjection,
    StructuralScopeProjection,
    StructuralTimeframeProjection,
)
from financial_dashboard.context.structural_levels import (
    StructuralLevelKind,
    StructuralLevelRole,
    StructuralLevelSide,
    build_structural_level_view,
)


def _scope(
    scope: str,
    *,
    weak_high: float | None,
    weak_low: float | None,
    protected_high: float | None,
    protected_low: float | None,
    weak_high_identity: int = 11,
    weak_low_identity: int = 12,
    protected_high_identity: int = 13,
    protected_low_identity: int = 14,
) -> StructuralScopeProjection:
    return StructuralScopeProjection(
        scope=scope,
        state="STATE_BULLISH",
        direction=1,
        protected_high=protected_high,
        protected_low=protected_low,
        weak_high=weak_high,
        weak_low=weak_low,
        strong_high_identity=1,
        strong_low_identity=2,
        protected_high_identity=protected_high_identity,
        protected_low_identity=protected_low_identity,
        weak_high_identity=weak_high_identity,
        weak_low_identity=weak_low_identity,
    )


def test_structural_level_view_keeps_objectives_and_boundaries_semantically_separate() -> None:
    structure = StructuralFactsProjection(
        symbol="ASELS",
        timeframes=("1d",),
        timeframe_facts=(
            StructuralTimeframeProjection(
                timeframe="1d",
                as_of="2026-08-27",
                data_quality=ContextDataQuality.VALID,
                external=_scope(
                    "EXTERNAL",
                    weak_high=112.0,
                    weak_low=94.0,
                    protected_high=118.0,
                    protected_low=90.0,
                ),
                internal=None,
                events=(),
            ),
        ),
    )

    view = build_structural_level_view(structure, current_price=100.0)

    objectives = {item.kind: item for item in view.objectives}
    boundaries = {item.kind: item for item in view.thesis_boundaries}

    assert objectives[StructuralLevelKind.WEAK_HIGH].role is StructuralLevelRole.STRUCTURAL_OBJECTIVE
    assert objectives[StructuralLevelKind.WEAK_HIGH].side is StructuralLevelSide.ABOVE
    assert objectives[StructuralLevelKind.WEAK_LOW].side is StructuralLevelSide.BELOW
    assert boundaries[StructuralLevelKind.PROTECTED_HIGH].role is StructuralLevelRole.THESIS_BOUNDARY
    assert boundaries[StructuralLevelKind.PROTECTED_LOW].side is StructuralLevelSide.BELOW


def test_structural_level_view_does_not_promote_unidentified_prices() -> None:
    structure = StructuralFactsProjection(
        symbol="ASELS",
        timeframes=("1h",),
        timeframe_facts=(
            StructuralTimeframeProjection(
                timeframe="1h",
                as_of="2026-08-27",
                data_quality=ContextDataQuality.VALID,
                external=_scope(
                    "EXTERNAL",
                    weak_high=105.0,
                    weak_low=95.0,
                    protected_high=108.0,
                    protected_low=92.0,
                    weak_high_identity=0,
                    protected_low_identity=0,
                ),
                internal=None,
                events=(),
            ),
        ),
    )

    view = build_structural_level_view(structure, current_price=100.0)
    kinds = {item.kind for item in view.levels}

    assert StructuralLevelKind.WEAK_HIGH not in kinds
    assert StructuralLevelKind.PROTECTED_LOW not in kinds
    assert StructuralLevelKind.WEAK_LOW in kinds
    assert StructuralLevelKind.PROTECTED_HIGH in kinds
