from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from financial_dashboard.context.envelope import ContextDataQuality, FactRef
from financial_dashboard.context.participation_behavior_projection import ParticipationBehaviorProjection
from financial_dashboard.context.projections import (
    StabilSupportEventProjection,
    StabilSupportProjection,
    StructuralFactsProjection,
    StructuralTimeframeProjection,
)
from financial_dashboard.context.volatility_environment_projection import VolatilityEnvironmentProjection

from .environment import EnvironmentAssessment, EnvironmentRisk, assess_environment
from .participation import ParticipationAssessment, ParticipationState, assess_participation
from .structural import (
    DecisionHorizon,
    HorizonRelation,
    StructuralAssessment,
    StructuralDirection,
    ThesisState,
    build_horizon_structural_snapshot,
)


class TimeframeAuthorityRole(StrEnum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    BRIDGE = "BRIDGE"
    TIMING = "TIMING"
    EXECUTION = "EXECUTION"
    BACKGROUND = "BACKGROUND"
    RISK_CONTEXT = "RISK_CONTEXT"


class BridgeState(StrEnum):
    ALIGNED = "ALIGNED"
    COUNTER_REACTION = "COUNTER_REACTION"
    TRANSITION_WARNING = "TRANSITION_WARNING"
    NEUTRAL = "NEUTRAL"
    UNRESOLVED = "UNRESOLVED"
    UNAVAILABLE = "UNAVAILABLE"


class StructuralRegime(StrEnum):
    DIRECTIONAL = "DIRECTIONAL"
    TRANSITION = "TRANSITION"
    INVALIDATED = "INVALIDATED"
    UNRESOLVED = "UNRESOLVED"


class MarketRiskRegime(StrEnum):
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    HARD_BLOCK = "HARD_BLOCK"
    UNKNOWN = "UNKNOWN"


class ParticipationPropagationState(StrEnum):
    HTF_CONFIRMED = "HTF_CONFIRMED"
    BRIDGE_CONFIRMED = "BRIDGE_CONFIRMED"
    LOCAL_ONLY = "LOCAL_ONLY"
    WEAKENING = "WEAKENING"
    OPPOSING = "OPPOSING"
    MIXED = "MIXED"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class TimeframeStructuralNode:
    horizon: DecisionHorizon
    timeframe: str
    role: TimeframeAuthorityRole
    data_quality: ContextDataQuality
    external_state: str | None
    external_direction: int
    internal_state: str | None
    internal_direction: int
    protected_high: float | None
    protected_low: float | None
    weak_high: float | None
    weak_low: float | None
    current_external_refs: tuple[FactRef, ...]

    @property
    def is_direction_authority(self) -> bool:
        return self.role is TimeframeAuthorityRole.PRIMARY


@dataclass(frozen=True, slots=True)
class HorizonStructuralMap:
    horizon: DecisionHorizon
    primary_timeframe: str
    nodes: tuple[TimeframeStructuralNode, ...]
    bridge_state: BridgeState
    structural_regime: StructuralRegime
    reasons: tuple[str, ...]

    def for_timeframe(self, timeframe: str) -> TimeframeStructuralNode:
        normalized = timeframe.strip().lower()
        for node in self.nodes:
            if node.timeframe == normalized:
                return node
        raise KeyError(f"structural map timeframe not found: {timeframe}")


@dataclass(frozen=True, slots=True)
class HorizonMarketState:
    horizon: DecisionHorizon
    structural: StructuralAssessment
    structural_map: HorizonStructuralMap
    environment_risk: MarketRiskRegime
    environment: tuple[tuple[str, EnvironmentAssessment], ...]
    participation_propagation: ParticipationPropagationState
    participation: tuple[tuple[str, ParticipationAssessment], ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StabilMarketState:
    """Read-only native Stabil facts; never BUY/SELL or horizon policy."""

    data_quality: ContextDataQuality
    timeframe: str | None
    as_of: object | None
    support_ref: FactRef | None
    support_level: float | None
    support_floor: float | None
    validity: str | None
    dynamics: str | None
    progression: str | None
    distance_pct: float | None
    distance_atr: float | None
    bars_above_support: int | None
    bars_below_support: int | None
    reclaim_count: int | None
    events: tuple[StabilSupportEventProjection, ...]
    motion: str | None
    relation: str | None
    interaction: str | None
    approach_origin: str | None
    bars_since_rebase: int | None
    cross_count: int | None
    last_rebase_step_atr: float | None
    distance_delta_atr: float | None
    reclaim_active: bool | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MarketStateSnapshot:
    """Causal factual MTF state with traceable evidence only.

    ``source_refs`` contains only real FactRef objects already carried by the factual
    projections used here. Unknown lineage remains unknown; ``source_lineage`` never
    fabricates replacement identities for refs whose lineage_id is absent.
    """

    as_of: object
    long_term: HorizonMarketState
    short_term: HorizonMarketState
    horizon_relation: HorizonRelation
    stabil: StabilMarketState
    source_refs: tuple[FactRef, ...]
    source_lineage: tuple[str, ...]
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.as_of is None:
            raise ValueError("market state as_of must be known")

        keys = tuple(ref.deterministic_key for ref in self.source_refs)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("market state source refs must be unique and deterministic")
        if any(not ref.is_available_at(self.as_of) for ref in self.source_refs):
            raise ValueError("market state cannot contain future-unavailable refs")

        expected_lineage = tuple(
            sorted(
                {
                    ref.lineage_id
                    for ref in self.source_refs
                    if ref.lineage_id is not None
                }
            )
        )
        if self.source_lineage != expected_lineage:
            raise ValueError("market state source lineage must match known source refs")

        for horizon_state in (self.long_term, self.short_term):
            authority_as_of = horizon_state.structural.authority_as_of
            if authority_as_of is None:
                continue
            try:
                is_future = authority_as_of > self.as_of
            except TypeError as exc:
                raise TypeError("market state structural as_of must be comparable") from exc
            if is_future:
                raise ValueError("market state cannot contain future structural authority")

        if self.stabil.as_of is not None:
            try:
                stabil_is_future = self.stabil.as_of > self.as_of
            except TypeError as exc:
                raise TypeError("market state Stabil as_of must be comparable") from exc
            if stabil_is_future:
                raise ValueError("market state cannot contain future Stabil state")


_LT_ROLES: tuple[tuple[str, TimeframeAuthorityRole], ...] = (
    ("1d", TimeframeAuthorityRole.PRIMARY),
    ("4h", TimeframeAuthorityRole.SECONDARY),
    ("2h", TimeframeAuthorityRole.BRIDGE),
    ("1h", TimeframeAuthorityRole.TIMING),
    ("30m", TimeframeAuthorityRole.EXECUTION),
)
_ST_ROLES: tuple[tuple[str, TimeframeAuthorityRole], ...] = (
    ("1d", TimeframeAuthorityRole.BACKGROUND),
    ("4h", TimeframeAuthorityRole.RISK_CONTEXT),
    ("2h", TimeframeAuthorityRole.BRIDGE),
    ("1h", TimeframeAuthorityRole.PRIMARY),
    ("30m", TimeframeAuthorityRole.EXECUTION),
)

_BULLISH_STATES = frozenset({"BULLISH", "STATE_BULLISH"})
_BEARISH_STATES = frozenset({"BEARISH", "STATE_BEARISH"})
_TRANSITION_UP_STATES = frozenset({"TRANSITION_UP", "STATE_TRANSITION_UP"})
_TRANSITION_DOWN_STATES = frozenset({"TRANSITION_DOWN", "STATE_TRANSITION_DOWN"})


def _row(structure: StructuralFactsProjection, timeframe: str) -> StructuralTimeframeProjection | None:
    try:
        return structure.for_timeframe(timeframe)
    except KeyError:
        return None


def _scope_state(scope: object | None) -> str | None:
    if scope is None:
        return None
    token = str(getattr(scope, "state", "") or "").strip().upper()
    return token or None


def _current_external_refs(row: StructuralTimeframeProjection | None) -> tuple[FactRef, ...]:
    if row is None:
        return ()
    refs = (
        event.ref
        for event in row.events
        if event.scope.strip().upper() == "EXTERNAL"
        and event.confirmation_status.strip().upper() == "CONFIRMED"
        and event.validity.strip().upper() == "VALID"
        and event.relevance.strip().upper() == "CURRENT"
        and event.ref.data_quality is ContextDataQuality.VALID
    )
    return tuple(sorted(refs, key=lambda ref: ref.deterministic_key))


def _node(
    structure: StructuralFactsProjection,
    *,
    horizon: DecisionHorizon,
    timeframe: str,
    role: TimeframeAuthorityRole,
) -> TimeframeStructuralNode:
    row = _row(structure, timeframe)
    if row is None:
        return TimeframeStructuralNode(
            horizon=horizon,
            timeframe=timeframe,
            role=role,
            data_quality=ContextDataQuality.UNAVAILABLE,
            external_state=None,
            external_direction=0,
            internal_state=None,
            internal_direction=0,
            protected_high=None,
            protected_low=None,
            weak_high=None,
            weak_low=None,
            current_external_refs=(),
        )
    external = row.external
    internal = row.internal
    return TimeframeStructuralNode(
        horizon=horizon,
        timeframe=timeframe,
        role=role,
        data_quality=row.data_quality,
        external_state=_scope_state(external),
        external_direction=0 if external is None else int(external.direction),
        internal_state=_scope_state(internal),
        internal_direction=0 if internal is None else int(internal.direction),
        protected_high=None if external is None else external.protected_high,
        protected_low=None if external is None else external.protected_low,
        weak_high=None if external is None else external.weak_high,
        weak_low=None if external is None else external.weak_low,
        current_external_refs=_current_external_refs(row),
    )


def _direction_value(direction: StructuralDirection) -> int:
    if direction is StructuralDirection.LONG:
        return 1
    if direction is StructuralDirection.SHORT:
        return -1
    return 0


def _transition_target(state: str | None) -> int:
    if state in _TRANSITION_UP_STATES:
        return 1
    if state in _TRANSITION_DOWN_STATES:
        return -1
    return 0


def _bridge_state(
    assessment: StructuralAssessment,
    bridge: TimeframeStructuralNode,
) -> BridgeState:
    if bridge.data_quality is not ContextDataQuality.VALID or bridge.external_state is None:
        return BridgeState.UNAVAILABLE
    primary_direction = _direction_value(assessment.direction)
    if primary_direction == 0:
        return BridgeState.UNRESOLVED

    target = _transition_target(bridge.external_state)
    if target != 0:
        if target == primary_direction:
            return BridgeState.ALIGNED
        return BridgeState.TRANSITION_WARNING

    if bridge.external_direction == primary_direction:
        return BridgeState.ALIGNED
    if bridge.external_direction == -primary_direction:
        return BridgeState.COUNTER_REACTION
    if bridge.external_direction == 0:
        return BridgeState.NEUTRAL
    return BridgeState.UNRESOLVED


def _structural_regime(assessment: StructuralAssessment) -> StructuralRegime:
    if assessment.thesis_state is ThesisState.INTACT and assessment.direction is not StructuralDirection.UNRESOLVED:
        return StructuralRegime.DIRECTIONAL
    if assessment.thesis_state is ThesisState.TRANSITIONING:
        return StructuralRegime.TRANSITION
    if assessment.thesis_state is ThesisState.INVALIDATED:
        return StructuralRegime.INVALIDATED
    return StructuralRegime.UNRESOLVED


def build_horizon_structural_map(
    structure: StructuralFactsProjection,
    assessment: StructuralAssessment,
) -> HorizonStructuralMap:
    roles = _LT_ROLES if assessment.horizon is DecisionHorizon.LONG_TERM else _ST_ROLES
    nodes = tuple(
        _node(
            structure,
            horizon=assessment.horizon,
            timeframe=timeframe,
            role=role,
        )
        for timeframe, role in roles
    )
    bridge = next(node for node in nodes if node.role is TimeframeAuthorityRole.BRIDGE)
    bridge_state = _bridge_state(assessment, bridge)
    regime = _structural_regime(assessment)
    reasons = (
        f"PRIMARY_AUTHORITY:{assessment.authority_timeframe}",
        f"STRUCTURAL_REGIME:{regime.value}",
        f"2h:BRIDGE:{bridge_state.value}",
    )
    return HorizonStructuralMap(
        horizon=assessment.horizon,
        primary_timeframe=assessment.authority_timeframe,
        nodes=nodes,
        bridge_state=bridge_state,
        structural_regime=regime,
        reasons=reasons,
    )


def _environment_timeframes(horizon: DecisionHorizon) -> tuple[str, ...]:
    if horizon is DecisionHorizon.LONG_TERM:
        return ("1d", "4h", "2h")
    # Volatility is intentionally not available on 1H/30m. 4H/2H are therefore
    # environment context for ST, never structural authority.
    return ("4h", "2h")


def _environment_state(
    assessment: StructuralAssessment,
    volatility: VolatilityEnvironmentProjection | None,
) -> tuple[MarketRiskRegime, tuple[tuple[str, EnvironmentAssessment], ...], tuple[str, ...]]:
    rows = tuple(
        (timeframe, assess_environment(assessment.direction, volatility, timeframe=timeframe))
        for timeframe in _environment_timeframes(assessment.horizon)
    )
    risks = tuple(item.risk for _, item in rows)
    if EnvironmentRisk.HARD_BLOCK in risks:
        risk = MarketRiskRegime.HARD_BLOCK
    elif EnvironmentRisk.ELEVATED in risks:
        risk = MarketRiskRegime.ELEVATED
    elif any(item.data_quality is ContextDataQuality.VALID for _, item in rows):
        risk = MarketRiskRegime.NORMAL
    else:
        risk = MarketRiskRegime.UNKNOWN
    reasons = (f"ENVIRONMENT_RISK:{risk.value}",)
    return risk, rows, reasons


def _participation_state(
    assessment: StructuralAssessment,
    participation: ParticipationBehaviorProjection | None,
) -> tuple[
    ParticipationPropagationState,
    tuple[tuple[str, ParticipationAssessment], ...],
    tuple[str, ...],
]:
    # 30m is deliberately excluded: it is execution-time context and may be dormant.
    # Propagation is observed causally from local 1H -> bridge 2H -> HTF 4H.
    timeframes = ("1h", "2h", "4h")
    rows = tuple(
        (timeframe, assess_participation(assessment.direction, participation, timeframe=timeframe))
        for timeframe in timeframes
    )
    states = {timeframe: item.state for timeframe, item in rows}
    supportive = {tf for tf, state in states.items() if state is ParticipationState.SUPPORTIVE}
    opposing = {tf for tf, state in states.items() if state is ParticipationState.OPPOSING}
    weak = {tf for tf, state in states.items() if state is ParticipationState.WEAK}
    known = {
        tf
        for tf, item in rows
        if item.data_quality is ContextDataQuality.VALID and item.state is not ParticipationState.UNKNOWN
    }

    if supportive and opposing:
        state = ParticipationPropagationState.MIXED
    elif opposing:
        state = ParticipationPropagationState.OPPOSING
    elif "4h" in supportive:
        state = ParticipationPropagationState.HTF_CONFIRMED
    elif "2h" in supportive:
        state = ParticipationPropagationState.BRIDGE_CONFIRMED
    elif "1h" in supportive:
        state = ParticipationPropagationState.LOCAL_ONLY
    elif weak:
        state = ParticipationPropagationState.WEAKENING
    elif known:
        state = ParticipationPropagationState.NEUTRAL
    else:
        state = ParticipationPropagationState.UNKNOWN

    reasons = (f"PARTICIPATION_PROPAGATION:{state.value}",)
    return state, rows, reasons


def _stabil_state(stabil: StabilSupportProjection | None) -> StabilMarketState:
    if stabil is None:
        return StabilMarketState(
            data_quality=ContextDataQuality.UNAVAILABLE,
            timeframe=None,
            as_of=None,
            support_ref=None,
            support_level=None,
            support_floor=None,
            validity=None,
            dynamics=None,
            progression=None,
            distance_pct=None,
            distance_atr=None,
            bars_above_support=None,
            bars_below_support=None,
            reclaim_count=None,
            events=(),
            motion=None,
            relation=None,
            interaction=None,
            approach_origin=None,
            bars_since_rebase=None,
            cross_count=None,
            last_rebase_step_atr=None,
            distance_delta_atr=None,
            reclaim_active=None,
            reasons=("STABIL:UNAVAILABLE",),
        )

    behavior = stabil.behavior
    reasons = [
        f"STABIL_VALIDITY:{stabil.validity}",
        f"STABIL_DYNAMICS:{stabil.dynamics}",
        f"STABIL_PROGRESSION:{stabil.progression}",
    ]
    if behavior is None:
        reasons.append("STABIL_BEHAVIOR:UNAVAILABLE")
    else:
        reasons.extend(
            (
                f"STABIL_MOTION:{behavior.motion}",
                f"STABIL_RELATION:{behavior.relation}",
                f"STABIL_INTERACTION:{behavior.interaction}",
                f"STABIL_APPROACH_ORIGIN:{behavior.approach_origin}",
            )
        )

    return StabilMarketState(
        data_quality=stabil.data_quality,
        timeframe=stabil.timeframe,
        as_of=stabil.as_of,
        support_ref=stabil.support_ref,
        support_level=stabil.support_level,
        support_floor=stabil.support_floor,
        validity=stabil.validity,
        dynamics=stabil.dynamics,
        progression=stabil.progression,
        distance_pct=stabil.distance_pct,
        distance_atr=stabil.distance_atr,
        bars_above_support=stabil.bars_above_support,
        bars_below_support=stabil.bars_below_support,
        reclaim_count=stabil.reclaim_count,
        events=stabil.events,
        motion=None if behavior is None else behavior.motion,
        relation=None if behavior is None else behavior.relation,
        interaction=None if behavior is None else behavior.interaction,
        approach_origin=None if behavior is None else behavior.approach_origin,
        bars_since_rebase=None if behavior is None else behavior.bars_since_rebase,
        cross_count=None if behavior is None else behavior.cross_count,
        last_rebase_step_atr=None if behavior is None else behavior.last_rebase_step_atr,
        distance_delta_atr=None if behavior is None else behavior.distance_delta_atr,
        reclaim_active=None if behavior is None else behavior.reclaim_active,
        reasons=tuple(reasons),
    )


def _build_horizon_market_state(
    structure: StructuralFactsProjection,
    assessment: StructuralAssessment,
    *,
    volatility: VolatilityEnvironmentProjection | None,
    participation: ParticipationBehaviorProjection | None,
) -> HorizonMarketState:
    structural_map = build_horizon_structural_map(structure, assessment)
    environment_risk, environment_rows, environment_reasons = _environment_state(
        assessment,
        volatility,
    )
    propagation, participation_rows, participation_reasons = _participation_state(
        assessment,
        participation,
    )
    return HorizonMarketState(
        horizon=assessment.horizon,
        structural=assessment,
        structural_map=structural_map,
        environment_risk=environment_risk,
        environment=environment_rows,
        participation_propagation=propagation,
        participation=participation_rows,
        reasons=(
            *assessment.reasons,
            *structural_map.reasons,
            *environment_reasons,
            *participation_reasons,
        ),
    )


def _latest_structure_as_of(structure: StructuralFactsProjection) -> object:
    rows = structure.timeframe_facts
    if not rows:
        raise ValueError("market state as_of is required when Structure has no timeframe facts")
    latest = rows[0].as_of
    if latest is None:
        raise ValueError("market state Structure as_of must be known")
    for row in rows[1:]:
        if row.as_of is None:
            raise ValueError("market state Structure as_of must be known")
        try:
            if row.as_of > latest:
                latest = row.as_of
        except TypeError as exc:
            raise TypeError("market state Structure as_of values must be comparable") from exc
    return latest


def _validate_structure_as_of(structure: StructuralFactsProjection, as_of: object) -> None:
    for row in structure.timeframe_facts:
        if row.as_of is None:
            raise ValueError("market state Structure as_of must be known")
        try:
            is_future = row.as_of > as_of
        except TypeError as exc:
            raise TypeError("market state Structure as_of must be comparable to snapshot as_of") from exc
        if is_future:
            raise ValueError(f"market state cannot contain future Structure state:{row.timeframe}")


def _horizon_source_refs(state: HorizonMarketState) -> tuple[FactRef, ...]:
    refs: list[FactRef] = list(state.structural.source_refs)
    for node in state.structural_map.nodes:
        refs.extend(node.current_external_refs)
    for _, assessment in state.environment:
        refs.extend(assessment.source_refs)
    for _, assessment in state.participation:
        refs.extend(assessment.source_refs)
    return tuple(refs)


def _market_state_source_refs(
    long_term: HorizonMarketState,
    short_term: HorizonMarketState,
    stabil: StabilMarketState,
) -> tuple[FactRef, ...]:
    refs = [*_horizon_source_refs(long_term), *_horizon_source_refs(short_term)]
    if stabil.support_ref is not None:
        refs.append(stabil.support_ref)
    refs.extend(event.ref for event in stabil.events)

    by_key: dict[tuple[str, str, str, str, str], FactRef] = {}
    for ref in refs:
        key = ref.deterministic_key
        existing = by_key.get(key)
        if existing is not None and existing != ref:
            raise ValueError("market state source ref identity collision")
        by_key[key] = ref
    return tuple(by_key[key] for key in sorted(by_key))


def build_market_state(
    structure: StructuralFactsProjection,
    *,
    as_of: object | None = None,
    stabil: StabilSupportProjection | None = None,
    volatility: VolatilityEnvironmentProjection | None = None,
    participation: ParticipationBehaviorProjection | None = None,
) -> MarketStateSnapshot:
    """Build one factual, horizon-separated market-state read model.

    Existing Structure authority, volatility and participation behavior is preserved
    unchanged in this read-only migration. Stabil native support/lifecycle behavior is
    carried alongside those facts without creating a vote, gate or BUY/SELL action.
    Traceable evidence is exposed only through real source refs already present in the
    frozen projections, and every such ref is bounded by the resolved ``as_of``.
    """

    resolved_as_of = _latest_structure_as_of(structure) if as_of is None else as_of
    if resolved_as_of is None:
        raise ValueError("market state as_of must be known")
    _validate_structure_as_of(structure, resolved_as_of)

    structural = build_horizon_structural_snapshot(structure)
    long_term = _build_horizon_market_state(
        structure,
        structural.long_term,
        volatility=volatility,
        participation=participation,
    )
    short_term = _build_horizon_market_state(
        structure,
        structural.short_term,
        volatility=volatility,
        participation=participation,
    )
    stabil_state = _stabil_state(stabil)
    source_refs = _market_state_source_refs(long_term, short_term, stabil_state)
    source_lineage = tuple(
        sorted(
            {
                ref.lineage_id
                for ref in source_refs
                if ref.lineage_id is not None
            }
        )
    )
    return MarketStateSnapshot(
        as_of=resolved_as_of,
        long_term=long_term,
        short_term=short_term,
        horizon_relation=structural.relation,
        stabil=stabil_state,
        source_refs=source_refs,
        source_lineage=source_lineage,
        reasons=structural.reasons,
    )


__all__ = [
    "BridgeState",
    "HorizonMarketState",
    "HorizonStructuralMap",
    "MarketRiskRegime",
    "MarketStateSnapshot",
    "ParticipationPropagationState",
    "StabilMarketState",
    "StructuralRegime",
    "TimeframeAuthorityRole",
    "TimeframeStructuralNode",
    "build_horizon_structural_map",
    "build_market_state",
]
