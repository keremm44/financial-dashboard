from __future__ import annotations

from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

from .axes import evaluate_context_axes
from .envelope import ContextDataQuality, FactRef
from .lineage import build_lineage_groups
from .permissions import PermissionEnvelope, resolve_permission
from .projections import (
    HamProjection,
    HamTimeframeProjection,
    LiquidityProjection,
    ParticipationProjection,
    PatternProjection,
    PatternTimeframeProjection,
    ReactionEvidenceProjection,
    StabilSupportProjection,
    StructuralFactsProjection,
    VolatilityProjection,
    VolatilityTimeframeProjection,
    project_ham,
    project_liquidity,
    project_participation,
    project_pattern,
    project_reaction_evidence,
    project_stabil_support,
    project_structural_facts,
    project_volatility,
)
from .snapshot import CrossDomainContextSnapshot, build_context_snapshot
from .zones import ZoneIntelligenceConfig, build_zone_intelligence


@dataclass(frozen=True, slots=True)
class CrossDomainBuildResult:
    """Shadow output produced beside the existing workspace domains."""

    context: CrossDomainContextSnapshot
    permission: PermissionEnvelope


@dataclass(frozen=True, slots=True)
class CrossDomainBuildInputs:
    symbol: str
    as_of: Any
    anchor_timeframe: str
    current_price: float
    structure_location: Any
    available_at: Any
    data_quality_by_timeframe: Mapping[str, Any]
    reference_atr_by_timeframe: Mapping[str, float]
    pattern_replay: Any | None = None
    liquidity_replay: Any | None = None
    order_block_replay: Any | None = None
    fvg_engulfing_replay: Any | None = None
    stabil_support_replay: Any | None = None
    participation_replay: Any | None = None
    volatility_replay: Any | None = None
    ham_replay: Any | None = None
    requested_timeframes: tuple[str, ...] = ()
    trigger_timeframes: tuple[str, ...] = ("1h", "30m")
    unsupported_contexts: tuple[str, ...] = ()
    zone_config: ZoneIntelligenceConfig | None = None


def _available(ref: FactRef | None, as_of: Any) -> bool:
    return ref is not None and ref.is_available_at(as_of)


def _structure_projection_source(structure_location: Any) -> Any:
    """Expose the thin structural projection shape without changing replay classes."""

    if hasattr(structure_location, "structure_for"):
        return structure_location
    return SimpleNamespace(
        symbol=structure_location.symbol,
        timeframes=tuple(structure_location.timeframes),
        replays=structure_location.replays,
        structure_for=lambda timeframe: structure_location.replay_for(timeframe).market_structure,
    )


def _all_structural_refs(projection: StructuralFactsProjection) -> tuple[FactRef, ...]:
    return tuple(event.ref for tf in projection.timeframe_facts for event in tf.events)


def _all_liquidity_refs(projection: LiquidityProjection | None) -> tuple[FactRef, ...]:
    return () if projection is None else tuple(item.ref for item in projection.observations)


def _all_reaction_refs(projection: ReactionEvidenceProjection | None) -> tuple[FactRef, ...]:
    if projection is None:
        return ()
    return tuple(item.ref for item in (*projection.reaction_zones, *projection.confirmations))


def _all_stabil_refs(projection: StabilSupportProjection | None) -> tuple[FactRef, ...]:
    if projection is None:
        return ()
    refs: list[FactRef] = []
    if projection.support_ref is not None:
        refs.append(projection.support_ref)
    refs.extend(event.ref for event in projection.events)
    return tuple(refs)


def _all_participation_refs(projection: ParticipationProjection | None) -> tuple[FactRef, ...]:
    return () if projection is None else tuple(item.ref for item in projection.timeframe_facts)


def _all_pattern_refs(projection: PatternProjection | None) -> tuple[FactRef, ...]:
    if projection is None:
        return ()
    return tuple(item.ref for item in projection.timeframe_facts if item.ref is not None)


def _all_volatility_refs(projection: VolatilityProjection | None) -> tuple[FactRef, ...]:
    if projection is None:
        return ()
    return tuple(item.ref for item in projection.timeframe_facts if item.ref is not None)


def _all_ham_refs(projection: HamProjection | None) -> tuple[FactRef, ...]:
    if projection is None:
        return ()
    return tuple(family.ref for item in projection.timeframe_facts for family in item.families)


def _filter_structural(projection: StructuralFactsProjection, as_of: Any) -> StructuralFactsProjection:
    rows = tuple(
        replace(item, events=tuple(event for event in item.events if event.ref.is_available_at(as_of)))
        for item in projection.timeframe_facts
    )
    return replace(projection, timeframe_facts=rows)


def _filter_liquidity(projection: LiquidityProjection | None, as_of: Any) -> LiquidityProjection | None:
    if projection is None:
        return None
    return replace(
        projection,
        observations=tuple(item for item in projection.observations if item.ref.is_available_at(as_of)),
    )


def _filter_reaction(
    projection: ReactionEvidenceProjection | None,
    as_of: Any,
) -> ReactionEvidenceProjection | None:
    if projection is None:
        return None
    return replace(
        projection,
        reaction_zones=tuple(item for item in projection.reaction_zones if item.ref.is_available_at(as_of)),
        confirmations=tuple(item for item in projection.confirmations if item.ref.is_available_at(as_of)),
    )


def _filter_stabil(
    projection: StabilSupportProjection | None,
    as_of: Any,
) -> StabilSupportProjection | None:
    if projection is None:
        return None
    support_available = _available(projection.support_ref, as_of)
    return replace(
        projection,
        support_ref=projection.support_ref if support_available else None,
        support_level=projection.support_level if support_available else None,
        support_floor=projection.support_floor if support_available else None,
        events=tuple(event for event in projection.events if event.ref.is_available_at(as_of)),
        data_quality=projection.data_quality if support_available else ContextDataQuality.UNAVAILABLE,
    )


def _filter_participation(
    projection: ParticipationProjection | None,
    as_of: Any,
) -> ParticipationProjection | None:
    if projection is None:
        return None
    rows = tuple(item for item in projection.timeframe_facts if item.ref.is_available_at(as_of))
    return replace(projection, timeframe_facts=rows)


def _filter_pattern(projection: PatternProjection | None, as_of: Any) -> PatternProjection | None:
    if projection is None:
        return None
    rows: list[PatternTimeframeProjection] = []
    for item in projection.timeframe_facts:
        if item.ref is None or item.ref.is_available_at(as_of):
            rows.append(item)
            continue
        rows.append(
            replace(
                item,
                data_quality=ContextDataQuality.UNAVAILABLE,
                ref=None,
                pattern_state_code=None,
                pattern_type_code=None,
                classic_direction=None,
                break_state_code=None,
                break_level=None,
                retest_state_code=None,
                identity=None,
            )
        )
    return replace(projection, timeframe_facts=tuple(rows))


def _filter_volatility(
    projection: VolatilityProjection | None,
    as_of: Any,
) -> VolatilityProjection | None:
    if projection is None:
        return None
    rows: list[VolatilityTimeframeProjection] = []
    for item in projection.timeframe_facts:
        if item.ref is None or item.ref.is_available_at(as_of):
            rows.append(item)
            continue
        rows.append(
            replace(
                item,
                data_quality=ContextDataQuality.UNAVAILABLE,
                ref=None,
                regime_code=None,
                band_state_code=None,
                fib_state_code=None,
                active_swing_direction=0,
                fib_retracement_ratio=None,
                early_state="NONE",
                early_episode_id=0,
                early_episode_started=False,
            )
        )
    return replace(projection, timeframe_facts=tuple(rows))


def _filter_ham(projection: HamProjection | None, as_of: Any) -> HamProjection | None:
    if projection is None:
        return None
    rows: list[HamTimeframeProjection] = []
    for item in projection.timeframe_facts:
        families = tuple(family for family in item.families if family.ref.is_available_at(as_of))
        rows.append(
            replace(
                item,
                families=families,
                data_quality=item.data_quality if families else ContextDataQuality.UNAVAILABLE,
            )
        )
    return replace(projection, timeframe_facts=tuple(rows))


def _unsupported_tokens(
    reaction: ReactionEvidenceProjection | None,
    explicit: Iterable[str],
) -> tuple[str, ...]:
    values = {str(item).strip() for item in explicit if str(item).strip()}
    if reaction is not None:
        values.update(
            f"FVG_ENGULFING_UNSUPPORTED:{timeframe}"
            for timeframe in reaction.unsupported_fvg_engulfing_timeframes
        )
    return tuple(sorted(values))


def build_cross_domain_context(inputs: CrossDomainBuildInputs) -> CrossDomainBuildResult:
    """Build one deterministic, knowledge-bounded shadow context result."""

    structural_raw = project_structural_facts(
        _structure_projection_source(inputs.structure_location),
        available_at=inputs.available_at,
    )
    liquidity_raw = (
        None
        if inputs.liquidity_replay is None
        else project_liquidity(
            inputs.liquidity_replay,
            data_quality_by_timeframe=inputs.data_quality_by_timeframe,
        )
    )
    reaction_raw = project_reaction_evidence(
        symbol=inputs.symbol,
        order_block_replay=inputs.order_block_replay,
        fvg_engulfing_replay=inputs.fvg_engulfing_replay,
        data_quality_by_timeframe=inputs.data_quality_by_timeframe,
        requested_timeframes=inputs.requested_timeframes,
    )
    stabil_raw = None if inputs.stabil_support_replay is None else project_stabil_support(inputs.stabil_support_replay)
    participation_raw = (
        None
        if inputs.participation_replay is None
        else project_participation(inputs.participation_replay, available_at=inputs.available_at)
    )
    pattern_raw = (
        None
        if inputs.pattern_replay is None
        else project_pattern(inputs.pattern_replay, available_at=inputs.available_at)
    )
    volatility_raw = (
        None
        if inputs.volatility_replay is None
        else project_volatility(inputs.volatility_replay, available_at=inputs.available_at)
    )
    ham_raw = (
        None
        if inputs.ham_replay is None
        else project_ham(inputs.ham_replay, available_at=inputs.available_at)
    )

    all_refs = tuple(
        (
            *_all_structural_refs(structural_raw),
            *_all_liquidity_refs(liquidity_raw),
            *_all_reaction_refs(reaction_raw),
            *_all_stabil_refs(stabil_raw),
            *_all_participation_refs(participation_raw),
            *_all_pattern_refs(pattern_raw),
            *_all_volatility_refs(volatility_raw),
            *_all_ham_refs(ham_raw),
        )
    )

    structural = _filter_structural(structural_raw, inputs.as_of)
    liquidity = _filter_liquidity(liquidity_raw, inputs.as_of)
    reaction = _filter_reaction(reaction_raw, inputs.as_of)
    stabil = _filter_stabil(stabil_raw, inputs.as_of)
    participation = _filter_participation(participation_raw, inputs.as_of)
    pattern = _filter_pattern(pattern_raw, inputs.as_of)
    volatility = _filter_volatility(volatility_raw, inputs.as_of)
    ham = _filter_ham(ham_raw, inputs.as_of)

    zones = build_zone_intelligence(
        symbol=inputs.symbol,
        as_of=inputs.as_of,
        current_price=inputs.current_price,
        structure_location=inputs.structure_location,
        structural=structural,
        reaction=reaction,
        liquidity=liquidity,
        stabil_support=stabil,
        reference_atr_by_timeframe=inputs.reference_atr_by_timeframe,
        config=inputs.zone_config,
    )
    axes = evaluate_context_axes(
        structural=structural,
        zones=zones,
        anchor_timeframe=inputs.anchor_timeframe,
        liquidity=liquidity,
        participation=participation,
        pattern=pattern,
        volatility=volatility,
        ham=ham,
        trigger_timeframes=inputs.trigger_timeframes,
    )
    eligible_refs = tuple(ref for ref in all_refs if ref.is_available_at(inputs.as_of))
    context = build_context_snapshot(
        symbol=inputs.symbol,
        as_of=inputs.as_of,
        anchor_timeframe=inputs.anchor_timeframe,
        axes=axes,
        zones=zones,
        all_fact_refs=all_refs,
        lineage_groups=build_lineage_groups(eligible_refs),
        unsupported_contexts=_unsupported_tokens(reaction_raw, inputs.unsupported_contexts),
    )
    return CrossDomainBuildResult(context=context, permission=resolve_permission(context))


__all__ = ["CrossDomainBuildInputs", "CrossDomainBuildResult", "build_cross_domain_context"]
