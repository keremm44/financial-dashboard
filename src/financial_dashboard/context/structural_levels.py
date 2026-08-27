from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .envelope import ContextDataQuality
from .projections import StructuralFactsProjection, StructuralScopeProjection


class StructuralLevelKind(StrEnum):
    WEAK_HIGH = "WEAK_HIGH"
    WEAK_LOW = "WEAK_LOW"
    PROTECTED_HIGH = "PROTECTED_HIGH"
    PROTECTED_LOW = "PROTECTED_LOW"


class StructuralLevelRole(StrEnum):
    STRUCTURAL_OBJECTIVE = "STRUCTURAL_OBJECTIVE"
    THESIS_BOUNDARY = "THESIS_BOUNDARY"


class StructuralLevelSide(StrEnum):
    ABOVE = "ABOVE"
    BELOW = "BELOW"
    AT_PRICE = "AT_PRICE"


@dataclass(frozen=True, slots=True)
class StructuralLevelObservation:
    timeframe: str
    scope: str
    kind: StructuralLevelKind
    role: StructuralLevelRole
    price: float
    identity: int
    side: StructuralLevelSide
    data_quality: ContextDataQuality


@dataclass(frozen=True, slots=True)
class StructuralLevelView:
    symbol: str
    current_price: float
    levels: tuple[StructuralLevelObservation, ...]

    @property
    def objectives(self) -> tuple[StructuralLevelObservation, ...]:
        return tuple(
            item
            for item in self.levels
            if item.role is StructuralLevelRole.STRUCTURAL_OBJECTIVE
        )

    @property
    def thesis_boundaries(self) -> tuple[StructuralLevelObservation, ...]:
        return tuple(
            item
            for item in self.levels
            if item.role is StructuralLevelRole.THESIS_BOUNDARY
        )

    def objectives_on(self, side: StructuralLevelSide) -> tuple[StructuralLevelObservation, ...]:
        return tuple(item for item in self.objectives if item.side is side)


def _side(price: float, current_price: float) -> StructuralLevelSide:
    if price > current_price:
        return StructuralLevelSide.ABOVE
    if price < current_price:
        return StructuralLevelSide.BELOW
    return StructuralLevelSide.AT_PRICE


def _append_level(
    rows: list[StructuralLevelObservation],
    *,
    timeframe: str,
    scope: StructuralScopeProjection,
    kind: StructuralLevelKind,
    role: StructuralLevelRole,
    price: float | None,
    identity: int,
    current_price: float,
    data_quality: ContextDataQuality,
) -> None:
    # A structural level without a native identity is not promoted into the decision
    # read model. This view is descriptive only and does not invent lineage or a new
    # causal fact around an unidentified price.
    if price is None or identity <= 0:
        return
    value = float(price)
    rows.append(
        StructuralLevelObservation(
            timeframe=timeframe,
            scope=scope.scope,
            kind=kind,
            role=role,
            price=value,
            identity=int(identity),
            side=_side(value, current_price),
            data_quality=data_quality,
        )
    )


def build_structural_level_view(
    structure: StructuralFactsProjection,
    *,
    current_price: float,
) -> StructuralLevelView:
    """Expose weak objectives and protected thesis boundaries without new authority.

    Weak High/Low remain structural objective candidates; Protected High/Low remain
    thesis-boundary references. The view does not claim that a weak level must be
    reached, does not convert protected levels into targets, and does not create new
    FactRefs or independent evidence.
    """

    rows: list[StructuralLevelObservation] = []
    for timeframe_fact in structure.timeframe_facts:
        for scope in (timeframe_fact.external, timeframe_fact.internal):
            if scope is None:
                continue
            _append_level(
                rows,
                timeframe=timeframe_fact.timeframe,
                scope=scope,
                kind=StructuralLevelKind.WEAK_HIGH,
                role=StructuralLevelRole.STRUCTURAL_OBJECTIVE,
                price=scope.weak_high,
                identity=scope.weak_high_identity,
                current_price=float(current_price),
                data_quality=timeframe_fact.data_quality,
            )
            _append_level(
                rows,
                timeframe=timeframe_fact.timeframe,
                scope=scope,
                kind=StructuralLevelKind.WEAK_LOW,
                role=StructuralLevelRole.STRUCTURAL_OBJECTIVE,
                price=scope.weak_low,
                identity=scope.weak_low_identity,
                current_price=float(current_price),
                data_quality=timeframe_fact.data_quality,
            )
            _append_level(
                rows,
                timeframe=timeframe_fact.timeframe,
                scope=scope,
                kind=StructuralLevelKind.PROTECTED_HIGH,
                role=StructuralLevelRole.THESIS_BOUNDARY,
                price=scope.protected_high,
                identity=scope.protected_high_identity,
                current_price=float(current_price),
                data_quality=timeframe_fact.data_quality,
            )
            _append_level(
                rows,
                timeframe=timeframe_fact.timeframe,
                scope=scope,
                kind=StructuralLevelKind.PROTECTED_LOW,
                role=StructuralLevelRole.THESIS_BOUNDARY,
                price=scope.protected_low,
                identity=scope.protected_low_identity,
                current_price=float(current_price),
                data_quality=timeframe_fact.data_quality,
            )

    return StructuralLevelView(
        symbol=structure.symbol,
        current_price=float(current_price),
        levels=tuple(
            sorted(
                rows,
                key=lambda item: (
                    item.timeframe,
                    item.scope,
                    item.role.value,
                    item.kind.value,
                    item.price,
                    item.identity,
                ),
            )
        ),
    )


__all__ = [
    "StructuralLevelKind",
    "StructuralLevelObservation",
    "StructuralLevelRole",
    "StructuralLevelSide",
    "StructuralLevelView",
    "build_structural_level_view",
]
