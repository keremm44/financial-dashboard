from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from financial_dashboard.targeting.models import TargetEvidenceType

from .envelope import (
    ContextDataQuality,
    ContextDomain,
    FactRef,
    normalize_context_data_quality,
)
from .lineage import families_for, lineage_id_from_origin_event


AvailabilityResolver = Callable[[Any, str], Any]


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _quality(value: Any) -> ContextDataQuality:
    return normalize_context_data_quality(value)


def _fact_ref(
    *,
    domain: ContextDomain,
    fact_type: str,
    symbol: str,
    timeframe: str,
    native_id: str,
    native_state: str,
    origin_time: Any,
    confirmed_at: Any | None,
    available_at: Any,
    data_quality: ContextDataQuality,
    lineage_id: str | None = None,
) -> FactRef:
    causal_family, source_family = families_for(domain, fact_type=fact_type)
    return FactRef(
        domain=domain,
        fact_type=fact_type,
        symbol=symbol,
        timeframe=timeframe,
        native_id=native_id,
        native_state=native_state,
        origin_time=origin_time,
        confirmed_at=confirmed_at,
        available_at=available_at,
        lineage_id=lineage_id,
        causal_family=causal_family,
        source_family=source_family,
        data_quality=data_quality,
    )


@dataclass(frozen=True, slots=True)
class StructuralEventProjection:
    ref: FactRef
    scope: str
    event_type: str
    direction: int
    broken_level: float | None
    origin_price: float | None
    confirmation_status: str
    validity: str
    relevance: str
    outcome: str
    bos_maturity: str


@dataclass(frozen=True, slots=True)
class StructuralScopeProjection:
    scope: str
    state: str
    direction: int
    protected_high: float | None
    protected_low: float | None
    weak_high: float | None
    weak_low: float | None
    strong_high_identity: int
    strong_low_identity: int
    protected_high_identity: int
    protected_low_identity: int
    weak_high_identity: int
    weak_low_identity: int


@dataclass(frozen=True, slots=True)
class StructuralTimeframeProjection:
    timeframe: str
    as_of: Any
    data_quality: ContextDataQuality
    external: StructuralScopeProjection | None
    internal: StructuralScopeProjection | None
    events: tuple[StructuralEventProjection, ...]


@dataclass(frozen=True, slots=True)
class StructuralFactsProjection:
    symbol: str
    timeframes: tuple[str, ...]
    timeframe_facts: tuple[StructuralTimeframeProjection, ...]

    def for_timeframe(self, timeframe: str) -> StructuralTimeframeProjection:
        normalized = timeframe.strip().lower()
        for item in self.timeframe_facts:
            if item.timeframe == normalized:
                return item
        raise KeyError(f"structural projection timeframe not found: {timeframe}")


def _scope_projection(scope: Any, export: Any, prefix: str) -> StructuralScopeProjection | None:
    if scope is None:
        return None
    return StructuralScopeProjection(
        scope=str(scope.scope),
        state=str(scope.state),
        direction=int(scope.direction),
        protected_high=getattr(export, f"{prefix}_protected_high", None),
        protected_low=getattr(export, f"{prefix}_protected_low", None),
        weak_high=getattr(export, f"{prefix}_weak_high", None),
        weak_low=getattr(export, f"{prefix}_weak_low", None),
        strong_high_identity=int(scope.strong_high_identity),
        strong_low_identity=int(scope.strong_low_identity),
        protected_high_identity=int(scope.protected_high_identity),
        protected_low_identity=int(scope.protected_low_identity),
        weak_high_identity=int(scope.weak_high_identity),
        weak_low_identity=int(scope.weak_low_identity),
    )


def project_structural_facts(
    replay: Any,
    *,
    available_at: AvailabilityResolver,
) -> StructuralFactsProjection:
    facts: list[StructuralTimeframeProjection] = []
    for timeframe in replay.timeframes:
        snapshot = replay.structure_for(timeframe)
        replay_row = replay.replays[timeframe]
        data_quality = _quality(replay_row.input_batch.source_quality.status)
        export = snapshot.export
        events: list[StructuralEventProjection] = []
        for event in snapshot.events:
            confirmation_status = str(_enum_value(event.confirmation_status))
            event_confirmed = event.confirmed_at if confirmation_status == "CONFIRMED" else None
            origin_time = (
                event.origin_source_at
                if event.origin_source_at is not None
                else event.broken_source_at
                if event.broken_source_at is not None
                else event.candidate_at
                if event.candidate_at is not None
                else event.confirmed_at
            )
            ref = _fact_ref(
                domain=ContextDomain.MARKET_STRUCTURE,
                fact_type=str(event.event_type),
                symbol=replay.symbol,
                timeframe=timeframe,
                native_id=str(event.event_uid),
                native_state=f"{_enum_value(event.validity)}:{_enum_value(event.relevance)}",
                origin_time=origin_time,
                confirmed_at=event_confirmed,
                available_at=available_at(event.confirmed_at, timeframe),
                data_quality=data_quality,
            )
            events.append(
                StructuralEventProjection(
                    ref=ref,
                    scope=str(event.scope),
                    event_type=str(event.event_type),
                    direction=int(event.direction),
                    broken_level=event.broken_level,
                    origin_price=event.origin_price,
                    confirmation_status=confirmation_status,
                    validity=str(_enum_value(event.validity)),
                    relevance=str(_enum_value(event.relevance)),
                    outcome=str(_enum_value(event.outcome)),
                    bos_maturity=str(_enum_value(event.bos_maturity)),
                )
            )
        facts.append(
            StructuralTimeframeProjection(
                timeframe=timeframe,
                as_of=snapshot.as_of,
                data_quality=data_quality,
                external=_scope_projection(snapshot.external_scope, export, "external") if export is not None else None,
                internal=_scope_projection(snapshot.internal_scope, export, "internal") if export is not None else None,
                events=tuple(sorted(events, key=lambda item: item.ref.deterministic_key)),
            )
        )
    return StructuralFactsProjection(
        symbol=replay.symbol,
        timeframes=tuple(replay.timeframes),
        timeframe_facts=tuple(facts),
    )


@dataclass(frozen=True, slots=True)
class LiquidityObservation:
    ref: FactRef
    low: float
    high: float
    anchor_price: float | None
    liquidity_scope: str | None
    roles: tuple[str, ...]
    target_eligible: bool


@dataclass(frozen=True, slots=True)
class LiquidityBehaviorObservation:
    ref: FactRef
    pool_identity: str
    side: str
    level: float
    maturity: str
    relation: str
    removal: str
    age_bars: int
    bars_since_touch: int
    touch_count: int
    distance_atr: float | None
    distance_delta_atr: float | None


@dataclass(frozen=True, slots=True)
class LiquidityProjection:
    symbol: str
    timeframes: tuple[str, ...]
    observations: tuple[LiquidityObservation, ...]
    behavior_observations: tuple[LiquidityBehaviorObservation, ...] = ()

    def for_timeframe(self, timeframe: str) -> tuple[LiquidityObservation, ...]:
        normalized = timeframe.strip().lower()
        return tuple(item for item in self.observations if item.ref.timeframe == normalized)

    def behavior_for_timeframe(self, timeframe: str) -> tuple[LiquidityBehaviorObservation, ...]:
        normalized = timeframe.strip().lower()
        return tuple(item for item in self.behavior_observations if item.ref.timeframe == normalized)


def project_liquidity(
    replay: Any,
    *,
    data_quality_by_timeframe: Mapping[str, Any],
) -> LiquidityProjection:
    observations: list[LiquidityObservation] = []
    for evidence in replay.evidence:
        if evidence.evidence_type is not TargetEvidenceType.LIQUIDITY:
            continue
        quality = _quality(data_quality_by_timeframe[evidence.timeframe])
        ref = _fact_ref(
            domain=ContextDomain.LIQUIDITY,
            fact_type="POOL",
            symbol=evidence.symbol,
            timeframe=evidence.timeframe,
            native_id=str(evidence.native_origin_id),
            native_state=str(evidence.source_state),
            origin_time=evidence.origin_time,
            confirmed_at=evidence.confirmed_at,
            available_at=evidence.available_at,
            data_quality=quality,
            lineage_id=lineage_id_from_origin_event(evidence),
        )
        observations.append(
            LiquidityObservation(
                ref=ref,
                low=float(evidence.low),
                high=float(evidence.high),
                anchor_price=None if evidence.anchor_price is None else float(evidence.anchor_price),
                liquidity_scope=(
                    None if evidence.liquidity_scope is None else str(_enum_value(evidence.liquidity_scope))
                ),
                roles=tuple(sorted(str(_enum_value(role)) for role in evidence.roles)),
                target_eligible=bool(evidence.target_eligible),
            )
        )

    behavior_rows: list[LiquidityBehaviorObservation] = []
    behavior_by_timeframe = getattr(replay, "liquidity_behavior", None) or {}
    for timeframe, behavior in behavior_by_timeframe.items():
        snapshot = replay.snapshots.get(timeframe)
        if snapshot is None or behavior.as_of is None:
            continue
        quality = _quality(data_quality_by_timeframe[timeframe])
        for pool in behavior.pools:
            native_state = (
                f"{_enum_value(pool.maturity)}:{_enum_value(pool.relation)}:"
                f"{_enum_value(pool.removal)}"
            )
            ref = _fact_ref(
                domain=ContextDomain.LIQUIDITY,
                fact_type="POOL_BEHAVIOR",
                symbol=replay.symbol,
                timeframe=timeframe,
                native_id=f"LIQ_BEHAVIOR:{timeframe}:{pool.identity}:{_token(behavior.as_of)}",
                native_state=native_state,
                origin_time=behavior.as_of,
                confirmed_at=behavior.as_of,
                available_at=snapshot.available_at,
                data_quality=quality,
            )
            behavior_rows.append(
                LiquidityBehaviorObservation(
                    ref=ref,
                    pool_identity=str(pool.identity),
                    side=str(_enum_value(pool.side)),
                    level=float(pool.level),
                    maturity=str(_enum_value(pool.maturity)),
                    relation=str(_enum_value(pool.relation)),
                    removal=str(_enum_value(pool.removal)),
                    age_bars=int(pool.age_bars),
                    bars_since_touch=int(pool.bars_since_touch),
                    touch_count=int(pool.touch_count),
                    distance_atr=pool.distance_atr,
                    distance_delta_atr=pool.distance_delta_atr,
                )
            )

    return LiquidityProjection(
        symbol=replay.symbol,
        timeframes=tuple(replay.timeframes),
        observations=tuple(sorted(observations, key=lambda item: item.ref.deterministic_key)),
        behavior_observations=tuple(
            sorted(behavior_rows, key=lambda item: item.ref.deterministic_key)
        ),
    )


@dataclass(frozen=True, slots=True)
class ReactionObservation:
    ref: FactRef
    evidence_type: str
    low: float
    high: float
    anchor_price: float | None
    roles: tuple[str, ...]
    semantic_role: str


@dataclass(frozen=True, slots=True)
class ReactionEvidenceProjection:
    symbol: str
    timeframes: tuple[str, ...]
    reaction_zones: tuple[ReactionObservation, ...]
    confirmations: tuple[ReactionObservation, ...]
    unsupported_fvg_engulfing_timeframes: tuple[str, ...] = ()


def _project_reaction_item(evidence: Any, quality: ContextDataQuality) -> ReactionObservation:
    evidence_type = str(_enum_value(evidence.evidence_type))
    domain = {
        TargetEvidenceType.ORDER_BLOCK: ContextDomain.ORDER_BLOCK,
        TargetEvidenceType.FVG: ContextDomain.FVG,
        TargetEvidenceType.ENGULFING: ContextDomain.ENGULFING,
    }[evidence.evidence_type]
    semantic_role = "CONFIRMATION" if evidence.evidence_type is TargetEvidenceType.ENGULFING else "REACTION_ZONE"
    ref = _fact_ref(
        domain=domain,
        fact_type=evidence_type,
        symbol=evidence.symbol,
        timeframe=evidence.timeframe,
        native_id=str(evidence.native_origin_id),
        native_state=str(evidence.source_state),
        origin_time=evidence.origin_time,
        confirmed_at=evidence.confirmed_at,
        available_at=evidence.available_at,
        data_quality=quality,
        lineage_id=lineage_id_from_origin_event(evidence),
    )
    return ReactionObservation(
        ref=ref,
        evidence_type=evidence_type,
        low=float(evidence.low),
        high=float(evidence.high),
        anchor_price=None if evidence.anchor_price is None else float(evidence.anchor_price),
        roles=tuple(sorted(str(_enum_value(role)) for role in evidence.roles)),
        semantic_role=semantic_role,
    )


def project_reaction_evidence(
    *,
    symbol: str,
    order_block_replay: Any | None,
    fvg_engulfing_replay: Any | None,
    data_quality_by_timeframe: Mapping[str, Any],
    requested_timeframes: Iterable[str] = (),
) -> ReactionEvidenceProjection:
    zones: list[ReactionObservation] = []
    confirmations: list[ReactionObservation] = []
    timeframes: set[str] = set()
    for replay in (order_block_replay, fvg_engulfing_replay):
        if replay is None:
            continue
        timeframes.update(replay.timeframes)
        for evidence in replay.evidence:
            if evidence.evidence_type not in {
                TargetEvidenceType.ORDER_BLOCK,
                TargetEvidenceType.FVG,
                TargetEvidenceType.ENGULFING,
            }:
                continue
            item = _project_reaction_item(
                evidence,
                _quality(data_quality_by_timeframe[evidence.timeframe]),
            )
            if item.semantic_role == "CONFIRMATION":
                confirmations.append(item)
            else:
                zones.append(item)
    requested = tuple(sorted({str(tf).strip().lower() for tf in requested_timeframes if str(tf).strip()}))
    fvg_supported = set() if fvg_engulfing_replay is None else set(fvg_engulfing_replay.timeframes)
    unsupported = tuple(tf for tf in requested if tf not in fvg_supported) if requested else ()
    return ReactionEvidenceProjection(
        symbol=symbol,
        timeframes=tuple(sorted(timeframes)),
        reaction_zones=tuple(sorted(zones, key=lambda item: item.ref.deterministic_key)),
        confirmations=tuple(sorted(confirmations, key=lambda item: item.ref.deterministic_key)),
        unsupported_fvg_engulfing_timeframes=unsupported,
    )


@dataclass(frozen=True, slots=True)
class StabilSupportEventProjection:
    ref: FactRef
    event_type: str
    support_level: float | None
    support_floor: float | None
    price: float
    bars_above_support: int
    bars_below_support: int
    reclaim_count: int


@dataclass(frozen=True, slots=True)
class StabilSupportBehaviorProjection:
    motion: str
    relation: str
    interaction: str
    bars_since_rebase: int | None
    cross_count: int
    last_rebase_step_atr: float | None
    reclaim_active: bool


@dataclass(frozen=True, slots=True)
class StabilSupportProjection:
    symbol: str
    timeframe: str
    as_of: Any
    data_quality: ContextDataQuality
    support_ref: FactRef | None
    support_level: float | None
    support_floor: float | None
    validity: str
    dynamics: str
    progression: str
    distance_pct: float | None
    distance_atr: float | None
    bars_above_support: int
    bars_below_support: int
    reclaim_count: int
    events: tuple[StabilSupportEventProjection, ...]
    behavior: StabilSupportBehaviorProjection | None = None


def _token(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _project_stabil_behavior(behavior: Any | None) -> StabilSupportBehaviorProjection | None:
    if behavior is None:
        return None
    return StabilSupportBehaviorProjection(
        motion=str(_enum_value(behavior.motion)),
        relation=str(_enum_value(behavior.relation)),
        interaction=str(_enum_value(behavior.interaction)),
        bars_since_rebase=behavior.bars_since_rebase,
        cross_count=int(behavior.cross_count),
        last_rebase_step_atr=behavior.last_rebase_step_atr,
        reclaim_active=bool(behavior.reclaim_active),
    )


def project_stabil_support(replay: Any) -> StabilSupportProjection:
    snapshot = replay.snapshot
    quality = _quality(replay.input_batch.source_quality.status)
    support_ref: FactRef | None = None
    if (
        snapshot.support_level is not None
        and snapshot.support_origin_at is not None
        and snapshot.support_available_at is not None
    ):
        native_id = (
            f"STABIL_SUPPORT:{replay.timeframe}:{_token(snapshot.support_origin_at)}:"
            f"{_token(snapshot.support_confirmed_at)}:{float(snapshot.support_level):.10g}"
        )
        support_ref = _fact_ref(
            domain=ContextDomain.STABIL_SUPPORT,
            fact_type="DAILY_STRUCTURAL_SUPPORT",
            symbol=replay.symbol,
            timeframe=replay.timeframe,
            native_id=native_id,
            native_state=str(_enum_value(snapshot.validity)),
            origin_time=snapshot.support_origin_at,
            confirmed_at=snapshot.support_confirmed_at,
            available_at=snapshot.support_available_at,
            data_quality=quality,
        )
    events: list[StabilSupportEventProjection] = []
    for event in snapshot.events:
        event_type = str(_enum_value(event.event_type))
        event_ref = _fact_ref(
            domain=ContextDomain.STABIL_SUPPORT,
            fact_type=event_type,
            symbol=replay.symbol,
            timeframe=replay.timeframe,
            native_id=f"STABIL_EVENT:{replay.timeframe}:{event.sequence}:{event_type}",
            native_state=event_type,
            origin_time=event.origin_at if event.origin_at is not None else event.event_time,
            confirmed_at=event.event_time,
            available_at=event.available_at,
            data_quality=quality,
        )
        events.append(
            StabilSupportEventProjection(
                ref=event_ref,
                event_type=event_type,
                support_level=event.support_level,
                support_floor=event.support_floor,
                price=float(event.price),
                bars_above_support=int(event.bars_above_support),
                bars_below_support=int(event.bars_below_support),
                reclaim_count=int(event.reclaim_count),
            )
        )
    return StabilSupportProjection(
        symbol=replay.symbol,
        timeframe=replay.timeframe,
        as_of=snapshot.as_of,
        data_quality=quality,
        support_ref=support_ref,
        support_level=snapshot.support_level,
        support_floor=snapshot.support_floor,
        validity=str(_enum_value(snapshot.validity)),
        dynamics=str(_enum_value(snapshot.dynamics)),
        progression=str(_enum_value(snapshot.progression)),
        distance_pct=snapshot.distance_pct,
        distance_atr=snapshot.distance_atr,
        bars_above_support=int(snapshot.bars_above_support),
        bars_below_support=int(snapshot.bars_below_support),
        reclaim_count=int(snapshot.reclaim_count),
        events=tuple(events),
        behavior=_project_stabil_behavior(getattr(replay, "behavior", None)),
    )


@dataclass(frozen=True, slots=True)
class ParticipationLinkProjection:
    structure_event_uid: str
    relation: str
    assessed_at: Any
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParticipationTimeframeProjection:
    timeframe: str
    data_quality: ContextDataQuality
    ref: FactRef
    status: str
    state: str
    evidence_direction: int
    event_links: tuple[ParticipationLinkProjection, ...]


@dataclass(frozen=True, slots=True)
class ParticipationProjection:
    symbol: str
    timeframes: tuple[str, ...]
    timeframe_facts: tuple[ParticipationTimeframeProjection, ...]


def project_participation(
    replay: Any,
    *,
    available_at: AvailabilityResolver,
) -> ParticipationProjection:
    rows: list[ParticipationTimeframeProjection] = []
    for timeframe_replay in replay.timeframe_replays:
        latest = timeframe_replay.latest
        quality = _quality(latest.data_quality)
        ref = _fact_ref(
            domain=ContextDomain.VOLUME,
            fact_type="PARTICIPATION_OBSERVATION",
            symbol=replay.symbol,
            timeframe=timeframe_replay.timeframe,
            native_id=(
                f"VOL:{timeframe_replay.timeframe}:{latest.bar_index}:{latest.segment_id}"
            ),
            native_state=str(latest.state),
            origin_time=latest.timestamp,
            confirmed_at=latest.timestamp,
            available_at=available_at(latest.timestamp, timeframe_replay.timeframe),
            data_quality=quality,
        )
        links = tuple(
            ParticipationLinkProjection(
                structure_event_uid=str(link.event_uid),
                relation=str(_enum_value(link.relation)),
                assessed_at=link.assessed_at,
                reasons=tuple(str(reason) for reason in link.reasons),
            )
            for link in timeframe_replay.event_links
        )
        rows.append(
            ParticipationTimeframeProjection(
                timeframe=timeframe_replay.timeframe,
                data_quality=quality,
                ref=ref,
                status=str(_enum_value(latest.status)),
                state=str(latest.state),
                evidence_direction=int(latest.evidence_direction),
                event_links=links,
            )
        )
    return ParticipationProjection(
        symbol=replay.symbol,
        timeframes=tuple(replay.timeframes),
        timeframe_facts=tuple(rows),
    )


@dataclass(frozen=True, slots=True)
class PatternTimeframeProjection:
    timeframe: str
    data_quality: ContextDataQuality
    ref: FactRef | None
    pattern_state_code: int | None
    pattern_type_code: int | None
    classic_direction: int | None
    break_state_code: int | None
    break_level: float | None
    retest_state_code: int | None
    identity: float | None


@dataclass(frozen=True, slots=True)
class PatternProjection:
    symbol: str
    timeframes: tuple[str, ...]
    timeframe_facts: tuple[PatternTimeframeProjection, ...]


def project_pattern(
    replay: Any,
    *,
    available_at: AvailabilityResolver,
) -> PatternProjection:
    rows: list[PatternTimeframeProjection] = []
    for snapshot in replay.pattern_snapshots:
        source_quality = replay.structure_location.replay_for(snapshot.timeframe).input_batch.source_quality.status
        quality = _quality(source_quality)
        export = snapshot.export
        ref: FactRef | None = None
        if export is not None and snapshot.as_of is not None:
            identity = "NONE" if export.identity is None else f"{float(export.identity):.10g}"
            ref = _fact_ref(
                domain=ContextDomain.PATTERN,
                fact_type="PATTERN_SNAPSHOT",
                symbol=replay.symbol,
                timeframe=snapshot.timeframe,
                native_id=f"PATTERN:{snapshot.timeframe}:{identity}",
                native_state="NONE" if export.state is None else str(int(export.state)),
                origin_time=snapshot.as_of,
                confirmed_at=snapshot.as_of,
                available_at=available_at(snapshot.as_of, snapshot.timeframe),
                data_quality=quality,
            )
        rows.append(
            PatternTimeframeProjection(
                timeframe=snapshot.timeframe,
                data_quality=quality,
                ref=ref,
                pattern_state_code=None if export is None else export.state,
                pattern_type_code=None if export is None else export.pattern_type,
                classic_direction=None if export is None else export.classic_direction,
                break_state_code=None if export is None else export.break_state,
                break_level=None if export is None else export.break_level,
                retest_state_code=None if export is None else export.retest_state,
                identity=None if export is None else export.identity,
            )
        )
    return PatternProjection(
        symbol=replay.symbol,
        timeframes=tuple(replay.timeframes),
        timeframe_facts=tuple(rows),
    )


@dataclass(frozen=True, slots=True)
class VolatilityTimeframeProjection:
    timeframe: str
    data_quality: ContextDataQuality
    ref: FactRef | None
    regime_code: int | None
    band_state_code: int | None
    fib_state_code: int | None
    active_swing_direction: int
    fib_retracement_ratio: float | None
    early_state: str
    early_episode_id: int
    early_episode_started: bool


@dataclass(frozen=True, slots=True)
class VolatilityProjection:
    symbol: str
    timeframes: tuple[str, ...]
    timeframe_facts: tuple[VolatilityTimeframeProjection, ...]


def project_volatility(
    replay: Any,
    *,
    available_at: AvailabilityResolver,
) -> VolatilityProjection:
    rows: list[VolatilityTimeframeProjection] = []
    for timeframe in replay.timeframes:
        latest = replay.for_timeframe(timeframe).latest
        if latest is None:
            rows.append(
                VolatilityTimeframeProjection(
                    timeframe=timeframe,
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
            continue
        export = latest.confirmed_export
        quality = _quality(export.data_quality)
        ref = None
        if latest.timestamp is not None:
            ref = _fact_ref(
                domain=ContextDomain.VOLATILITY,
                fact_type="VOLATILITY_CONTEXT_OBSERVATION",
                symbol=replay.symbol,
                timeframe=timeframe,
                native_id=f"VOLATILITY:{timeframe}:{_token(latest.timestamp)}",
                native_state="PENDING" if export.regime is None else str(int(export.regime)),
                origin_time=latest.timestamp,
                confirmed_at=latest.timestamp,
                available_at=available_at(latest.timestamp, timeframe),
                data_quality=quality,
            )
        rows.append(
            VolatilityTimeframeProjection(
                timeframe=timeframe,
                data_quality=quality,
                ref=ref,
                regime_code=export.regime,
                band_state_code=export.band_state,
                fib_state_code=export.fib_state,
                active_swing_direction=int(export.active_swing_direction),
                fib_retracement_ratio=export.fib_retracement_ratio,
                early_state=str(_enum_value(latest.early.state)),
                early_episode_id=int(latest.early.episode_id),
                early_episode_started=bool(latest.early.episode_started),
            )
        )
    return VolatilityProjection(
        symbol=replay.symbol,
        timeframes=tuple(replay.timeframes),
        timeframe_facts=tuple(rows),
    )


@dataclass(frozen=True, slots=True)
class HamFamilyProjection:
    family: str
    ref: FactRef
    balance: float | None
    activity: float | None
    coverage: float
    ready: bool


@dataclass(frozen=True, slots=True)
class HamTimeframeProjection:
    timeframe: str
    data_quality: ContextDataQuality
    families: tuple[HamFamilyProjection, ...]


@dataclass(frozen=True, slots=True)
class HamProjection:
    symbol: str
    timeframes: tuple[str, ...]
    timeframe_facts: tuple[HamTimeframeProjection, ...]


def project_ham(
    replay: Any,
    *,
    available_at: AvailabilityResolver,
) -> HamProjection:
    rows: list[HamTimeframeProjection] = []
    family_names = ("PRICE", "MOMENTUM", "TIMING", "FLOW")
    for timeframe_replay in replay.timeframe_replays:
        latest = timeframe_replay.latest
        quality = _quality(latest.data_quality)
        family_values = latest.families.as_tuple()
        family_rows: list[HamFamilyProjection] = []
        for family_name, family in zip(family_names, family_values, strict=True):
            causal_family, source_family = families_for(
                ContextDomain.HAM,
                fact_type=f"{family_name}_BALANCE",
            )
            ref = FactRef(
                domain=ContextDomain.HAM,
                fact_type=f"{family_name}_BALANCE",
                symbol=replay.symbol,
                timeframe=timeframe_replay.timeframe,
                native_id=(
                    f"HAM:{timeframe_replay.timeframe}:{family_name}:{_token(latest.timestamp)}"
                ),
                native_state="READY" if family.ready else "NOT_READY",
                origin_time=latest.timestamp,
                confirmed_at=latest.timestamp,
                available_at=available_at(latest.timestamp, timeframe_replay.timeframe),
                lineage_id=None,
                causal_family=causal_family,
                source_family=source_family,
                data_quality=quality,
            )
            family_rows.append(
                HamFamilyProjection(
                    family=family_name,
                    ref=ref,
                    balance=family.balance,
                    activity=family.activity,
                    coverage=float(family.coverage),
                    ready=bool(family.ready),
                )
            )
        rows.append(
            HamTimeframeProjection(
                timeframe=timeframe_replay.timeframe,
                data_quality=quality,
                families=tuple(family_rows),
            )
        )
    return HamProjection(
        symbol=replay.symbol,
        timeframes=tuple(replay.timeframes),
        timeframe_facts=tuple(rows),
    )


__all__ = [
    "AvailabilityResolver",
    "HamFamilyProjection",
    "HamProjection",
    "HamTimeframeProjection",
    "LiquidityBehaviorObservation",
    "LiquidityObservation",
    "LiquidityProjection",
    "ParticipationLinkProjection",
    "ParticipationProjection",
    "ParticipationTimeframeProjection",
    "PatternProjection",
    "PatternTimeframeProjection",
    "ReactionEvidenceProjection",
    "ReactionObservation",
    "StabilSupportBehaviorProjection",
    "StabilSupportEventProjection",
    "StabilSupportProjection",
    "StructuralEventProjection",
    "StructuralFactsProjection",
    "StructuralScopeProjection",
    "StructuralTimeframeProjection",
    "VolatilityProjection",
    "VolatilityTimeframeProjection",
    "project_ham",
    "project_liquidity",
    "project_participation",
    "project_pattern",
    "project_reaction_evidence",
    "project_stabil_support",
    "project_structural_facts",
    "project_volatility",
]
