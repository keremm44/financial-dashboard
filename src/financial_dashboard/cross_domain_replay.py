from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable, Iterable, Sequence

from financial_dashboard.context.builder import CrossDomainBuildResult


@dataclass(frozen=True, slots=True)
class CrossDomainReplayPoint:
    index: int
    as_of: Any
    result: CrossDomainBuildResult
    signature: str


@dataclass(frozen=True, slots=True)
class CrossDomainTransition:
    as_of: Any
    field: str
    previous: str
    current: str


@dataclass(frozen=True, slots=True)
class CrossDomainHistoricalReplay:
    points: tuple[CrossDomainReplayPoint, ...]
    transitions: tuple[CrossDomainTransition, ...]

    @property
    def latest(self) -> CrossDomainBuildResult | None:
        return None if not self.points else self.points[-1].result


_REPLAY_FIELDS = (
    "structural_thesis",
    "continuation",
    "reaction",
    "reaction_direction",
    "reversal",
    "reversal_direction",
    "objective",
    "participation",
    "volatility",
    "pattern_readiness",
    "mtf",
    "ham_readiness",
    "conflict",
)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _zone_identity(zone: Any | None) -> str:
    return "NONE" if zone is None else str(zone.zone_id)


def replay_signature(result: CrossDomainBuildResult) -> str:
    """Return a deterministic semantic signature for one knowledge-bounded result."""

    context = result.context
    axes = context.axes
    permission = result.permission
    parts = [
        str(context.symbol),
        str(context.as_of),
        str(context.anchor_timeframe),
        *(f"{field}={_enum_value(getattr(axes, field))}" for field in _REPLAY_FIELDS),
        f"nearest_support={_zone_identity(context.zones.nearest_qualified_support)}",
        f"nearest_resistance={_zone_identity(context.zones.nearest_qualified_resistance)}",
        f"strongest_support={_zone_identity(context.zones.strongest_relevant_support)}",
        f"strongest_resistance={_zone_identity(context.zones.strongest_relevant_resistance)}",
        f"scope={_enum_value(permission.scope)}",
        f"side={_enum_value(permission.permitted_side)}",
        f"gate={_enum_value(permission.gate_state)}",
        "eligible=" + ",".join(context.knowledge_boundary.eligible_fact_ids),
        "future=" + ",".join(context.knowledge_boundary.excluded_future_fact_ids),
        "unconfirmed=" + ",".join(context.knowledge_boundary.unconfirmed_fact_ids),
        "unsupported=" + ",".join(context.knowledge_boundary.unsupported_contexts),
    ]
    return sha256("\n".join(parts).encode("utf-8")).hexdigest()


def validate_replay_result(result: CrossDomainBuildResult, *, expected_as_of: Any | None = None) -> None:
    """Fail closed on causality or action-authority violations."""

    context = result.context
    if expected_as_of is not None and context.as_of != expected_as_of:
        raise ValueError("cross-domain replay result as_of does not match requested replay point")
    if context.knowledge_boundary.as_of != context.as_of:
        raise ValueError("knowledge boundary as_of must match context as_of")
    for ref in context.source_refs:
        if not ref.is_available_at(context.as_of):
            raise ValueError("cross-domain replay contains source fact unavailable at as_of")
    if result.permission.is_actionable_signal:
        raise ValueError("permission envelope must never become an actionable signal")


def _transition_fields(result: CrossDomainBuildResult) -> dict[str, str]:
    axes = result.context.axes
    permission = result.permission
    fields = {field: _enum_value(getattr(axes, field)) for field in _REPLAY_FIELDS}
    fields.update(
        {
            "nearest_support": _zone_identity(result.context.zones.nearest_qualified_support),
            "nearest_resistance": _zone_identity(result.context.zones.nearest_qualified_resistance),
            "permission_scope": _enum_value(permission.scope),
            "permitted_side": _enum_value(permission.permitted_side),
            "gate_state": _enum_value(permission.gate_state),
        }
    )
    return fields


def _build_transitions(points: Sequence[CrossDomainReplayPoint]) -> tuple[CrossDomainTransition, ...]:
    if len(points) < 2:
        return ()
    out: list[CrossDomainTransition] = []
    previous = _transition_fields(points[0].result)
    for point in points[1:]:
        current = _transition_fields(point.result)
        for field in sorted(current):
            if previous[field] == current[field]:
                continue
            out.append(
                CrossDomainTransition(
                    as_of=point.as_of,
                    field=field,
                    previous=previous[field],
                    current=current[field],
                )
            )
        previous = current
    return tuple(out)


def build_cross_domain_historical_replay(
    replay_points: Iterable[Any],
    *,
    build_at: Callable[[Any], CrossDomainBuildResult],
) -> CrossDomainHistoricalReplay:
    """Build deterministic history from causal prefix builders.

    `build_at` is intentionally injected. The replay boundary never reruns or owns
    native engines; callers supply a prefix-safe builder backed by existing replay
    infrastructure.
    """

    points: list[CrossDomainReplayPoint] = []
    previous_as_of: Any | None = None
    for index, as_of in enumerate(replay_points):
        if previous_as_of is not None and as_of <= previous_as_of:
            raise ValueError("cross-domain replay points must be strictly increasing")
        result = build_at(as_of)
        validate_replay_result(result, expected_as_of=as_of)
        points.append(
            CrossDomainReplayPoint(
                index=index,
                as_of=as_of,
                result=result,
                signature=replay_signature(result),
            )
        )
        previous_as_of = as_of
    frozen = tuple(points)
    return CrossDomainHistoricalReplay(points=frozen, transitions=_build_transitions(frozen))


def assert_prefix_stable(
    prefix: CrossDomainHistoricalReplay,
    extended: CrossDomainHistoricalReplay,
) -> None:
    """Verify future replay points do not rewrite already-emitted history."""

    if len(prefix.points) > len(extended.points):
        raise AssertionError("prefix replay cannot be longer than extended replay")
    for left, right in zip(prefix.points, extended.points, strict=False):
        if left.as_of != right.as_of or left.signature != right.signature:
            raise AssertionError(f"cross-domain prefix instability detected at {left.as_of}")


__all__ = [
    "CrossDomainHistoricalReplay",
    "CrossDomainReplayPoint",
    "CrossDomainTransition",
    "assert_prefix_stable",
    "build_cross_domain_historical_replay",
    "replay_signature",
    "validate_replay_result",
]
