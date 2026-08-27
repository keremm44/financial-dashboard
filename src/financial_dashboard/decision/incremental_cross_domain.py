from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Mapping

from financial_dashboard.context.axes import evaluate_context_axes
from financial_dashboard.context.builder import (
    CrossDomainBuildInputs,
    CrossDomainBuildResult,
    _all_fvg_engulfing_lifecycle_refs,
    _all_ham_refs,
    _all_liquidity_landscape_refs,
    _all_liquidity_refs,
    _all_order_block_behavior_refs,
    _all_participation_behavior_refs,
    _all_participation_refs,
    _all_pattern_behavior_refs,
    _all_pattern_refs,
    _all_reaction_refs,
    _all_stabil_refs,
    _all_structural_refs,
    _all_support_resistance_refs,
    _all_volatility_environment_refs,
    _all_volatility_refs,
    _filter_ham,
    _filter_liquidity,
    _filter_participation,
    _filter_pattern,
    _filter_reaction,
    _filter_stabil,
    _filter_structural,
    _filter_volatility,
    _unsupported_tokens,
)
from financial_dashboard.context.fvg_engulfing_projection import (
    FvgEngulfingLifecycleProjection,
    project_fvg_engulfing_lifecycle,
)
from financial_dashboard.context.lineage import build_lineage_groups
from financial_dashboard.context.liquidity_landscape_projection import (
    LiquidityLandscapeProjection,
    project_liquidity_landscape,
)
from financial_dashboard.context.order_block_behavior_projection import (
    OrderBlockBehaviorProjection,
    project_order_block_behavior,
)
from financial_dashboard.context.participation_behavior_projection import (
    ParticipationBehaviorProjection,
    project_participation_behavior,
)
from financial_dashboard.context.pattern_behavior_projection import (
    PatternBehaviorProjection,
    project_pattern_behavior,
)
from financial_dashboard.context.permissions import resolve_permission
from financial_dashboard.context.projections import (
    HamProjection,
    LiquidityProjection,
    ParticipationProjection,
    PatternProjection,
    ReactionEvidenceProjection,
    StructuralFactsProjection,
    VolatilityProjection,
    project_ham,
    project_liquidity,
    project_participation,
    project_pattern,
    project_reaction_evidence,
    project_stabil_support,
    project_structural_facts,
    project_volatility,
)
from financial_dashboard.context.snapshot import build_context_snapshot
from financial_dashboard.context.support_resistance_projection import (
    SupportResistanceProjection,
    project_support_resistance,
)
from financial_dashboard.context.volatility_environment_projection import (
    VolatilityEnvironmentProjection,
    project_volatility_environment,
)
from financial_dashboard.context.zones import build_zone_intelligence


def _one_structure(replay: Any, timeframe: str) -> Any:
    row = replay.replay_for(timeframe)

    def replay_for(requested: str) -> Any:
        if requested.strip().lower() != timeframe:
            raise KeyError(requested)
        return row

    return SimpleNamespace(
        symbol=replay.symbol,
        timeframes=(timeframe,),
        replays={timeframe: row},
        replay_for=replay_for,
        structure_for=lambda requested: replay_for(requested).market_structure,
    )


def _one_target(replay: Any | None, timeframe: str) -> Any | None:
    if replay is None or timeframe not in replay.timeframes:
        return None
    snapshot = replay.snapshots.get(timeframe)
    if snapshot is None:
        return None
    kwargs: dict[str, Any] = {
        "symbol": replay.symbol,
        "timeframes": (timeframe,),
        "snapshots": {timeframe: snapshot},
        "evidence": tuple(item for item in replay.evidence if item.timeframe == timeframe),
    }
    for name in (
        "liquidity_behavior",
        "order_block_behavior",
        "fvg_lifecycle",
        "engulfing_lifecycle",
    ):
        source = getattr(replay, name, None)
        if source is not None:
            kwargs[name] = {timeframe: source.get(timeframe)}
    return SimpleNamespace(**kwargs)


def _one_pattern(replay: Any | None, timeframe: str) -> Any | None:
    if replay is None or timeframe not in replay.timeframes:
        return None
    snapshots = tuple(
        item for item in replay.pattern_snapshots if item.timeframe == timeframe
    )
    if not snapshots:
        return None
    return SimpleNamespace(
        symbol=replay.symbol,
        timeframes=(timeframe,),
        structure_location=_one_structure(replay.structure_location, timeframe),
        pattern_snapshots=snapshots,
    )


def _one_participation(replay: Any | None, timeframe: str) -> Any | None:
    if replay is None or timeframe not in replay.timeframes:
        return None
    rows = tuple(
        item for item in replay.timeframe_replays if item.timeframe == timeframe
    )
    if not rows:
        return None
    return SimpleNamespace(
        symbol=replay.symbol,
        timeframes=(timeframe,),
        timeframe_replays=rows,
    )


def _one_volatility(replay: Any | None, timeframe: str) -> Any | None:
    if replay is None or timeframe not in replay.timeframes:
        return None
    row = replay.for_timeframe(timeframe)
    return SimpleNamespace(
        symbol=replay.symbol,
        timeframes=(timeframe,),
        for_timeframe=lambda requested: row
        if requested.strip().lower() == timeframe
        else (_ for _ in ()).throw(KeyError(requested)),
    )


def _one_ham(replay: Any | None, timeframe: str) -> Any | None:
    if replay is None or timeframe not in replay.timeframes:
        return None
    rows = tuple(
        item for item in replay.timeframe_replays if item.timeframe == timeframe
    )
    if not rows:
        return None
    return SimpleNamespace(
        symbol=replay.symbol,
        timeframes=(timeframe,),
        timeframe_replays=rows,
    )


def _participation_signature(replay: Any | None, timeframe: str, watermark: int) -> tuple[Any, ...]:
    one = _one_participation(replay, timeframe)
    if one is None:
        return (watermark, 0, None)
    row = one.timeframe_replays[0]
    links = tuple(getattr(row, "event_links", ()) or ())
    last = None if not links else getattr(links[-1], "assessed_at", None)
    return (watermark, len(links), last)


class IncrementalCrossDomainProjector:
    """Reuse immutable projection rows while causal timeframe watermarks are unchanged.

    The canonical cross-domain builder re-projects every timeframe on every 1h
    decision cutoff. Historical/live native state is already frozen per timeframe
    watermark, so higher-timeframe projection rows can be reused safely until that
    watermark advances. Final availability filtering, zone intelligence, axes,
    lineage, context and permission are still rebuilt for each decision ``as_of``.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[Any, ...], Any] = {}

    def _get(self, key: tuple[Any, ...], factory) -> Any:
        if key not in self._cache:
            self._cache[key] = factory()
        return self._cache[key]

    def build(
        self,
        inputs: CrossDomainBuildInputs,
        *,
        watermarks: Mapping[str, int],
    ) -> CrossDomainBuildResult:
        timeframes = tuple(inputs.structure_location.timeframes)

        structural_rows = []
        sr_rows = []
        liquidity_rows = []
        liquidity_behavior_rows = []
        landscape_rows = []
        reaction_zones = []
        reaction_confirmations = []
        ob_behavior_rows = []
        fvg_rows = []
        engulf_rows = []
        participation_rows = []
        participation_behavior_rows = []
        pattern_rows = []
        pattern_behavior_rows = []
        volatility_rows = []
        volatility_environment_rows = []
        ham_rows = []

        requested = tuple(
            sorted(
                {
                    str(tf).strip().lower()
                    for tf in inputs.requested_timeframes
                    if str(tf).strip()
                }
            )
        )
        fvg_supported = (
            set()
            if inputs.fvg_engulfing_replay is None
            else set(inputs.fvg_engulfing_replay.timeframes)
        )

        for timeframe in timeframes:
            watermark = int(watermarks[timeframe])
            one_structure = _one_structure(inputs.structure_location, timeframe)

            structural = self._get(
                ("structural", timeframe, watermark),
                lambda one=one_structure: project_structural_facts(
                    one,
                    available_at=inputs.available_at,
                ),
            )
            structural_rows.extend(structural.timeframe_facts)

            support = self._get(
                ("support_resistance", timeframe, watermark),
                lambda one=one_structure: project_support_resistance(
                    one,
                    data_quality_by_timeframe=inputs.data_quality_by_timeframe,
                ),
            )
            sr_rows.extend(support.timeframe_facts)

            one_liquidity = _one_target(inputs.liquidity_replay, timeframe)
            if one_liquidity is not None:
                liquidity = self._get(
                    ("liquidity", timeframe, watermark),
                    lambda one=one_liquidity: project_liquidity(
                        one,
                        data_quality_by_timeframe=inputs.data_quality_by_timeframe,
                    ),
                )
                liquidity_rows.extend(liquidity.observations)
                liquidity_behavior_rows.extend(liquidity.behavior_observations)

                landscape = self._get(
                    ("liquidity_landscape", timeframe, watermark),
                    lambda one=one_liquidity: project_liquidity_landscape(
                        one,
                        data_quality_by_timeframe=inputs.data_quality_by_timeframe,
                    ),
                )
                if landscape is not None:
                    landscape_rows.extend(landscape.observations)

            one_ob = _one_target(inputs.order_block_replay, timeframe)
            one_fvg = _one_target(inputs.fvg_engulfing_replay, timeframe)
            if one_ob is not None or one_fvg is not None:
                reaction = self._get(
                    ("reaction", timeframe, watermark),
                    lambda ob=one_ob, fvg=one_fvg, tf=timeframe: project_reaction_evidence(
                        symbol=inputs.symbol,
                        order_block_replay=ob,
                        fvg_engulfing_replay=fvg,
                        data_quality_by_timeframe=inputs.data_quality_by_timeframe,
                        requested_timeframes=(tf,),
                    ),
                )
                reaction_zones.extend(reaction.reaction_zones)
                reaction_confirmations.extend(reaction.confirmations)

            if one_ob is not None:
                ob_behavior = self._get(
                    ("ob_behavior", timeframe, watermark),
                    lambda one=one_ob: project_order_block_behavior(
                        one,
                        data_quality_by_timeframe=inputs.data_quality_by_timeframe,
                    ),
                )
                if ob_behavior is not None:
                    ob_behavior_rows.extend(ob_behavior.observations)

            if one_fvg is not None:
                lifecycle = self._get(
                    ("fvg_lifecycle", timeframe, watermark),
                    lambda one=one_fvg: project_fvg_engulfing_lifecycle(
                        one,
                        data_quality_by_timeframe=inputs.data_quality_by_timeframe,
                    ),
                )
                if lifecycle is not None:
                    fvg_rows.extend(lifecycle.fvg)
                    engulf_rows.extend(lifecycle.engulfing)

            one_participation = _one_participation(inputs.participation_replay, timeframe)
            if one_participation is not None:
                psig = _participation_signature(
                    inputs.participation_replay,
                    timeframe,
                    watermark,
                )
                participation = self._get(
                    ("participation", timeframe, *psig),
                    lambda one=one_participation: project_participation(
                        one,
                        available_at=inputs.available_at,
                    ),
                )
                participation_rows.extend(participation.timeframe_facts)

                participation_behavior = self._get(
                    ("participation_behavior", timeframe, *psig),
                    lambda one=one_participation: project_participation_behavior(
                        one,
                        available_at=inputs.available_at,
                    ),
                )
                if participation_behavior is not None:
                    participation_behavior_rows.extend(
                        participation_behavior.timeframe_facts
                    )

            one_pattern = _one_pattern(inputs.pattern_replay, timeframe)
            if one_pattern is not None:
                pattern = self._get(
                    ("pattern", timeframe, watermark),
                    lambda one=one_pattern: project_pattern(
                        one,
                        available_at=inputs.available_at,
                    ),
                )
                pattern_rows.extend(pattern.timeframe_facts)

                pattern_behavior = self._get(
                    ("pattern_behavior", timeframe, watermark),
                    lambda one=one_pattern: project_pattern_behavior(
                        one,
                        available_at=inputs.available_at,
                    ),
                )
                if pattern_behavior is not None:
                    pattern_behavior_rows.extend(pattern_behavior.timeframe_facts)

            one_volatility = _one_volatility(inputs.volatility_replay, timeframe)
            if one_volatility is not None:
                volatility = self._get(
                    ("volatility", timeframe, watermark),
                    lambda one=one_volatility: project_volatility(
                        one,
                        available_at=inputs.available_at,
                    ),
                )
                volatility_rows.extend(volatility.timeframe_facts)

                environment = self._get(
                    ("volatility_environment", timeframe, watermark),
                    lambda one=one_volatility: project_volatility_environment(
                        one,
                        available_at=inputs.available_at,
                    ),
                )
                if environment is not None:
                    volatility_environment_rows.extend(environment.timeframe_facts)

            one_ham = _one_ham(inputs.ham_replay, timeframe)
            if one_ham is not None:
                ham = self._get(
                    ("ham", timeframe, watermark),
                    lambda one=one_ham: project_ham(
                        one,
                        available_at=inputs.available_at,
                    ),
                )
                ham_rows.extend(ham.timeframe_facts)

        structural_raw = StructuralFactsProjection(
            symbol=inputs.symbol,
            timeframes=timeframes,
            timeframe_facts=tuple(structural_rows),
        )
        support_resistance_raw = SupportResistanceProjection(
            symbol=inputs.symbol,
            timeframes=timeframes,
            timeframe_facts=tuple(
                sorted(sr_rows, key=lambda item: item.ref.deterministic_key)
            ),
        )
        liquidity_raw = (
            None
            if inputs.liquidity_replay is None
            else LiquidityProjection(
                symbol=inputs.symbol,
                timeframes=timeframes,
                observations=tuple(
                    sorted(liquidity_rows, key=lambda item: item.ref.deterministic_key)
                ),
                behavior_observations=tuple(
                    sorted(
                        liquidity_behavior_rows,
                        key=lambda item: item.ref.deterministic_key,
                    )
                ),
            )
        )
        liquidity_landscape_raw = (
            None
            if inputs.liquidity_replay is None
            else LiquidityLandscapeProjection(
                symbol=inputs.symbol,
                timeframes=timeframes,
                observations=tuple(
                    sorted(landscape_rows, key=lambda item: item.ref.deterministic_key)
                ),
            )
        )
        unsupported = tuple(tf for tf in requested if tf not in fvg_supported) if requested else ()
        reaction_raw = ReactionEvidenceProjection(
            symbol=inputs.symbol,
            timeframes=tuple(
                sorted(
                    set(
                        (() if inputs.order_block_replay is None else inputs.order_block_replay.timeframes)
                    )
                    | set(
                        (() if inputs.fvg_engulfing_replay is None else inputs.fvg_engulfing_replay.timeframes)
                    )
                )
            ),
            reaction_zones=tuple(
                sorted(reaction_zones, key=lambda item: item.ref.deterministic_key)
            ),
            confirmations=tuple(
                sorted(
                    reaction_confirmations,
                    key=lambda item: item.ref.deterministic_key,
                )
            ),
            unsupported_fvg_engulfing_timeframes=unsupported,
        )
        order_block_behavior_raw = (
            None
            if inputs.order_block_replay is None
            else OrderBlockBehaviorProjection(
                symbol=inputs.symbol,
                timeframes=tuple(inputs.order_block_replay.timeframes),
                observations=tuple(
                    sorted(ob_behavior_rows, key=lambda item: item.ref.deterministic_key)
                ),
            )
        )
        fvg_engulfing_lifecycle_raw = (
            None
            if inputs.fvg_engulfing_replay is None
            else FvgEngulfingLifecycleProjection(
                symbol=inputs.symbol,
                timeframes=tuple(inputs.fvg_engulfing_replay.timeframes),
                fvg=tuple(sorted(fvg_rows, key=lambda item: item.ref.deterministic_key)),
                engulfing=tuple(
                    sorted(engulf_rows, key=lambda item: item.ref.deterministic_key)
                ),
            )
        )
        stabil_raw = (
            None
            if inputs.stabil_support_replay is None
            else self._get(
                ("stabil", "1d", int(watermarks.get("1d", -1))),
                lambda: project_stabil_support(inputs.stabil_support_replay),
            )
        )
        participation_raw = (
            None
            if inputs.participation_replay is None
            else ParticipationProjection(
                symbol=inputs.symbol,
                timeframes=tuple(inputs.participation_replay.timeframes),
                timeframe_facts=tuple(participation_rows),
            )
        )
        participation_behavior_raw = (
            None
            if inputs.participation_replay is None
            else ParticipationBehaviorProjection(
                symbol=inputs.symbol,
                timeframes=tuple(inputs.participation_replay.timeframes),
                timeframe_facts=tuple(participation_behavior_rows),
            )
        )
        pattern_raw = (
            None
            if inputs.pattern_replay is None
            else PatternProjection(
                symbol=inputs.symbol,
                timeframes=tuple(inputs.pattern_replay.timeframes),
                timeframe_facts=tuple(pattern_rows),
            )
        )
        pattern_behavior_raw = (
            None
            if inputs.pattern_replay is None
            else PatternBehaviorProjection(
                symbol=inputs.symbol,
                timeframes=tuple(inputs.pattern_replay.timeframes),
                timeframe_facts=tuple(pattern_behavior_rows),
            )
        )
        volatility_raw = (
            None
            if inputs.volatility_replay is None
            else VolatilityProjection(
                symbol=inputs.symbol,
                timeframes=tuple(inputs.volatility_replay.timeframes),
                timeframe_facts=tuple(volatility_rows),
            )
        )
        volatility_environment_raw = (
            None
            if inputs.volatility_replay is None
            else VolatilityEnvironmentProjection(
                symbol=inputs.symbol,
                timeframes=tuple(inputs.volatility_replay.timeframes),
                timeframe_facts=tuple(volatility_environment_rows),
            )
        )
        ham_raw = (
            None
            if inputs.ham_replay is None
            else HamProjection(
                symbol=inputs.symbol,
                timeframes=tuple(inputs.ham_replay.timeframes),
                timeframe_facts=tuple(ham_rows),
            )
        )

        all_refs = tuple(
            (
                *_all_structural_refs(structural_raw),
                *_all_support_resistance_refs(support_resistance_raw),
                *_all_liquidity_refs(liquidity_raw),
                *_all_liquidity_landscape_refs(liquidity_landscape_raw),
                *_all_reaction_refs(reaction_raw),
                *_all_order_block_behavior_refs(order_block_behavior_raw),
                *_all_fvg_engulfing_lifecycle_refs(fvg_engulfing_lifecycle_raw),
                *_all_stabil_refs(stabil_raw),
                *_all_participation_refs(participation_raw),
                *_all_participation_behavior_refs(participation_behavior_raw),
                *_all_pattern_refs(pattern_raw),
                *_all_pattern_behavior_refs(pattern_behavior_raw),
                *_all_volatility_refs(volatility_raw),
                *_all_volatility_environment_refs(volatility_environment_raw),
                *_all_ham_refs(ham_raw),
            )
        )

        structural = _filter_structural(structural_raw, inputs.as_of)
        support_resistance = support_resistance_raw.available_at(inputs.as_of)
        liquidity = _filter_liquidity(liquidity_raw, inputs.as_of)
        liquidity_landscape = (
            None
            if liquidity_landscape_raw is None
            else liquidity_landscape_raw.available_at(inputs.as_of)
        )
        reaction = _filter_reaction(reaction_raw, inputs.as_of)
        order_block_behavior = (
            None
            if order_block_behavior_raw is None
            else order_block_behavior_raw.available_at(inputs.as_of)
        )
        fvg_engulfing_lifecycle = (
            None
            if fvg_engulfing_lifecycle_raw is None
            else fvg_engulfing_lifecycle_raw.available_at(inputs.as_of)
        )
        stabil = _filter_stabil(stabil_raw, inputs.as_of)
        participation = _filter_participation(participation_raw, inputs.as_of)
        participation_behavior = (
            None
            if participation_behavior_raw is None
            else participation_behavior_raw.available_at(inputs.as_of)
        )
        pattern = _filter_pattern(pattern_raw, inputs.as_of)
        pattern_behavior = (
            None
            if pattern_behavior_raw is None
            else pattern_behavior_raw.available_at(inputs.as_of)
        )
        volatility = _filter_volatility(volatility_raw, inputs.as_of)
        volatility_environment = (
            None
            if volatility_environment_raw is None
            else volatility_environment_raw.available_at(inputs.as_of)
        )
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
            unsupported_contexts=_unsupported_tokens(
                reaction_raw,
                inputs.unsupported_contexts,
            ),
        )
        return CrossDomainBuildResult(
            context=context,
            permission=resolve_permission(context),
            structural=structural,
            support_resistance=support_resistance,
            liquidity=liquidity,
            liquidity_landscape=liquidity_landscape,
            reaction=reaction,
            stabil_support=stabil,
            participation=participation,
            pattern=pattern,
            volatility=volatility,
            ham=ham,
            zones=zones,
            order_block_behavior=order_block_behavior,
            fvg_engulfing_lifecycle=fvg_engulfing_lifecycle,
            participation_behavior=participation_behavior,
            volatility_environment=volatility_environment,
            pattern_behavior=pattern_behavior,
        )


__all__ = ["IncrementalCrossDomainProjector"]
